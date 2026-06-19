# NYC Transit Live

A live map of NYC subways and buses, built on the MTA's public real-time feeds.
Buses report true GPS positions and move on the map; subways are placed at their
next station using real-time arrival data joined against the static schedule,
then glide between stations as time passes (v1: straight-line interpolation
between the previous and next station; v2 will follow the actual route geometry).
The feeds usually prune the just-departed stop, so the backend carries each
train's previous-poll station forward across polls as that anchor — letting
trains glide even when the feed omits where they came from.

## How it works

A small FastAPI backend polls the MTA's GTFS-Realtime feeds every ~20 seconds,
decodes the protobuf, and exposes clean JSON. A Leaflet frontend polls that JSON
and draws/moves markers. The backend does the polling once and serves many
browser clients, so the MTA endpoints aren't hit on every page refresh.

Clicking a subway station marker shows the upcoming trains in each direction
with live countdowns. The same subway poll that places trains also builds a
per-station arrivals index in memory (the stops a train placement discards are
exactly those arrival times), so a click is served from memory without hitting
the MTA. The endpoints involved:

- `GET /api/subway-stops` — station markers `[{id, name, lat, lon}]`, static
  for the session (cached by the browser).
- `GET /api/subway-arrivals/{station_id}` — `{fetched_at, station_id,
  station_name, directions: {Northbound, Southbound}}` from the in-memory index,
  refreshed each poll; the frontend ticks the countdowns down between polls.

```
nyc-transit-live/
├── backend/
│   ├── main.py              # FastAPI app + JSON endpoints, serves the frontend
│   ├── feeds.py             # fetch + decode GTFS-RT protobuf (buses + subways)
│   ├── static_data.py       # load stop coords / route shapes from static GTFS
│   ├── bus_static.py        # background-built on-disk index of bus route shapes
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
├── data/
│   ├── gtfs_static/         # downloaded static subway GTFS (gitignored)
│   └── cache/bus_routes/    # background-built bus route index (gitignored)
├── .github/workflows/ci.yml # backend pytest + frontend node tests
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

## Data sources

- **Buses** — MTA Bus Time `VehiclePositions` feed (requires key). Real lat/lon.
- **Subways** — MTA keyless GTFS-RT feeds, grouped by line (ACE, BDFM, numbered
  lines, etc.). These carry trip/arrival updates, not GPS, so trains are shown
  at their next station.
- **Static GTFS** — stop coordinates and route shapes, downloaded into
  `data/gtfs_static/` and loaded into memory at startup.

All feeds are free to use. Data is GTFS-Realtime (protobuf), decoded server-side.

## Scaling

The deploy must run a **single uvicorn worker** (the default; no `--workers`
flag). `bus_static` keeps its index status and partial flag as per-process
state: with multiple workers, each would download and build the bus route
index independently, and a worker whose build partially failed would 404
routes that another worker indexed fine. Route geometry itself is read from
the shared on-disk cache, so data wouldn't corrupt — but going multi-worker
would need a file lock around the index build (so one worker builds while
the others wait) and workers re-reading the manifest instead of trusting
their own build result.

## Monitoring

`GET /api/status` returns an always-200 operational snapshot: per-feed cache
freshness — both `age_s` (since this server last polled) and `feed_age_s` (how
stale the feed's own content was at poll time) — the last recorded poll error
if any, the bus route index state, and the static subway GTFS age.

`GET /healthz` is the readiness probe (Railway's healthcheck points here). It
returns 503 when the app can't serve fresh data: no feed is fresh, or the bus
route index build has failed. It stays healthy as long as **at least one** feed
is fresh, so a misconfigured key (which only stops the bus feed) doesn't take
down an otherwise-working subway map, and a still-building index during cold
start doesn't flap it.

Both feed envelopes (`/api/buses`, `/api/subways`) carry `fetched_at` (this
server's poll time) and `feed_timestamp` (the feed's own content time, oldest
across the subway feeds). The frontend judges staleness from the difference of
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
  just-departed stop. (v2: follow route geometry.)

## Notes

- The MTA's logos, official map, and route symbols require a license. Use your
  own colors and markers rather than official MTA branding.
- Cache the static GTFS in memory on startup — it's large; don't reload per request.
- Phase 4 (subways) is the hard part: joining realtime `trip_id`s to physical
  stations involves fiddly matching against the static schedule, and the subway
  feeds use NYC-specific protobuf extensions. Expect to iterate.

## License

Personal project. MTA data is subject to the MTA's terms and conditions.
