"""Load the committed AirTrain JFK static fixture (data/airtrain_jfk.json).

AirTrain JFK has no public realtime feed, so this module just reads the one
committed JSON artifact produced by scripts/gen_airtrain_fixture.py. It does NOT
download anything and is deliberately NOT part of the GTFS static warmup path:
that path exists for stale-prone network downloads with retry, whereas this is a
local file shipped with the app. See the generator's header for the source URL
and the expired-calendar caveat.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AIRTRAIN_FIXTURE = PROJECT_ROOT / "data" / "airtrain_jfk.json"

# Structural keys every entry must carry. Exact station/route COUNTS are asserted
# in the tests (the fixture is a golden artifact), not enforced here, so a
# legitimate future regeneration that adds a stop does not also need a code edit.
_STATION_KEYS = ("id", "name", "lat", "lon")
_ROUTE_KEYS = ("id", "name", "polyline", "stations", "headways")
_BAND_KEYS = ("start", "end", "headway_min")


def load_airtrain(path: Path = AIRTRAIN_FIXTURE) -> dict:
    """Read and validate the committed AirTrain fixture into {stations, routes}.

    WHY this raises (and is called synchronously at startup, aborting boot on a
    bad fixture) rather than degrading gracefully like the network static loaders:
    the fixture is a committed artifact shipped with the app, so a missing,
    malformed, or wrong-shaped file is a build/deploy bug that must fail LOUDLY in
    CI and at boot, not a transient upstream outage to ride out. There is no
    network here and nothing to retry, so a hard failure is the correct, visible
    behavior. The `_provenance` block in the file is intentionally dropped: only
    the rider-facing {stations, routes} are served.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    stations = data.get("stations")
    routes = data.get("routes")
    if not isinstance(stations, list) or not stations:
        raise ValueError("AirTrain fixture: 'stations' must be a non-empty list")
    if not isinstance(routes, list) or not routes:
        raise ValueError("AirTrain fixture: 'routes' must be a non-empty list")
    for s in stations:
        if not isinstance(s, dict) or any(k not in s for k in _STATION_KEYS):
            raise ValueError(f"AirTrain fixture: station missing keys {_STATION_KEYS}: {s!r}")
    for r in routes:
        if not isinstance(r, dict) or any(k not in r for k in _ROUTE_KEYS):
            raise ValueError(f"AirTrain fixture: route missing keys {_ROUTE_KEYS}: {r!r}")
        bands = r["headways"]
        if not isinstance(bands, list):
            raise ValueError(f"AirTrain fixture: route {r.get('id')!r} headways must be a list")
        for band in bands:
            if not isinstance(band, dict) or any(k not in band for k in _BAND_KEYS):
                raise ValueError(
                    f"AirTrain fixture: headway band missing keys {_BAND_KEYS}: {band!r}"
                )
    # Serve only the rider-facing payload; drop _provenance and any other extras.
    return {"stations": stations, "routes": routes}
