#!/usr/bin/env python3
"""F09 (P2), "Invalid numeric coordinates can corrupt output or kill warmup": reproduction.

THE AUDIT'S CLAIM, quoted from docs/reviews/audit-2026-09-05.md section 1:

    "`float()` accepts values such as `nan` and `inf`. The parsers do not
    consistently apply finite and geographic range checks. An actual subway-stops
    ASGI response containing a parsed NaN returned **HTTP 200 with `lat:null`**,
    rather than a 500; the frontend passes that coordinate directly to Leaflet,
    where null is numerically coerced to zero.

     A separate NJT reproduction passed accepted NaN shape points into the real
     geometry builder. It raised `ValueError: cannot convert float NaN to
     integer`. Because derived geometry is built outside the warmup's
     catch/retry block, the task terminated while its status remained `loading`."

RUN IT (from the repo root):

    cd /home/user/nyc-transit-live && \
      ./.venv/bin/python docs/reviews/audit-2026-09-05/f09_nonfinite_coordinates.py

WHAT THIS SCRIPT MEASURES, AND HOW.

Half (a), the subway side:
  1. The REAL parsers backend/static_data.py::_parse_stops_rows and
     ::_parse_stations_rows (the second is the audit's L146 anchor) are fed a
     stops.txt whose coordinate column carries nan / inf / -inf / an out-of-range
     number / junk / empty. Which tokens survive the parse is the measurement.
  2. The REAL ASGI app (backend/main.py::app, driven over httpx.ASGITransport with
     no lifespan) serves GET /api/subway-stops from that parsed dict. The status
     code and the response bytes are printed verbatim.
  3. The REAL vendored Leaflet 1.9.4 (frontend/vendor/leaflet/leaflet.js, the
     production asset) is loaded into a node:vm and handed the coordinates out of
     THAT response body through the same call the production line makes
     (frontend/systems/subway.js: L.circleMarker([station.lat, station.lon])).
     Leaflet's own projection (L.CRS.EPSG3857.latLngToPoint) then says where the
     marker lands. Both source support and execution, as reported below.
  4. Blast radius beyond the marker: the REAL subway decoder
     backend/feeds/subway.py::_decode_trains is run over the committed capture
     backend/tests/fixtures/subway_1_7_s.pb with one stop of the committed
     subway_1_7_s_stops.json holding a NaN latitude.

Half (b), the NJ Transit side:
  5. The REAL parser backend/njt_static.py::_parse_shapes (the audit's L316
     anchor) reads the COMMITTED fixture backend/tests/fixtures/njt_gtfs/shapes.txt
     with one row's shape_pt_lat overwritten by nan / inf / -inf.
  6. The REAL builder njt_static.build_njt_route_shapes consumes the whole
     fixture publication parsed by the REAL njt_static._parse_open, and the exact
     exception (type, message, raising line) is printed.
  7. The REAL warmup backend/warmups.py::_warm_njt_static is driven as an asyncio
     task against the real app object. Final task state, the exception, the
     njt_static_status string and the live /api/njt-stops response are printed.

WHAT IS INJECTED (everything else is production code or committed fixture bytes):
  * the nonfinite coordinate tokens in the CSV rows listed above,
  * njt_static.load_njt_static, replaced by a coroutine that returns the
    fixture publication parsed above (this is what keeps the run hermetic: no
    download, no NJT host, NO MINT),
  * njt_auth.is_configured, replaced by a lambda returning True so the warmup
    does not take its not-configured short circuit. NO NJT credential is set,
    read or invented anywhere in this file.
  * the app.state fields backend/main.py's lifespan would set, assigned directly
    because the lifespan (which would start pollers and downloads) is not run.

HERMETIC: a socket guard installed below fails the run if anything attempts a
connect or a DNS lookup. No wall-clock dependence: the subway decoder is driven
at the `now` recorded in the committed subway_1_7_s_expected.json, not today.

EXIT: 0 while every recorded behavior still holds, 1 with the failing checks named.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FIXTURES = BACKEND / "tests" / "fixtures"
FRONTEND = REPO / "frontend"
# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT. The pop below this used to
# be the whole scrub and it was not one: env_seams calls load_dotenv when it is
# imported, which refills any credential the pop removed. The addresses set here are
# what make this process unable to reach NJ Transit at all, credentials or not. See
# _hermetic for the leak this closes and the measurement behind it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(BACKEND))


# ---------------------------------------------------------------------------
# Hermeticity guard: nothing here may reach the network, and NJT least of all.
# ---------------------------------------------------------------------------
class _NetworkAttempted(RuntimeError):
    pass


_dial_attempts: list[str] = []


def _blocked(*args, **kwargs):
    _dial_attempts.append(repr(args[:2]))
    raise _NetworkAttempted("this reproduction is hermetic; no outbound connection is allowed")


socket.socket.connect = _blocked  # type: ignore[method-assign]
socket.socket.connect_ex = _blocked  # type: ignore[method-assign]
socket.create_connection = _blocked  # type: ignore[assignment]
socket.getaddrinfo = _blocked  # type: ignore[assignment]

# main must be imported before warmups: warmups imports main, and main imports
# names back out of warmups, so importing warmups first hits the half-built module.
import main  # noqa: E402
import warmups  # noqa: E402

import feeds  # noqa: E402
import httpx  # noqa: E402
import njt_auth  # noqa: E402
import njt_static  # noqa: E402
import static_data  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()



CHECKS: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    CHECKS.append((bool(ok), label, detail))
    return bool(ok)


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def nonfinite(value) -> bool:
    return isinstance(value, float) and not math.isfinite(value)


def describe(value) -> str:
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    return repr(value)


# ---------------------------------------------------------------------------
# Half (a) step 1: the real subway coordinate parsers.
# ---------------------------------------------------------------------------
rule("F09 (a1)  backend/static_data.py: what the subway coordinate parsers accept")

# A real coordinate, lifted from the committed capture's stop table rather than
# invented, so the control row is fixture data.
_committed_stops = json.loads((FIXTURES / "subway_1_7_s_stops.json").read_text())
_control_id = sorted(_committed_stops)[0]
GOOD_LAT = _committed_stops[_control_id]["lat"]
GOOD_LON = _committed_stops[_control_id]["lon"]
print(f"control coordinate from subway_1_7_s_stops.json[{_control_id}]: {GOOD_LAT}, {GOOD_LON}")

# THE INJECTION. Every row below is the committed control coordinate with one
# column overwritten by the token named in stop_name. location_type=1 makes them
# parent stations, which is what /api/subway-stops serves.
ROWS = [
    ("101", "control row untouched", str(GOOD_LAT), str(GOOD_LON)),
    ("102", "injected nan latitude", "nan", str(GOOD_LON)),
    ("103", "injected nan longitude", str(GOOD_LAT), "nan"),
    ("104", "injected nan in both columns", "nan", "nan"),
    ("105", "injected inf latitude", "inf", str(GOOD_LON)),
    ("106", "injected -inf latitude", "-inf", str(GOOD_LON)),
    ("107", "injected out-of-range latitude", "9999.5", str(GOOD_LON)),
    ("108", "injected non-numeric latitude", "not-a-number", str(GOOD_LON)),
    ("109", "injected empty latitude", "", str(GOOD_LON)),
]
STOPS_TXT = "stop_id,stop_name,stop_lat,stop_lon,location_type\n" + "".join(
    f"{sid},{name},{lat},{lon},1\n" for sid, name, lat, lon in ROWS
)

float_lines = [
    n
    for n, line in enumerate(( BACKEND / "static_data.py").read_text().splitlines(), 1)
    if 'float(row.get("stop_lat")' in line
]
print(f"float() coordinate conversions in static_data.py at lines: {float_lines}")

stations = static_data._parse_stations_rows(io.BytesIO(STOPS_TXT.encode()))
platforms = static_data._parse_stops_rows(io.BytesIO(STOPS_TXT.encode()))
print()
print(f"{'row':<5}{'injected token':<32}{'_parse_stations_rows lat':<28}kept?")
for sid, name, lat, lon in ROWS:
    kept = sid in stations
    got = describe(stations[sid]["lat"]) if kept else "(row dropped)"
    print(f"{sid:<5}{name:<32}{got:<28}{'yes' if kept else 'no'}")
print()
print(f"_parse_stations_rows kept {len(stations)}/{len(ROWS)} rows: {sorted(stations)}")
print(f"_parse_stops_rows     kept {len(platforms)}/{len(ROWS)} rows: {sorted(platforms)}")

accepted_nonfinite = sorted(
    sid for sid, s in stations.items() if nonfinite(s["lat"]) or nonfinite(s["lon"])
)
print(f"rows accepted carrying a NONFINITE coordinate: {accepted_nonfinite}")
print(f"row 107 (latitude 9999.5, far outside any geographic range) kept: {'107' in stations}")

check(
    accepted_nonfinite == ["102", "103", "104", "105", "106"],
    "static_data parsers accept nan, inf and -inf coordinates",
    f"nonfinite rows kept = {accepted_nonfinite}",
)
check(
    "107" in stations and "108" not in stations and "109" not in stations,
    "the same parsers keep an out-of-range 9999.5 and reject only unparseable text",
    f"107 kept={'107' in stations}, 108 kept={'108' in stations}, 109 kept={'109' in stations}",
)


# ---------------------------------------------------------------------------
# Half (a) step 2: the real ASGI app.
# ---------------------------------------------------------------------------
rule("F09 (a2)  the real ASGI app: GET /api/subway-stops with a parsed NaN in state")

app = main.app
# The fields backend/main.py's lifespan sets for the subway group, assigned here
# because the lifespan itself would start pollers and downloads.
app.state.subway_stations = stations
app.state.subway_station_routes = {}
app.state.subway_static_status = "ready"


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://reproduction") as client:
        return await client.get(path)


response = asyncio.run(_get("/api/subway-stops"))
body = response.text
print(f"HTTP status code : {response.status_code}")
print(f"content-type     : {response.headers.get('content-type')}")
print(f"cache-control    : {response.headers.get('cache-control')}")
print("response body (verbatim):")
for row in json.loads(body):
    print("   " + json.dumps(row))
print()
served = {row["id"]: row for row in json.loads(body)}
null_lat = sorted(sid for sid, row in served.items() if row["lat"] is None)
null_lon = sorted(sid for sid, row in served.items() if row["lon"] is None)
print(f"rows served with lat null: {null_lat}")
print(f"rows served with lon null: {null_lon}")
_literal = '"lat":null'
print(f"raw response bytes contain the literal {_literal} : {_literal in body}")
print(f"row 107 served its out-of-range latitude verbatim: {served['107']['lat']}")

check(
    response.status_code == 200,
    "the subway-stops response is HTTP 200, not a 500",
    f"status={response.status_code}",
)
check(
    '"lat":null' in body and null_lat == ["102", "104", "105", "106"],
    "nan/inf/-inf latitudes are serialized as JSON null (not a 500, not NaN)",
    f"null lat rows={null_lat}",
)
check(
    served["107"]["lat"] == 9999.5,
    "an out-of-range but finite latitude is served verbatim",
    f"107 lat={served['107']['lat']}",
)


# ---------------------------------------------------------------------------
# Half (a) step 3: the real Leaflet, on the real response bytes.
# ---------------------------------------------------------------------------
rule("F09 (a3)  the real vendored Leaflet: what it does with those null coordinates")

subway_js = (FRONTEND / "systems" / "subway.js").read_text().splitlines()
marker_line = next(
    n for n, line in enumerate(subway_js, 1) if "L.circleMarker([station.lat, station.lon]" in line
)
print("production consumer, frontend/systems/subway.js:")
print(f"   {marker_line}: {subway_js[marker_line - 1].strip()}")
print("   (station is one element of the /api/subway-stops body printed above;")
print("    stationLayer is on the map: frontend/systems/shared.js:163)")
stations_js = (FRONTEND / "stations.js").read_text().splitlines()
pan_lines = [n for n, line in enumerate(stations_js, 1) if "map.panTo([entry.lat, entry.lon])" in line]
print(f"second consumer, frontend/stations.js lines {pan_lines}: map.panTo([entry.lat, entry.lon])")

node = shutil.which("node")
if not check(
    bool(node),
    "node is available to execute the vendored Leaflet",
    "" if node else "node not found on PATH",
):
    print("cannot execute Leaflet without node; the frontend half cannot be measured")
    sys.exit(1)

LEAFLET_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const [leafletPath, bodyPath] = process.argv.slice(2);
const src = fs.readFileSync(leafletPath, "utf8");

// Minimum browser surface Leaflet touches while loading. No DOM behaviour is
// simulated: the code under test (toLatLng, LatLng, CircleMarker, the CRS
// projection) is pure arithmetic over the coordinates handed to it.
const el = () => ({
  style: {}, children: [],
  classList: { add() {}, remove() {}, contains() { return false; } },
  appendChild() {}, removeChild() {}, setAttribute() {}, getAttribute() { return null; },
  addEventListener() {}, removeEventListener() {}, getElementsByTagName() { return []; },
});
const documentStub = {
  documentElement: el(), body: el(), head: el(),
  createElement: () => el(), createElementNS: () => el(),
  addEventListener() {}, removeEventListener() {},
};
const navigatorStub = { userAgent: "node", platform: "node", maxTouchPoints: 0 };
const windowStub = {
  devicePixelRatio: 1, document: documentStub, navigator: navigatorStub,
  screen: { width: 800, height: 600 },
  addEventListener() {}, removeEventListener() {},
};
const sandbox = {
  window: windowStub, document: documentStub, navigator: navigatorStub,
  screen: windowStub.screen, setTimeout, clearTimeout, console,
  module: { exports: {} },
};
sandbox.exports = sandbox.module.exports;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "leaflet.js" });
const L = sandbox.module.exports;

const ZOOM = 12;
const stations = JSON.parse(fs.readFileSync(bodyPath, "utf8"));
const out = { version: L.version, zoom: ZOOM, rows: [] };
for (const station of stations) {
  const row = { id: station.id, lat: station.lat, lon: station.lon };
  // EXACTLY the production call in frontend/systems/subway.js.
  let marker = null;
  try {
    marker = L.circleMarker([station.lat, station.lon], { radius: 4 });
    row.constructed = true;
    row.latlng = marker._latlng === null
      ? null
      : { lat: marker._latlng.lat, lng: marker._latlng.lng };
  } catch (e) {
    row.constructed = false;
    row.constructError = e.constructor.name + ": " + e.message;
  }
  if (marker) {
    try {
      // What Leaflet does to place the marker on the map.
      const point = L.CRS.EPSG3857.latLngToPoint(marker._latlng, ZOOM);
      row.point = { x: point.x, y: point.y };
    } catch (e) {
      row.projectError = e.constructor.name + ": " + e.message;
    }
  }
  out.rows.push(row);
}

// retryUntil is production code (frontend/helpers.js) and is what map.js wraps
// loadStations in; this shows what it does with a throw from that loop.
const helpers = require(process.argv[4]);
let calls = 0;
const slept = [];
const thrower = async () => {
  calls += 1;
  // The throw a null-latitude marker produces when Leaflet projects it.
  L.CRS.EPSG3857.latLngToPoint(L.latLng([null, -73.9]), ZOOM);
  return true;
};
const run = helpers.retryUntil(thrower, {
  baseMs: 500, capMs: 8000,
  sleep: async (ms) => { slept.push(ms); if (slept.length >= 4) throw new Error("STOP"); },
});
run.catch(() => {}).then(() => {
  out.retry = { calls, slept };
  console.log("RESULT " + JSON.stringify(out));
});
"""

with tempfile.TemporaryDirectory() as tmp:
    harness = Path(tmp) / "leaflet_harness.js"
    body_path = Path(tmp) / "subway_stops_body.json"
    harness.write_text(LEAFLET_HARNESS)
    body_path.write_text(body)
    proc = subprocess.run(
        [
            node,
            str(harness),
            str(FRONTEND / "vendor" / "leaflet" / "leaflet.js"),
            str(body_path),
            str(FRONTEND / "helpers.js"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
if proc.returncode != 0:
    print(proc.stdout)
    print(proc.stderr, file=sys.stderr)
    check(False, "the Leaflet harness ran", f"node exited {proc.returncode}")
    leaflet = {"rows": [], "version": "?", "zoom": 12, "retry": {"calls": 0, "slept": []}}
else:
    leaflet = json.loads(next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))[7:])

print()
print(f"vendored Leaflet version: {leaflet['version']} (self-hosted, frontend/vendor/leaflet)")
print(f"{'row':<5}{'lat, lon served':<26}{'L.circleMarker _latlng':<30}projected pixel (zoom 12)")
by_id = {}
for row in leaflet["rows"]:
    served_pair = f"{row['lat']}, {row['lon']}"
    latlng = "null (no LatLng built)" if row.get("latlng") is None else (
        f"LatLng({row['latlng']['lat']}, {row['latlng']['lng']})"
    )
    if "point" in row:
        placed = f"x={row['point']['x']:.1f} y={row['point']['y']:.1f}"
    else:
        placed = row.get("projectError", "?")
    print(f"{row['id']:<5}{served_pair:<26}{latlng:<30}{placed}")
    by_id[row["id"]] = row

control = by_id["101"]
nan_lat_row = by_id["102"]
nan_lon_row = by_id["103"]
both_row = by_id["104"]
print()
if "point" in control and "point" in nan_lon_row:
    drift = abs(nan_lon_row["point"]["x"] - control["point"]["x"])
    print(
        "row 103 (null LONGITUDE) is the audit's coercion: Leaflet builds "
        f"LatLng({nan_lon_row['latlng']['lat']}, {nan_lon_row['latlng']['lng']}), which projects "
        f"{drift:.1f} pixels east of the control at zoom {leaflet['zoom']} "
        "(longitude 0, the Prime Meridian, with the latitude untouched)."
    )
print(
    "rows 102 and 104 (null LATITUDE) do NOT coerce: Leaflet's toLatLng returns null for an "
    "array whose first element is null (typeof null === 'object' fails its array test), the "
    "marker is constructed with _latlng === null, and the projection that places it raises "
    f"{nan_lat_row.get('projectError')!r}."
)
print(
    f"frontend/helpers.js::retryUntil swallows that throw: {leaflet['retry']['calls']} calls, "
    f"backing off {leaflet['retry']['slept']} ms, so loadStations restarts forever and every "
    "station after the bad row stays off the map."
)
out_of_range = by_id["107"]
print(
    "row 107 (finite but out of range, latitude 9999.5) throws nothing at all: Leaflet builds "
    f"LatLng({out_of_range['latlng']['lat']}, {out_of_range['latlng']['lng']}) and Mercator pins "
    f"it at y={out_of_range['point']['y']:.1f}, the top edge of the world, silently."
)

check(
    nan_lon_row.get("latlng") is not None and nan_lon_row["latlng"]["lng"] == 0,
    "a null LONGITUDE is numerically coerced to zero, as the audit describes",
    f"row 103 latlng={nan_lon_row.get('latlng')}",
)
check(
    nan_lat_row.get("latlng") is None
    and both_row.get("latlng") is None
    and "Cannot read properties of null" in (nan_lat_row.get("projectError") or ""),
    "a null LATITUDE is NOT coerced to zero: Leaflet yields a null LatLng that cannot project",
    f"row 102 latlng={nan_lat_row.get('latlng')} projectError={nan_lat_row.get('projectError')}",
)
check(
    leaflet["retry"]["calls"] >= 2,
    "helpers.retryUntil treats that throw as 'not yet' and retries the whole loader",
    f"calls={leaflet['retry']['calls']}",
)


# ---------------------------------------------------------------------------
# Half (a) step 4: the same NaN reaches train placements, not only markers.
# ---------------------------------------------------------------------------
rule("F09 (a4)  blast radius: the real subway decoder over the committed capture")

capture = (FIXTURES / "subway_1_7_s.pb").read_bytes()
expected = json.loads((FIXTURES / "subway_1_7_s_expected.json").read_text())
capture_now = expected["now"]  # the fixture's own recorded instant, never today's clock
clean_stops = json.loads((FIXTURES / "subway_1_7_s_stops.json").read_text())
clean_trains = feeds._decode_trains(capture, clean_stops, expected["feed_key"], capture_now)

victim = "106N"
poisoned_stops = {sid: dict(stop) for sid, stop in clean_stops.items()}
poisoned_stops[victim]["lat"] = float("nan")  # THE INJECTION: one stops.txt row
poisoned_trains = feeds._decode_trains(capture, poisoned_stops, expected["feed_key"], capture_now)
nan_placed = [t for t in poisoned_trains if nonfinite(t["latitude"]) or nonfinite(t["longitude"])]

print(f"capture              : backend/tests/fixtures/subway_1_7_s.pb, feed {expected['feed_key']}")
print(f"decoded at           : now={capture_now} (from subway_1_7_s_expected.json, not today)")
print(f"stops table          : {len(clean_stops)} committed stops, 1 latitude replaced by NaN ({victim})")
print(f"trains decoded clean : {len(clean_trains)}")
print(f"trains decoded after : {len(poisoned_trains)}, of which {len(nan_placed)} carry a NaN position")
if nan_placed:
    t = nan_placed[0]
    print(
        f"example              : trip {t['trip_id']} route {t['route_id']} at "
        f"({describe(t['latitude'])}, {t['longitude']}) next stop {t['stop_id']}"
    )
check(
    len(poisoned_trains) == len(clean_trains) and len(nan_placed) == 1,
    "one NaN stops.txt row also poisons live train placement (no bounds check there either)",
    f"{len(nan_placed)} of {len(poisoned_trains)} trains placed at NaN",
)


# ---------------------------------------------------------------------------
# Half (b) step 5 and 6: the NJT shape parser and the real geometry builder.
# ---------------------------------------------------------------------------
rule("F09 (b1)  backend/njt_static.py + route_geometry.py over the committed NJT fixture")

FIXTURE_GTFS = FIXTURES / "njt_gtfs"
members = {p.name: p.read_text() for p in sorted(FIXTURE_GTFS.iterdir())}
print(f"committed publication: backend/tests/fixtures/njt_gtfs/ ({', '.join(sorted(members))})")

njt_float_lines = [
    n
    for n, line in enumerate((BACKEND / "njt_static.py").read_text().splitlines(), 1)
    if 'float(row["shape_pt_lat"])' in line
]
print(f"shape coordinate conversion in njt_static.py at line(s): {njt_float_lines}")


def zip_of(members_map: dict[str, str]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members_map.items():
            zf.writestr(name, text)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def inject_shape_lat(token: str) -> dict[str, str]:
    """Overwrite the FIRST shapes.txt data row's shape_pt_lat with `token`."""
    lines = members["shapes.txt"].split("\n")
    header = lines[0].split(",")
    lat_col = header.index("shape_pt_lat")
    parts = lines[1].split(",")
    original = parts[lat_col]
    parts[lat_col] = token
    lines[1] = ",".join(parts)
    poisoned = dict(members)
    poisoned["shapes.txt"] = "\n".join(lines)
    poisoned["_injection"] = f"row 1 shape_pt_lat {original} -> {token}"
    return poisoned


clean_publication = njt_static._parse_open(zip_of(members))
clean_lines = njt_static.build_njt_route_shapes(
    clean_publication["trips"], clean_publication["shapes"], clean_publication["routes"]
)
print(
    f"clean parse: {len(clean_publication['stops'])} stops, {len(clean_publication['trips'])} trips, "
    f"{len(clean_publication['shapes'])} shapes, {len(clean_publication['routes'])} routes"
)
print(
    f"clean build: {len(clean_lines)} route entries, "
    f"{sum(len(entry['polylines']) for entry in clean_lines)} polylines, no exception"
)
print()

geometry_failures: dict[str, tuple[str, str, str, int]] = {}
for token in ("nan", "inf", "-inf"):
    poisoned = inject_shape_lat(token)
    note = poisoned.pop("_injection")
    parsed = njt_static._parse_open(zip_of(poisoned))
    bad_points = [
        (shape_id, point)
        for shape_id, points in parsed["shapes"].items()
        for point in points
        if nonfinite(point[0]) or nonfinite(point[1])
    ]
    print(f"injected {token!r} ({note})")
    print(
        f"   _parse_shapes accepted it: {len(bad_points)} nonfinite point(s), "
        f"e.g. shape {bad_points[0][0]} -> [{describe(bad_points[0][1][0])}, {bad_points[0][1][1]}]"
        if bad_points
        else "   _parse_shapes rejected it"
    )
    try:
        njt_static.build_njt_route_shapes(parsed["trips"], parsed["shapes"], parsed["routes"])
        print("   build_njt_route_shapes returned normally (NO exception)")
    except Exception as exc:  # noqa: BLE001 - the exception is the measurement
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        where = f"{Path(frame.filename).name}:{frame.lineno}"
        print(f"   build_njt_route_shapes raised {type(exc).__name__}: {exc}")
        print(f"      at {where}  {frame.line}")
        geometry_failures[token] = (type(exc).__name__, str(exc), where, frame.lineno)
        check(
            bool(bad_points),
            f"njt_static._parse_shapes accepts a {token!r} coordinate",
            f"nonfinite points={len(bad_points)}",
        )

check(
    geometry_failures.get("nan", ("", "", "", 0))[:2]
    == ("ValueError", "cannot convert float NaN to integer"),
    "build_njt_route_shapes raises ValueError: cannot convert float NaN to integer",
    f"nan -> {geometry_failures.get('nan')}",
)
check(
    geometry_failures.get("nan", ("", "", "", 0))[2].startswith("route_geometry.py"),
    "the raise comes from backend/route_geometry.py (the audit's L343 block)",
    f"raised at {geometry_failures.get('nan', ('', '', '?', 0))[2]}",
)
check(
    {t: v[0] for t, v in geometry_failures.items() if t in ("inf", "-inf")}
    == {"inf": "OverflowError", "-inf": "OverflowError"},
    "inf and -inf raise OverflowError there, not ValueError",
    f"inf -> {geometry_failures.get('inf')}, -inf -> {geometry_failures.get('-inf')}",
)


# ---------------------------------------------------------------------------
# Half (b) step 7: the real warmup.
# ---------------------------------------------------------------------------
rule("F09 (b2)  the real warmup backend/warmups.py::_warm_njt_static with that publication")

warm_lines = (BACKEND / "warmups.py").read_text().splitlines()
warm_def_line = next(
    n for n, line in enumerate(warm_lines, 1) if line.startswith("async def _warm_njt_static(")
)
try_line = next(
    n for n, line in enumerate(warm_lines, 1) if n > warm_def_line and line.strip() == "try:"
)
except_line = next(
    n for n, line in enumerate(warm_lines, 1) if n > try_line and "except Exception as exc:" in line
)
build_call_line = next(
    n
    for n, line in enumerate(warm_lines, 1)
    if n > except_line and "njt_static.build_njt_route_shapes(" in line
)
print(
    f"warmups.py: try at line {try_line}, its 'except Exception' at line {except_line}, "
    f"build_njt_route_shapes called at line {build_call_line} (outside that block)"
)

poisoned = inject_shape_lat("nan")
poisoned.pop("_injection")
warmup_publication = njt_static._parse_open(zip_of(poisoned))


async def _injected_load(*args, **kwargs):
    """Stands in for njt_static.load_njt_static. THIS IS THE HERMETIC SEAM: it
    returns the fixture publication parsed above, so no download, no NJT host and
    NO MINT is ever touched."""
    return warmup_publication


njt_static.load_njt_static = _injected_load
njt_auth.is_configured = lambda env=None: True  # no credential is set or read

# The njt fields backend/main.py's lifespan sets before the warmup runs.
app.state.njt_static = {}
app.state.njt_stops = {}
app.state.njt_routes = []
app.state.njt_station_routes = {}
app.state.njt_trips = {}
app.state.njt_stop_schedule = {}
app.state.njt_static_status = "loading"


async def _drive_warmup() -> dict:
    task = asyncio.create_task(warmups._warm_njt_static(app))
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=30)
    except BaseException:  # noqa: BLE001 - the task's own exception is the measurement
        pass
    result = {
        "done": task.done(),
        "cancelled": task.cancelled(),
        "exception": task.exception() if task.done() and not task.cancelled() else None,
    }
    result["stops_response"] = await _get("/api/njt-stops")
    result["routes_response"] = await _get("/api/njt-routes")
    result["status_response"] = await _get("/api/status")
    return result


warm = asyncio.run(_drive_warmup())
exc = warm["exception"]
frames = traceback.extract_tb(exc.__traceback__) if exc is not None else []
warm_frame = next((f for f in frames if f.filename.endswith("warmups.py")), None)

print()
print(f"warmup task done            : {warm['done']}")
print(f"warmup task cancelled       : {warm['cancelled']}")
print(f"warmup task exception       : {type(exc).__name__}: {exc}" if exc else "no exception")
if warm_frame:
    print(f"   escaped the warmup at    : warmups.py:{warm_frame.lineno}  {warm_frame.line}")
if frames:
    print(f"   originally raised at     : {Path(frames[-1].filename).name}:{frames[-1].lineno}")
print(f"app.state.njt_static_status : {app.state.njt_static_status!r}")
print(f"app.state.njt_stops         : {len(app.state.njt_stops)} stops (published before the raise)")
print(f"app.state.njt_station_routes: {len(app.state.njt_station_routes)} entries (same)")
print(f"app.state.njt_routes        : {app.state.njt_routes} (never assigned)")
print(f"app.state.njt_trips         : {app.state.njt_trips} (never assigned)")
print(f"app.state.njt_stop_schedule : {app.state.njt_stop_schedule} (never assigned)")
print(
    f"GET /api/njt-stops          : {warm['stops_response'].status_code} "
    f"{warm['stops_response'].text}"
)
print(
    f"GET /api/njt-routes         : {warm['routes_response'].status_code} "
    f"{warm['routes_response'].text}"
)
print(
    "GET /api/status njt_static  : "
    f"{json.loads(warm['status_response'].text).get('njt_static')!r}"
)
print("no retry is scheduled: the task object is finished, so nothing will ever call it again.")

check(
    warm["done"] and isinstance(exc, ValueError) and str(exc) == "cannot convert float NaN to integer",
    "the warmup task terminates with the geometry ValueError",
    f"done={warm['done']} exception={type(exc).__name__ if exc else None}: {exc}",
)
check(
    warm_frame is not None and warm_frame.lineno == build_call_line,
    "the exception escapes at the build_njt_route_shapes call, outside the catch/retry block",
    f"escaped at warmups.py:{warm_frame.lineno if warm_frame else None}, "
    f"try/except spans {try_line}-{except_line}",
)
check(
    app.state.njt_static_status == "loading",
    'the status string is still "loading" after the task has died',
    f"status={app.state.njt_static_status!r}",
)
check(
    warm["stops_response"].status_code == 503
    and json.loads(warm["status_response"].text).get("njt_static") == "loading",
    "the endpoints keep answering 503 'still loading' and /api/status agrees, forever",
    f"njt-stops={warm['stops_response'].status_code}, "
    f"status.njt_static={json.loads(warm['status_response'].text).get('njt_static')!r}",
)
check(
    len(app.state.njt_stops) > 0 and app.state.njt_routes == [] and app.state.njt_trips == {},
    "partial application state was published before the failed construction",
    f"{len(app.state.njt_stops)} stops live while routes/trips/schedule stayed empty",
)


# ---------------------------------------------------------------------------
# The consistency question: where do the geographic range checks apply?
# ---------------------------------------------------------------------------
rule("F09 (c)  are the geographic range checks applied consistently?")

PREDICATES = ("_in_nyc", "_in_railroad_box")
STATIC_MODULES = [
    "static_data.py",
    "static_shared.py",
    "njt_static.py",
    "railroad_static.py",
    "path_static.py",
    "ferry_static.py",
    "bus_static.py",
    "airtrain_static.py",
    "route_geometry.py",
]
REALTIME_MODULES = [
    "feeds/subway.py",
    "feeds/buses.py",
    "feeds/railroad.py",
    "feeds/path.py",
    "feeds/ferry.py",
    "feeds/njt.py",
]


def tracked_backend_modules() -> list[Path]:
    """Every backend .py file GIT TRACKS, outside tests. Not whatever is on disk.

    THE DIFFERENCE IS THE WHOLE MEASUREMENT. This scan asserts that the backend
    contains NO math.isfinite / isnan / isinf call at all, which is the audit's
    finding. BACKEND.rglob("*.py") walks the directory, and a developer checkout has
    backend/.venv inside it: measured on this machine, 22 call sites, every one of
    them in a vendored package under .venv and none in this repository. So the scan
    reported the finding as refuted on any checkout with a virtualenv in the
    conventional place, which is every checkout the contract tier documents.

    Same rule and same fix as the tracked-file scans in f13, and the same one PR 99
    applied to the backend module scan ("Scan tracked backend modules, not whatever
    is on disk"). git ls-files answers with the repository's own contents, so a venv,
    a build directory, an editor backup and a stray notebook are all invisible to it.
    """
    out = subprocess.run(
        ["git", "ls-files", "--", "backend/*.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\n")
    return [
        REPO / rel
        for rel in (line.strip() for line in out)
        if rel and "/tests/" not in rel and not rel.startswith("backend/tests/")
    ]


def call_sites(relative: str) -> int:
    text = (BACKEND / relative).read_text().splitlines()
    return sum(
        1
        for line in text
        if not line.lstrip().startswith("def ")
        and any(re.search(rf"\b{name}\(", line) for name in PREDICATES)
    )


print("bounds predicates live in backend/feeds/shared.py:")
print(f"   NYC box      : lat [{feeds.NYC_LAT_MIN}, {feeds.NYC_LAT_MAX}] "
      f"lon [{feeds.NYC_LON_MIN}, {feeds.NYC_LON_MAX}]")
print(f"   railroad box : lat [{feeds.RAILROAD_LAT_MIN}, {feeds.RAILROAD_LAT_MAX}] "
      f"lon [{feeds.RAILROAD_LON_MIN}, {feeds.RAILROAD_LON_MAX}]")
print()
print("call sites of those predicates:")
static_total = 0
for relative in STATIC_MODULES:
    n = call_sites(relative)
    static_total += n
    print(f"   {'backend/' + relative:<34}{n}   (static GTFS parser / derived geometry)")
realtime_total = 0
for relative in REALTIME_MODULES:
    n = call_sites(relative)
    realtime_total += n
    print(f"   {'backend/' + relative:<34}{n}   (realtime decoder)")
print(f"   TOTAL static parsers: {static_total}     TOTAL realtime decoders: {realtime_total}")
print()
finite_calls = sum(
    len(re.findall(r"math\.(isfinite|isnan|isinf)\(", path.read_text()))
    for path in tracked_backend_modules()
)
print(f"math.isfinite / isnan / isinf call sites in backend production code: {finite_calls}")
print()
print("what the predicates would have done with the injected values:")
for label, lat, lon in (
    ("nan latitude", float("nan"), GOOD_LON),
    ("inf latitude", float("inf"), GOOD_LON),
    ("-inf latitude", float("-inf"), GOOD_LON),
    ("9999.5 latitude", 9999.5, GOOD_LON),
    ("the control row", GOOD_LAT, GOOD_LON),
):
    print(
        f"   {label:<18}_in_nyc={feeds._in_nyc(lat, lon)!s:<6}"
        f"_in_railroad_box={feeds._in_railroad_box(lat, lon)}"
    )

check(
    static_total == 0 and realtime_total >= 3,
    "the range checks are applied ONLY in realtime decoders, never in a static parser",
    f"static call sites={static_total}, realtime call sites={realtime_total}",
)
check(
    finite_calls == 0,
    "no finite check exists anywhere in the backend",
    f"math.isfinite/isnan/isinf call sites={finite_calls}",
)
check(
    feeds._in_nyc(float("nan"), GOOD_LON) is False and feeds._in_nyc(float("inf"), GOOD_LON) is False,
    "the existing predicates would have rejected nan and inf had a static parser called them",
    "comparison against NaN is False, so the box test rejects it",
)


# ---------------------------------------------------------------------------
# Verdict.
# ---------------------------------------------------------------------------
rule("CHECKS")
failed = [(label, detail) for ok, label, detail in CHECKS if not ok]
for ok, label, detail in CHECKS:
    print(f"   [{'PASS' if ok else 'FAIL'}] {label}")
    if detail:
        print(f"          {detail}")
print()
print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} checks hold")

print(f"outbound connection attempts during this run: {len(_dial_attempts)} (must be 0)")
if _dial_attempts:
    failed.append(("the run stayed hermetic", f"dial attempts: {_dial_attempts}"))

if failed:
    print()
    print("REALITY HAS CHANGED SINCE THE AUDIT RECORD. Failing checks:")
    for label, detail in failed:
        print(f"   - {label}: {detail}")
    print()
    print("DISPOSITION: RECORD BROKEN. The recorded F09 behavior no longer reproduces as written.")
    sys.exit(1)

print()
print(
    "DISPOSITION: PARTLY VERIFIED. Both halves reproduce (HTTP 200 with lat:null from the real "
    "app; ValueError: cannot convert float NaN to integer kills the real NJT warmup task while "
    "its status stays 'loading'), but Leaflet coerces null to zero only for a null LONGITUDE: a "
    "null LATITUDE yields a null LatLng that throws when projected, and inf raises OverflowError "
    "rather than ValueError."
)
