# NYC Transit Live

A live map of NYC subways and buses, built on the MTA's public real-time feeds.
Buses report true GPS positions and move on the map; subways are placed at their
next station using real-time arrival data joined against the static schedule.

## How it works

A small FastAPI backend polls the MTA's GTFS-Realtime feeds every ~30 seconds,
decodes the protobuf, and exposes clean JSON. A Leaflet frontend polls that JSON
and draws/moves markers. The backend does the polling once and serves many
browser clients, so the MTA endpoints aren't hit on every page refresh.

```
nyc-transit-live/
├── backend/
│   ├── main.py          # FastAPI app + JSON endpoints, serves the frontend
│   ├── feeds.py         # fetch + decode GTFS-RT protobuf
│   ├── static_data.py   # load stop coords / route shapes from static GTFS
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── map.js           # Leaflet map, polls backend, draws markers
│   └── style.css
├── data/
│   └── gtfs_static/     # downloaded static GTFS (gitignored)
└── .env                 # BUS_TIME_API_KEY (gitignored)
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
4. **Run it.**
   ```bash
   uvicorn main:app --reload
   ```
   Then open http://localhost:8000.

## Data sources

- **Buses** — MTA Bus Time `VehiclePositions` feed (requires key). Real lat/lon.
- **Subways** — MTA keyless GTFS-RT feeds, grouped by line (ACE, BDFM, numbered
  lines, etc.). These carry trip/arrival updates, not GPS, so trains are shown
  at their next station.
- **Static GTFS** — stop coordinates and route shapes, downloaded into
  `data/gtfs_static/` and loaded into memory at startup.

All feeds are free to use. Data is GTFS-Realtime (protobuf), decoded server-side.

## Build phases

- [ ] **1. Backend proves data flows** — `/api/buses` returns live JSON.
- [ ] **2. Minimal map** — Leaflet map plots buses, polling every 15s.
- [ ] **3. Readable markers** — bearing rotation, route colors, popups, failure handling.
- [ ] **4. Subways** — `/api/subways`, trains placed at next station via static GTFS.
- [ ] **5. Route lines** — draw `shapes.txt` route geometry under the markers.

## Notes

- The MTA's logos, official map, and route symbols require a license. Use your
  own colors and markers rather than official MTA branding.
- Cache the static GTFS in memory on startup — it's large; don't reload per request.
- Phase 4 (subways) is the hard part: joining realtime `trip_id`s to physical
  stations involves fiddly matching against the static schedule, and the subway
  feeds use NYC-specific protobuf extensions. Expect to iterate.

## License

Personal project. MTA data is subject to the MTA's terms and conditions.
