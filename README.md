# NYC Transit Live

A live map of NYC subways, buses, commuter rail (LIRR + Metro-North), PATH,
and AirTrain JFK, built on the MTA's public real-time feeds plus the PANYNJ
static data and a community PATH bridge feed.
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
Clicking a bus, a subway train, a railroad train, or a ferry boat shows the alerts
for that vehicle's route the same way, and agency-wide alerts (which name no route
and no stop) appear in a dismissible banner over the map rather than in any one
popup.
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

PATH (Port Authority Trans-Hudson) is on the map as its own toggleable layer:
route polylines in each route's own color, clickable station dots with live
arrival popups, and trains that glide along the route geometry between
stations once the backend has observed an advance (a train not yet observed
moving sits placed at the station it is approaching). The
backend downloads and caches PATH's static GTFS in its own warmup group and
serves the 13 parent-station markers from `GET /api/path-stops`
(`[{id, name, lat, lon}]`) and the seven routes with their rider-facing names,
colors, and modal route geometry from `GET /api/path-routes`
(`[{id, name, color, text_color, shape}]`). Realtime trains come from a
community bridge feed (PATH publishes no official GTFS-RT feed):

- `GET /api/path`: `{fetched_at, feed_timestamp, trains}`, every train
  schedule-placed at its next station (the bridge carries no vehicle
  positions). Each train carries a stable synthetic `id` minted by the
  backend's identity matcher, and `prev_*` glide anchors populated after an
  observed advance to the next station (the same contract the subway v2
  payload feeds the glide); the bridge's own trip hash never reaches the
  payload.
- `GET /api/path-arrivals/{stop_id}`: `{fetched_at, stop_id, stop_name,
  directions}` with buckets `To New York` / `To New Jersey` plus a residual
  `Trains` bucket, only the non-empty ones (`{}` means nothing upcoming).

Two PATH-specific caveats. Bridge trip ids are UNSTABLE across upstream
refreshes, so nothing may key on them: the backend synthesizes cross-poll
identity instead, matching each generation on stable fields (same stop and
route/direction with a nearby arrival prediction, or a unique advance to the
next station in the static stop order) and resetting identity rather than
guessing when a match is ambiguous. The frontend keys its PATH markers on
those stable ids (the same diffing the other systems use), so markers and
open popups survive polls, and anchored trains glide between stations along
the drawn polylines under PATH's own slice tolerances; trip hashes are never
displayed. And PATH publishes no service alerts feed, so PATH is the one
system on the map whose popups carry no alerts block. PATH data is courtesy of PANYNJ, published via Trillium, and
subject to their license terms. PATH stop ids stay in their own namespace:
they are numeric and collide with MTA numeric ids across systems.

NYC Ferry serves both a static foundation and live boats. The backend downloads
and caches the ferry static GTFS in its own warmup group (modeled on the PATH
group) and polls the two realtime endpoints each cycle. The endpoints:

- `GET /api/ferry-stops`: `[{id, name, lat, lon, wheelchair}]`, one entry per
  landing. `wheelchair` reflects the GTFS `wheelchair_boarding` flag (true only
  when the feed marks the landing accessible).
- `GET /api/ferry-routes`: `[{id, name, color, text_color, shape}]`, the nine
  routes with their rider-facing names, colors verbatim from the feed, and the
  modal route geometry (same modal-shape-per-direction selection PATH uses).
- `GET /api/ferry`: `{fetched_at, feed_timestamp, boats}`, the live boats from
  the VehiclePositions feed. Each boat carries its real GPS position, hull label,
  trip_id, route_id, raw speed, and `status` (STOPPED_AT when docked, otherwise
  under way). Both realtime feeds carry an empty route_id, so a boat's route is
  recovered by joining its trip_id through the static trip-to-route map; a boat
  whose trip_id does not join keeps its position with a null route rather than
  being dropped, and a deadheading boat (empty trip_id) is dropped. `bearing` is
  omitted because the feed only ever reports 0.0.
- `GET /api/ferry-arrivals/{stop_id}`: `{fetched_at, stop_id, stop_name, routes}`,
  upcoming boats at a dock grouped by route name (the feed has no direction_id).
  Each row carries the arrival and departure times (docks report both as a dwell).

The map draws ferries as its own toggleable layer: route polylines, clickable
docks with live arrival popups, and moving GPS boat markers (a boat-hull shape,
distinct from every rail marker, that dims when the boat is STOPPED_AT a dock and
stays bright when under way). Boat popups show the hull label, route, and status
in plain words; a dock popup buckets its upcoming boats by route, counting down
to arrival, or to departure when a boat is dwelling at the dock, and surfaces the
dock's wheelchair-accessibility flag (the first accessibility display in the
app). Boats are keyed on their stable vehicle id and moved to their reported
position each poll (no schedule interpolation, unlike the subway/PATH glide).
Ferry service alerts are wired into the same `/api/alerts` pipeline as the MTA
systems: a dock popup prepends the union of alerts that name that dock's stop and
alerts that name any route serving that dock, and a boat popup prepends alerts that
name that boat's route (an agency-wide ferry alert joins the shared banner). The
"any route serving that dock" join comes from the routes-per-station index (H5),
derived from `stop_times` for every system, so a route-scoped alert reaches a
station even when no train or boat of that route is imminent there. A ferry
alert-feed failure marks only the ferry system degraded in `/api/status` (per-system
retention), it never breaks the poll. Boat speed is shown in knots for a boat that is
under way, converted client-side from the feed's meters-per-second reading (H4).

Ferries stop running overnight, so the realtime feeds return empty then; an empty
successful poll correctly clears the boats (they went home), while a failed poll
keeps the last-known set, the standard success-replaces / failure-retains split.
The frontend preserves that split rather than undoing it: an empty ferry poll
clears the boat markers immediately (a backend empty means the boats are gone,
because a transient problem is a failed poll instead), unlike the other feeds'
brief keep-last-known grace for a flickering upstream.

Ferry stop ids are short numerics that collide with MTA and PATH ids, so ferry
data stays in its own namespace. The static feed comes from NYC Ferry's
Connexionz endpoint, published on their Developer Tools page
(https://www.ferry.nyc/developer-tools/) and used under its Developer Terms:
NYC Ferry grants a non-exclusive, revocable right to integrate the GTFS into
sites and applications, and retains all rights and title in the data
(confirmed 2026-07-09). Attribution here is courtesy, not a stated
requirement. NYC Ferry's trademarks and logos stay reserved, so the map uses
its own markers; the route colors are fine because they are data in
routes.txt, not branding.

Service alerts are polled on their own slower loop and served from an in-memory
index (the map surfaces are the popup blocks and the agency-wide banner
described above):

- `GET /api/alerts`: `{fetched_at, alerts: [...]}`, one entry per alert active now
  across the keyless subway/bus/LIRR/MNR and NYC Ferry alert feeds: `{id, system, header,
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
│   ├── path_static.py       # download/parse the PATH static GTFS (PANYNJ via Trillium)
│   ├── ferry_static.py      # download/parse the NYC Ferry static GTFS (via Connexionz)
│   ├── static_shared.py     # stage/validate/promote pipeline shared by the four static loaders
│   ├── scripts/             # one-off generators (gen_airtrain_fixture.py, gen_path_fixture.py, gen_ferry_fixture.py)
│   ├── tests/               # pytest suite (run from backend/)
│   ├── requirements.txt     # lower-bound deps for local dev
│   ├── requirements.lock    # pinned deps installed by Railway and CI
│   └── requirements-dev.txt # the lock + test-only extras
├── frontend/
│   ├── index.html
│   ├── map.js               # Leaflet map, polls backend, draws markers
│   ├── stations.js          # the accessible station panel (search + arrivals as text)
│   ├── helpers.js           # pure helpers shared with map.js (node-testable)
│   ├── helpers.test.js      # node --test suite for the helpers
│   ├── style.css
│   └── vendor/leaflet/      # self-hosted Leaflet 1.9.4 (js, css, images, LICENSE)
├── tests/e2e/               # hermetic Playwright smoke suite (dev/test only)
│   ├── smoke.spec.js        # the scenarios; all network intercepted
│   ├── stations.spec.js     # the station panel, including a keyboard-only walk
│   ├── a11y.spec.js         # page-wide axe scan + the keyboard invariants
│   ├── mock.js              # /api/* fixtures + basemap-tile stub
│   ├── serve.js             # tiny static server for frontend/ (no backend)
│   ├── playwright.config.js # chromium only, starts the static server
│   └── fixtures/            # handcrafted JSON payloads
├── tests/statement.test.js  # ACCESSIBILITY.md cites real tests, checked
├── docs/reviews/            # adversarial-review adjudication records, one per phase
├── data/
│   ├── airtrain_jfk.json    # committed AirTrain JFK fixture (geometry + scheduled headways)
│   ├── gtfs_static/         # downloaded static subway GTFS (gitignored)
│   └── cache/bus_routes/    # background-built bus route index (gitignored)
├── ACCESSIBILITY.md         # what is measured, what is excepted, what is unchecked
├── .github/workflows/ci.yml # backend pytest + frontend node tests + e2e smoke
├── package.json             # dev-only: @playwright/test + @axe-core/playwright (the app is buildless)
├── railway.json             # Railway start command + healthcheck
├── nixpacks.toml            # pins Python 3.12 for the Railway build
├── requirements.txt         # root pointer -> backend/requirements.lock
└── .env                     # BUS_TIME_API_KEY (gitignored)
```

## Accessibility

A live map is a picture, and a picture is not arrival information. The station
panel is the same data as text: press **Stations** (or the skip link, which is the
first thing keyboard focus lands on) to search the stations each loader registers
as it draws them, across all six rail and ferry systems (Subway, LIRR, Metro-North,
PATH, Ferry, AirTrain), pick one, and read its next arrivals as sentences, grouped the way the popups
group them (by direction, or by route for ferries), each naming the route and
spelling the countdown out in words. AirTrain says plainly
that its numbers are scheduled headways rather than live tracking, because it
publishes no realtime feed. A stale feed says how old it is here in the same
wording the popups use. On a screen 1100px or wider the panel is docked open;
narrower, it opens over the map and starts closed. Selecting a station also pans
the map and opens that station's popup, so a sighted rider and a screen-reader
rider are looking at the same place. Arrivals refresh in the background, and the
live region announces only when the trains themselves change, never on a
countdown tick, so it does not talk over you.

**The map itself.** Every vehicle marker now carries a name, so a screen reader on a
touch device announces "1 train, next stop Times Sq-42 St, Northbound" instead of an
unlabeled button. Those markers are deliberately **not** in the keyboard tab order:
there can be several hundred of them, and tabbing through every bus in Brooklyn to
reach a control is not a keyboard path anyone wants. The keyboard path is the Stations
panel, one Tab away via the skip link. The map container itself stays focusable, so
Leaflet's arrow-key panning still works. Where a vehicle is parked on top of a station,
its popup carries an "Also here" link to that station's arrivals, so the station stays
reachable even when the marker covers it.

**Motion.** If your system asks for reduced motion, the map stops animating: vehicles
jump to each new position when the data arrives instead of sliding there, and the
marker and panel transitions are off. Nothing is hidden and no data is held back, since
this changes only how a position updates, never what is shown. One limitation worth
knowing: the map library reads its own zoom and pan animation settings once when the
page loads, so if you change the setting while the page is open, everything else
responds immediately but those two take effect the next time you load the page.

**Keeping your place.** This page rewrites itself under you every fifteen seconds, and
a keyboard rider parked on a control has to survive that. When a popup you are reading
refreshes, or the alert banner is rebuilt because the MTA reworded an incident, the
control you were on gets focus back rather than dropping you at the top of the document.
When the thing you were holding is genuinely gone, because the vehicle left the feed or
the last alert cleared, focus moves to the map and the page says so once: "The 1 train
you were following left the feed. Focus moved to the map."

**Escape** closes the topmost thing you are in, one surface per press: the popup first
if you are in a popup, the panel first if you are in the panel. On a phone the station
panel opens over the map and the page behind it goes `inert`, so nothing behind it is
reachable, and Tab still runs off the end of the panel rather than looping. No keyboard
trap has been found in any state the tests walk; "no trap anywhere" is a stronger claim
than three walked states support, and ACCESSIBILITY.md says which three.

**What is measured, and what is not.** CI enforces a page-wide axe-core scan at two
widths across six states, plus keyboard invariants driven by a real Tab walk
(`tests/e2e/a11y.spec.js`). The scan's five remaining undecidables each have a test
that answers them by measurement instead. What is honestly *not* covered: no screen
reader and no disabled rider has ever tested this page, it is checked in one browser
engine, the map itself is still a picture, and buses are not in the panel because their
stops are not stations. The full statement, with the test that proves every claim and
the complete list of what is unchecked, is in
[ACCESSIBILITY.md](ACCESSIBILITY.md).

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

The backend serves the frontend document and its static assets with a strict set
of security headers: a `Content-Security-Policy` that keeps scripts, styles, and
connections same-origin (allowing only the OSM basemap tile origin as an image
source), plus `X-Content-Type-Options: nosniff`, `Referrer-Policy:
strict-origin-when-cross-origin`, and a `Permissions-Policy` disabling geolocation,
camera, and microphone. The strict CSP is feasible because the frontend has no build
step and no CDN dependency (Leaflet is self-hosted). `style-src` allows
`'unsafe-inline'` because the popup and marker HTML use inline `style` attributes for
route colors; `script-src` stays strict. There is no HSTS (Railway terminates TLS),
and the JSON APIs and `/docs` are exempt. The Playwright e2e mirrors these headers so
a CSP that would break the app fails the suite.

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
fixtures in `tests/e2e/fixtures/`; Leaflet is self-hosted under
`frontend/vendor/leaflet/` and served by that static server exactly as production
serves it (there is no CDN URL left to intercept), and the basemap tiles are
stubbed. Nothing leaves the machine, so CI needs no network at test time. Time is frozen with Playwright's clock control, so the
arrival countdowns and the staleness window are deterministic (no sleeps).

#### Writing a spec: assert the state before asserting about it

If a test's title says "with X open", "from inside Y" or "while Z is showing",
call `expectState` from `tests/e2e/state.js` for that state **before** its own
assertions:

```js
const { expectState } = require("./state");
await expectState(page, ["one popup open", "focus inside the popup"], "A9j");
```

This is not ceremony. Four specs across three adversarial rounds claimed a state
they never reached and passed anyway: a spec titled "closing a popup the rider
was not in" that never closed a popup, one titled "the popup that owns the
button" that only ever had one popup live, a keyboard walk that required station
rows to be reachable in a state with no rows on screen, and an axe state named
"popup with a cross-link" that scanned a popup with no cross-link. Every one of
them was green. Reaching a state and asserting about a state are different acts,
and only the second was ever being written down.

Each witness is the smallest fact that is true in its state and false outside it,
and it fails with a sentence naming what is absent rather than with a confusing
mismatch twenty lines later. If the witness you need is not in `state.js`, add it
there rather than inline: a witness written inline is one the next spec cannot
reuse and the next reviewer cannot audit. The header of that file lists the four
failures that paid for it.

The sibling convention is `expectPopupState` in `tests/e2e/popup.js`, for the
narrower question of whether a specific marker's popup is open. Leaflet leaves a
closed popup in the DOM while it fades and never clears `map._popup`, so two of
the three obvious ways to ask that question answer wrongly. Ask through the
helper. In app code the same question is answered by `openPopupsOnMap()`, a
register the map keeps itself; `map._popup` means "most recently opened", which
is not the same thing and is never cleared on close.

## Data sources

- **Buses** — MTA Bus Time `VehiclePositions` feed (requires key). Real lat/lon.
- **Subways** — MTA keyless GTFS-RT feeds, grouped by line (ACE, BDFM, numbered
  lines, etc.). These carry trip/arrival updates, not GPS, so trains are shown
  at their next station.
- **Commuter rail** — MTA keyless GTFS-RT feeds for the LIRR and Metro-North.
  These do report real GPS, so trains with a vehicle position render at their
  true lat/lon; trains without one are placed at their next station from the
  trip updates and glide between stations (hollow markers, so the two are
  visually distinct).
- **PATH**: the community GTFS-RT bridge feed (jamespfennell's
  path-train-gtfs-realtime, sourced from the PANYNJ API; no official feed
  exists), decoded into placed trains with backend-synthesized identity, plus
  PANYNJ static GTFS via Trillium for stations, geometry, and the station
  order. Subject to PANYNJ license terms.
- **NJ Transit**: the RailData GTFS for NJ Transit Rail, behind a free account.
  Static stations and schedule only in 15a; realtime follows. **The only
  credentialed upstream here, and the only one that is not a GET.** Leaving
  `NJT_USERNAME` / `NJT_PASSWORD` unset is a supported configuration: the layer
  reports `not-configured` and makes no network call at all.
- **Static GTFS** — stop coordinates and route shapes, downloaded into
  `data/gtfs_static/` and loaded into memory by background warmup tasks (subway,
  railroad, PATH, ferry, and NJ Transit, each an independent group), off the startup
  critical path. A group's load retries automatically on failure, on a backoff schedule that
  starts at 15s and settles at 5 minutes, so a degraded network at boot self-heals
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

### NJ Transit (credentialed, and unlike every other upstream here)

Register for free RailData API access at the NJ Transit developer portal, then put
the account's own username and password in `.env` as `NJT_USERNAME` and
`NJT_PASSWORD`. There is no long-lived API key: the app exchanges those for a
short-lived token at runtime.

Three facts from the 2026-08-05 probes shape everything in `backend/njt_auth.py`,
and each is a way a conventional poller gets this wrong:

1. **Every endpoint is `POST multipart/form-data`, and the token rides as a form
   field**, not an `Authorization` header and not a query parameter. A GET returns
   nothing usable.
2. **Minting is rate-limited below the data cap, and the number is unpublished.**
   Tokens are product-scoped, so a GTFSRT token is rejected by the Usage API and we
   cannot read our own counters either. Mints are therefore treated as a scarce,
   unmeasurable resource: a single-flight cache turns concurrent callers into one
   mint, and a rejected token buys exactly one re-mint per attempt.
3. **An expired token is `HTTP 500` with `{"errorMessage":"Invalid token."}`, not
   401 or 403.** This is the dangerous one. A poller that classifies 500 as a server
   error backs off forever while the fix is a single re-mint; one that treats *all*
   500s as auth failures spends a mint on every real NJ Transit outage.
   `njt_auth.is_auth_error` matches that exact shape and nothing else, and the
   contract tier carries a same-class control (a genuine 500 with a different body)
   that must neither mint nor heal.

The feed itself is unusual too, and the validators are built around it rather than
around the GTFS norm: there is **no `calendar.txt` and no `feed_info.txt`**, so
service is 8,697 additive `calendar_dates` rows and staleness is answered by a
**service-date guard** (the latest scheduled service day must be today or later, in
New York local time) instead of a `feed_end_date`. The loader draws that hard line;
the contract monitor WARNs while the remaining runway is under 30 days. All 12
routes carry `route_type=113`, the GTFS *extended* Rail Service type that anything
switch-casing the classic 0-7 falls through. `shapes.txt` is present and
deliberately unparsed: it is 10 MB of the 11.1 MB payload and nothing reads it yet.

To regenerate the committed test fixture (needs credentials; nothing in CI touches
the live API):

```bash
python backend/scripts/gen_njt_fixture.py   # mints once, downloads, trims, verifies
```

It fails loudly on any drift from the probed facts. Eyeball the printed tables
before committing, per house rules.

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

### Upstream and timing overrides

Every upstream endpoint, five cadence constants, and the on-disk data root can be
overridden by an environment variable, and each override defaults to the literal
it replaced, so setting nothing changes nothing. `SEAM_NAMES` in
`backend/env_seams.py` is the complete list, and `backend/tests/test_env_seams.py`
pins every default by value.

Deliberately NOT overridable, so the list is not mistaken for "every constant":
the per-refresh and per-attempt deadlines, the backend's own staleness threshold,
the alert retention cap and feed deadline, the download deadline, and the static
archives' max age. None is needed by a contract scenario, and an unused knob is
still a supported surface.

Two of these are ordinary operational levers that predate the rest (`PATH_RT_URL`,
`FERRY_RT_BASE`): pointing a feed at a mirror is a legitimate thing to want. The
rest exist so the C6 contract tier can run the real backend process against a
controlled upstream simulator with every cadence compressed, which is the only way
to test the composite the three layers form rather than each layer alone.

### Static archives cannot destroy their own last-known-good

Every static GTFS download stages, validates, then promotes. The download lands
in a temp file beside the cached archive, that staged file is validated with the
loader's own parsers, and only a passing archive is renamed over the cache. A bad
upstream publication (a truncated zip, an HTML error page saved as `.zip`, a
`stops.txt` with headers and no rows, a damaged deflate stream) is deleted at the
stage file and the cached archive keeps serving, byte-untouched.

Each loader carries two validators, and the asymmetry is the point. A **new
publication** is checked with the full parse of every table the load reads,
because promoting is irreversible: the archive it replaces is gone. A **cached
archive** is checked with a lighter parse at load time, because that runs on every
load and there is nothing behind it to protect anyway. A cached archive that fails
its check is treated as absent, which forces a fresh staged download rather than
serving garbage.

That makes one state deliberate and worth recognizing: a group can be `ready`
while its archive is older than the 30-day refresh threshold. It is reachable
ONLY by a download that was attempted and failed validation, never by skipping a
download, and `static_archives` in `/api/status` is where the reason shows. The
converse is equally deliberate: with no valid cached archive, a failing download
is `failed`-and-retrying and never `ready`. Ready always means "serving
validated data".

## Monitoring

`GET /api/status` returns an always-200 operational snapshot: per-feed cache
freshness — both `age_s` (since this server last polled) and `feed_age_s` (how
stale the feed's own content was at poll time) — the last recorded poll error
if any, the bus route index state, the static subway GTFS age, and each static
group's warmup state (`subway_static` / `railroad_static` / `path_static` /
`ferry_static` / `njt_static`: loading, ready, or failed-and-retrying). `njt_static`
has a **fourth** state, `not-configured`, which is what a deployment without NJ
Transit credentials reports: no credentials means no network attempt of any kind, so
nothing is failing and nothing is retrying, and an operator reading `failed` would
otherwise go looking for a broken upstream that does not exist. Beside those,
`static_archives` reports each downloaded ARCHIVE (`subway`, `railroad_LIRR`,
`railroad_MNR`, `path`, `ferry`, `njt`): when a download last passed validation and was
promoted, why the last one was rejected, and how many have been rejected since.
A group state answers "can I serve this system"; these answer "how old is the
archive I am serving it from, and why", which together make the deliberate
ready-but-stale state legible. The `alerts` entry reports the alert poll's `age_s`, its
last error if any, the `active` alert count in the index, and `suppressed_planned`
(not-yet-active planned work the last poll held back), so upcoming service work is
visible even though it is excluded from `/api/alerts`. It also carries per-system
health under `systems` (each alert feed's last-decode time, whether its alerts are
currently `retained` from a down feed, and any current error) plus a
`degraded_systems` list: one of the alert feeds going down is a successful
poll overall, so its alerts are carried forward (bounded by an activity re-filter
and a retention cap) rather than silently deleted, and this is where that partial
outage shows.

`GET /healthz` is the readiness probe (Railway's healthcheck points here). It
returns 503 when the app can't serve fresh data: no feed is fresh, the bus route
index build has failed, or the subway static load has failed (and is retrying).
It stays healthy as long as **at least one** feed is fresh, so a misconfigured
key (which only stops the bus feed) doesn't take down an otherwise-working subway
map. A still-**loading** static group or bus index during cold start does not
flap it (the load runs in the background, off the healthcheck critical path);
only the failed states, which retry, degrade it until a retry succeeds. Railroad
static failure is deliberately lenient (a system that fails degrades to GPS-only)
rather than a healthz reason. That leniency is per system, not absolute: a load
where *every* railroad system came back empty is treated as a failed attempt and
retried, because a total failure marked ready would never be retried at all.

**Deployment invariant: the first retry rungs must fit well inside the healthcheck
window.** A failed static warmup retries on a backoff schedule
(`STATIC_RETRY_SCHEDULE_S` in `backend/main.py`, currently 15s, 30s, 60s, then 300s
steady, each with ±10% jitter), while `railway.json` sets `healthcheckTimeout` to
300s. The schedule as a whole is unbounded on purpose (it keeps retrying a genuinely
down upstream forever, at the 300s steady rung); what has to fit in the window is the
*early* part, so that a transient first-attempt failure gets more attempts before the
deploy is judged. Concretely, the first two retries are scheduled 15s and then 30s
after the attempt before them fails, so with attempts that fail fast (a refused
connection, a DNS miss, the cold-start case these rungs exist for) both land inside
300s. The retry interval used to be a flat 300s, which matched the window exactly and
so gave a cold-start blip no real second chance at all. Note the *sleeps* are what
this schedule governs: a slow attempt can still consume the window on its own, which
is `STATIC_ATTEMPT_DEADLINE_S`'s job to bound, not this one's.

The coupling runs both ways: lengthening the early rungs requires raising
`healthcheckTimeout`, and lowering `healthcheckTimeout` requires shortening them.
`backend/tests/test_api.py::test_static_warmup_retries_land_inside_the_healthcheck_window`
reads the timeout straight out of `railway.json` and fails on either mistake.
`railway.json` is JSON and cannot carry a comment, so this is the note of record.
(Distinct from `STATIC_ATTEMPT_DEADLINE_S`, which bounds how long a single attempt may
run rather than how long to wait between attempts.)

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

#### The strict parse boundary

Every realtime decoder in the app goes through one parser, `feeds.parse_feed`. It exists
because `FeedMessage.ParseFromString(b"")` SUCCEEDS: it returns an uninitialized
message with no header and zero entities and raises nothing, so an HTTP 200
carrying an empty body used to decode as a healthy feed that happened to be quiet.
That cleared the standing error and replaced live data with an empty generation, in
every decoder, making a silent upstream failure indistinguishable from a real lull.

`parse_feed` rejects an empty body, a malformed one, a body truncated mid-message,
and one that parses without a feed header, raising `FeedDecodeError` (a subclass of
protobuf's `DecodeError`, so every caller's existing routing carries it). It does
NOT reject a feed that is validly empty: a header with zero entities is real data,
and what that means stays each decoder's business. The ferry feed genuinely empties
overnight, and that empty still replaces its boats, while an empty BODY is now a
failed poll that keeps last-known.

Failures route at each source's own granularity: one poisoned subway group, alert
feed or railroad system degrades only itself (and surfaces through the per-system
block below), while the single-feed sources record a failed poll and keep last-known.

Per-ENTITY junk is deliberately not this boundary's business: a feed whose header is
valid but which carries one unusable entity is still served, and the decoders skip
that entity as they always have. The parser judges the body.

KNOWN GAP: the contract monitor has its own parse for the checks that only count
entities, so the bus and ferry realtime checks (which invoke no production decoder)
still read an empty 200 as a healthy quiet feed. The subway, railroad, PATH and
alerts checks run the production decoders and so inherit the strict parse.

#### Per-system freshness

An envelope's own `fetched_at` means "this poll ran". It says nothing about any
one upstream system, because a partial failure (one subway feed group down, four
of five alert feeds decoding) is still a SUCCESSFUL poll that advances it. The
aggregate endpoints therefore also carry a `systems` block, one entry per
subsystem (`/api/subways`: the 8 feed-group keys, `/api/railroads`: LIRR and MNR,
`/api/alerts`: the 5 alert systems), each reporting that system's own last decode,
whether its last poll succeeded, and since when its data has been carried forward.
The two timestamps diverge exactly when something is wrong, and that divergence is
the signal. The subway blocks also list the routes each system's served data
covers, because a subway train names no feed group and the client needs the join
to know which markers a stale block describes.

A failed system's data is carried forward for up to `FEED_RETENTION_MAX_S` (600s)
rather than vanishing, and the client renders it as what it is: dimmed markers, an
"as of Xm ago" line in the popup, a status line that names the degraded system
("railroad: MNR as of 6m ago" while LIRR stays quiet), and a glide that FREEZES
instead of dead-reckoning a marker forward on a dead feed. Past the cap the data
goes and only the block remains, still reporting the outage, so the disappearance
stays explained. Retention and that rendering are deliberately coupled: see
`FEED_RETENTION_ENABLED` in `backend/cache.py` for why the flag must never move
without them.

The single-feed sources (buses, PATH, ferry) carry no `systems` block. The client
synthesizes a one-system block from their envelope `fetched_at`, so they go
through the same staleness, dimming and freeze rules rather than being exempt for
having one feed.

### Live upstream contract monitor

The test suite is hermetic: golden fixtures pin every decoder against captured
bytes, so a green pipeline proves the code still parses yesterday's data but says
nothing about whether the live feeds still look like those captures. A moved feed
URL, an upstream schema change, a dead community bridge, or an expired static feed
would all pass CI and only surface as a broken map in production.

`backend/scripts/contract_monitor.py` closes that gap. On a schedule
(`.github/workflows/contract-monitor.yml`, every 6 hours plus manual dispatch) it
fetches every upstream source and the production `/api/status`, and decodes each
with the **same** production functions the app runs (`feeds._decode_feed`,
`_decode_railroad_feed`, `_decode_path_feed`, `_decode_alerts`, the
`path_static` / `ferry_static` / `railroad_static` / `static_data` parsers), so a
pass means the real code paths still work against today's data. Each check reports
`PASS`, `WARN`, or `FAIL`: `FAIL` means a human should look today, `WARN` is
notable but expected in some conditions. Judgements are banded, not exact, and
know which emptiness is normal (railroads run thin overnight, the ferry is closed
at night, zero active alerts is good news), so the monitor does not flap. It fetches
each source once with a single retry before declaring a failure, and sends the
community-hosted PATH bridge and NYC Ferry feeds the app's courteous User-Agent.

The workflow is separate from CI and is triggered only by its schedule and manual
dispatch, never on push or pull request, so it can never gate a merge. The run
status is the alert: a `FAIL` exits non-zero, which fails the run and triggers
GitHub's scheduled-failure notifications to the repo admins; `WARN`s surface in
the logs and the job summary without failing the run.

Config:

- `MONITOR_STATUS_URL` (a repository **variable**) is **required**. Set it under
  Settings -> Secrets and variables -> Actions -> Variables. Either form works: the
  deployment's base URL (`https://your-app.up.railway.app`) or the full status URL
  (`https://your-app.up.railway.app/api/status`), with or without a trailing slash.
  Leaving it unset is a `FAIL`, not a skip: an unmonitored deployment must not be
  able to look the same as a healthy one.
- `MONITOR_SKIP_PRODUCTION` (a repository **variable**, any non-empty value) is the
  deliberate way to run without a deployment, for a fork or a local
  `workflow_dispatch`. It yields a `WARN` that says the skip was explicit. The
  principle: silence must be chosen, never defaulted.
- `MTA_BUS_API_KEY` (a repository **secret**): when set, the monitor also checks
  the keyed bus feed. The key rides as a query parameter and every error string is
  scrubbed, so it never reaches the logs. When unset, the bus check is skipped
  with a `WARN`.
- `NJT_USERNAME` / `NJT_PASSWORD` (**environment** secrets, on an environment named
  `monitor`): when set, the monitor also checks NJ Transit's credentialed RailData
  static feed. **Environment secrets, not repository secrets**, which means the job
  must declare `environment: monitor` for them to resolve at all; the declaration is
  in `contract-monitor.yml` with the reasoning beside it. The environment is
  deployment-branch-restricted to `main`, so a context with no access to it gets no
  credentials and the `njt-static` check WARN-skips naming the two missing variables.
  That degradation is the design, not an accident: a fork or a pull request context
  reports NJT as skipped rather than failing or silently checking nothing. The
  scheduled runs always fire on the default branch, so they are always on the
  permitted side of that restriction.

  When they *are* set the monitor **mints exactly one token per run** (four a day at
  the 6-hourly cadence). NJ Transit's mint rate limit sits below its data cap and the
  number is unpublished, so `check_njt_static` mints once with no retry and reuses
  that token for the archive fetch, rather than leaving conservation to the shared
  retry helper.

The production section's bands are deliberately two-tiered so the red light stays
meaningful. A static group in any state but `ready` is a `FAIL`, because a mode
that is not ready is dark for riders. A feed's poll age is a `PASS` under 10
minutes, a `WARN` from 10 to 30 (upstream blips at that scale recover on their
own), and a `FAIL` past 30.

Two things are a `FAIL` immediately rather than aging into a threshold, because
they are deploy regressions rather than upstream moods: no feeds reported at all,
and an age that is present but unusable (a non-number, or a negative value, which
means the deployment's clock stepped). A feed reporting a **null** age has simply
never had a successful poll, which is not the same thing and is only a `WARN`: a
deployment with no bus API key serves `buses.age_s = null` forever by design, and
failing on it would paint a healthy map red on every run. When **every** feed is
null, though, nothing has ever polled, the cache never populated, and that is the
broken startup, so it fails.

A degraded alert system stays a `WARN` while the backend is still carrying its
alerts forward, and becomes a `FAIL` once that retention horizon has passed and
riders are genuinely seeing nothing for it. The alerts poll's own age is checked
first and on the same band. That ordering originally existed because a total
outage froze the per-system health map at its last healthy values, so the
per-system data alone would have read green; the backend no longer freezes it, and
`degraded_systems` is truthful during a total outage. The ordering stays for two
reasons that outlive the fix: the poll age dates every per-system field below it,
and it is the one signal that still moves when the poller has stopped running
altogether, which is a shape the per-system fields cannot show. A never-polled
index is a `WARN` (a warming deployment), except when every system is
simultaneously degraded, which means the deployment has never once reached an
alert feed and cannot heal on its own: that is a `FAIL`.

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
  (R3 narrowed that leniency: a railroad load where *every* system came back empty
  is a failed attempt the backend retries, so the payload the frontend sees stays
  empty and its own retry loop keeps asking until the backend heals.)
  Live-data polling already self-healed and is untouched.
- [x] **13a. PATH (static foundation)**: the PATH static GTFS (stops, routes,
  shapes, trips) is downloaded, cached, and served from its own warmup group via
  `/api/path-stops` (13 parent-station markers) and `/api/path-routes` (route
  names, colors, and modal route geometry). Static only: realtime PATH trains
  come in a later phase via a community bridge feed (whose trip ids were
  verified UNSTABLE across refreshes, so nothing keys on PATH trip ids), and
  PATH has no service alerts feed initially. Data courtesy of PANYNJ via
  Trillium, subject to their license terms.
- [x] **13b. PATH (realtime backend)**: the community GTFS-RT bridge feed is
  polled and decoded into trains placed at their next station (the bridge
  carries no vehicle positions) and a per-station arrivals index, served from
  `/api/path` and `/api/path-arrivals/{stop_id}`. No cross-poll identity and
  null glide anchors by design: bridge trip ids churn 100% when the upstream
  refreshes, so every poll decodes independently (gliding is 13d).
- [x] **13c. PATH (frontend layer)**: PATH joins the map as its own toggleable
  layer group trio: route polylines in each route's color, clickable station
  dots reusing the shared live-arrivals popup machinery (buckets ordered
  To New York, To New Jersey, Trains), and trains drawn at their placed
  stations. The train layer is rebuilt wholesale each poll rather than diffed
  on trip_id (unstable, see 13b), a failed poll keeps last-known markers, and
  PATH popups carry no alerts block because PATH has no alerts feed.
- [x] **13d-1. PATH synthetic identity (backend)**: /api/path trains carry a
  stable backend-minted `id` and prev glide anchors. A pure, clock-free
  matcher joins each decoded generation to the last by same-stop
  nearest-arrival within 60s (bilateral-unique, ties reset identity) or by a
  unique advance to the immediate successor in the static station order
  (built from stop_times.txt with child platform ids resolved to the parent
  stations the bridge uses). Identities expire after 3 absent generations;
  duplicate re-served generations carry everything unchanged. The bridge's
  unstable trip hash is dropped from the payload. Frontend gliding over
  these ids is 13d-2.
- [x] **13d-2. PATH gliding (frontend)**: applyPath moves from the 13c
  wholesale rebuild to keyed diffing on the backend's stable ids, so markers
  and open popups survive polls. Anchored trains join the shared animateTrains
  glide path, interpolating along the same polylines the layer draws (a PATH
  entry in the subway-style interpolation index) under PATH's own slice cap
  (Journal Square to Harrison outgrows the subway cap; railroad-scale slack
  would surrender misprojection protection for nothing). Anchorless trains sit
  placed, as before.
- [x] **14a. NYC Ferry (static foundation)**: the NYC Ferry static GTFS (stops,
  routes, shapes, trips) is downloaded, cached, and served from its own warmup
  group (modeled on the PATH group) via `/api/ferry-stops` (landing markers with
  a `wheelchair` accessibility flag) and `/api/ferry-routes` (the nine routes
  with names, colors, and modal route geometry). Static only: realtime ferry
  placement is a later phase. The trips table carries the trip to route map that
  join needs. Data comes via NYC Ferry's Connexionz endpoint under the Developer
  Terms on ferry.nyc/developer-tools/ (a revocable right to integrate the GTFS
  into sites and applications; NYC Ferry retains all rights).
- [x] **14b. NYC Ferry (realtime decoder + arrivals)**: the two ferry realtime
  feeds (VehiclePositions + TripUpdates) are polled each cycle and decoded into
  live GPS boats (`/api/ferry`) and a per-dock arrivals index grouped by route
  (`/api/ferry-arrivals/{stop_id}`). Both feeds carry an empty route_id, so route
  is recovered by joining trip_id through 14a's static trip-to-route map; a
  positioned boat that does not join keeps a null route (never dropped), while a
  deadheading boat (empty trip_id) is dropped. `bearing` is omitted (always 0.0);
  `status` and raw `speed` are passed through. An empty overnight poll clears the
  boats (success-replaces), unlike a failed poll (retains last-known). Endpoints
  land dark until the frontend layer (14c). No ferry alerts yet (added in 14d).
- [x] **14c. NYC Ferry (frontend layer)**: a toggleable ferry layer draws the
  route polylines, clickable docks with live arrival popups (bucketed by route,
  counting down to arrival or to departure when a boat is dwelling, and surfacing
  the dock's wheelchair-accessibility flag, the first accessibility display in
  the app), and moving GPS boat markers keyed on their stable vehicle id (a
  boat-hull shape that dims when STOPPED_AT a dock). An empty poll clears the
  boats immediately, preserving 14b's server-side empty-replaces / failure-retains
  split on the client. No speed shown (unit uncertain, a queued follow-up); ferry
  alerts follow in 14d.
- [x] **14d. NYC Ferry service alerts**: ferries join the same `/api/alerts`
  pipeline as the MTA systems. The backend adds NYC Ferry's keyless Connexionz
  GTFS-RT alert feed to `ALERT_FEED_URLS`; the generic gather, per-system
  retention, health map, and `degraded_systems` all extend with no other change,
  and the pure GTFS-RT decode needs none. On the map, a dock popup prepends alerts
  that name that dock's stop and a boat popup prepends alerts that name that boat's
  route. Docks show stop-scoped alerts only for now (no dock-to-routes mapping yet,
  a shared follow-up); a route-scoped ferry alert still reaches riders on every boat
  of that route.
- [x] **15a. NJ Transit (static foundation + the token door)**: NJ Transit's rail
  GTFS, and the first credentialed upstream in this app. Every RailData endpoint is
  `POST multipart/form-data` with a short-lived token as a **form field**, minted by
  exchanging a username and password, so `backend/njt_auth.py` is the one door every
  NJT request goes through: a single-flight token cache (N concurrent callers, one
  mint), and exactly one re-mint per attempt, enforced by there being no loop. The
  static loader rides the same staged download pipeline as every other archive, so
  the token path is exercised from birth. `/api/njt-stops` serves the station
  markers; realtime is 15b and the frontend 15c, so nothing here draws anything.
  See **NJ Transit** under Data sources for the three facts that shape all of it.

## Notes

- The MTA's logos, official map, and route symbols require a license. Use your
  own colors and markers rather than official MTA branding.
- Cache the static GTFS in memory on startup — it's large; don't reload per request.
- Phase 4 (subways) is the hard part: joining realtime `trip_id`s to physical
  stations involves fiddly matching against the static schedule, and the subway
  feeds use NYC-specific protobuf extensions. Expect to iterate.
- **Adding a system (Amtrak, NJ Transit, a second ferry operator) means using two
  seams, not copying a neighbouring file.** Build every marker with `labeledMarker`
  in `frontend/systems/shared.js`: it owns `keyboard: false` and the `role="img"`
  plus `aria-label` that replace what that option strips, and a marker made any other
  way rejoins the tab order as an unnamed button. `frontend/markers.test.js` fails
  the build if a system calls `L.marker` directly, if it names markers with none of
  the `helpers.js` name builders, or if it reuses markers across polls without
  calling `setMarkerName` to keep the label with the data. Register the system's
  stations with `registerStation` in the same file, using a **system-qualified** key:
  station ids collide across systems, and the contract tier measured 21 of 24 ferry
  dock ids colliding with Metro-North station ids.

## Contributing

Issues are welcome, and larger changes are best discussed in an issue first; to
report a security problem, see [SECURITY.md](SECURITY.md) instead of opening a
public issue. Contributions are accepted under the terms of the Apache-2.0
license, per its Section 5.

## License

The code in this repository is licensed under the Apache License, Version 2.0;
see [LICENSE](LICENSE).

Vendored third-party assets carry their own licenses in their own directories.
Leaflet (the map library) is self-hosted under `frontend/vendor/leaflet/` and
stays under its BSD-2-Clause license; see `frontend/vendor/leaflet/LICENSE`.
Apache-2.0 covers this project's own code, not those vendored files.

The transit DATA served through this code is a separate matter that no code
license changes: it remains governed by each provider's own terms, including the
MTA's terms and conditions, PANYNJ's terms for the PATH data (published via
Trillium), NYC Ferry's Developer Terms, and 511NY / NYSDOT's open-data terms for
the AirTrain JFK schedule. Apache-2.0 covers this project's own source, not the
underlying agency data.
