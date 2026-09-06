#!/usr/bin/env python3
"""F03 (P1) "Station countdowns lose upstream content freshness" : reproduction.

THE AUDIT'S CLAIM (section 1 of docs/reviews/audit-2026-09-05.md, finding F03):

    "A valid subway feed with a header ten minutes old and a prediction two
    minutes ahead was passed through the real decoder, refresh path and endpoint.
    The vehicle response exposed approximately 600 seconds of content lag. The
    station-arrivals response instead exposed a newly stamped `fetched_at`, the
    prediction, and no content-age field. The popup and station panel derive
    freshness from that acquisition timestamp. The existing per-contributing-group
    logic correctly handles failed fetches. It does not handle repeatedly
    successful retrieval of old information. Similar arrivals envelopes for other
    modes also omit content freshness."

    Cited: backend/routes/subway.py L85 (arrivals response), backend/models.py
    L191 (arrival model), frontend/helpers.js L713 (popup age calculation).

RUN IT (from the repository root):

    .venv/bin/python docs/reviews/audit-2026-09-05/f03_arrivals_content_freshness.py

WHAT IS PRODUCTION CODE HERE (nothing below is re-implemented):

  * feeds.subway._decode_feed / _aggregate_feeds / fetch_subway_trains: the real
    GTFS-RT decoder and fan-out, reached through main.fetch_subway_trains.
  * pollers._refresh_subways: the real refresh path, including the C2 per-group
    merge, drop_expired_arrivals, combine_group_arrivals and _system_freshness.
  * main.app driven over httpx.ASGITransport: GET /api/subways and
    GET /api/subway-arrivals/219 are the real routed handlers
    (routes/subway.py) and the real response models (models.py), so every
    number below is read out of SERVED JSON, not out of a Python call.
  * frontend/helpers.js required into node: the real feedAgeLine, staleAge and
    shapeStationArrivals (the popup and the station panel respectively), plus
    frontend/systems/subway.js loaded into a node:vm to call the real
    subwayArrivalsHtml.
  * backend/models.py imported for the arrivals-model enumeration; the model
    -> endpoint map is read out of backend/routes/*.py source.

WHAT IS INJECTED (the fault, stated plainly):

  * The feed bytes are the COMMITTED capture backend/tests/fixtures/subway_1_7_s.pb
    (259 entities, real MTA content) with ONE constant integer delta added to
    every timestamp it carries: the feed header, each trip_update.timestamp, each
    stop_time_update arrival/departure time and any vehicle timestamp. The delta
    is chosen so the capture's header lands exactly 600 s before this run's poll
    clock. No other field is touched, so the capture's internal structure (which
    prediction is how far ahead of its own header) is preserved exactly: the
    "prediction two minutes ahead" is the capture's own stop at header + 720 s,
    not a hand-written number. trip.start_date is deliberately left as captured;
    it is read only by the placement pass's not-yet-started filter, and a past
    start date never trips it.
  * 600 s is therefore the INJECTED CONDITION, not a measurement. What is
    measured is which served responses expose it and which do not.
  * The seven other subway feed groups are served a valid header-only feed
    stamped with the current clock, so the poll is a fully successful, fully
    healthy eight-group poll: no failure anywhere.
  * The static station index is derived from the committed platform-stops
    fixture subway_1_7_s_stops.json using the production
    feeds.subway._platform_direction (the real GTFS zip is not committed). The
    arrivals index itself is built entirely by the production decoder.

  No network is used. No NJ Transit host is contacted and no token is minted.
  Ages are measured against the capture's OWN header, never against today's date.

WHAT IT MEASURES

  a) content lag on /api/subways: fetched_at - feed_timestamp.
  b) the served key sets of /api/subways and /api/subway-arrivals/219, side by
     side, and whether any arrivals key can carry a content clock.
  c) what the real frontend does with the arrivals body: feedAgeLine,
     subwayArrivalsHtml and shapeStationArrivals against the served JSON.
  d) the per-group health block during that same poll (ok / fetched_at), and a
     SECOND successful poll of the same old bytes, to show the arrivals clock
     advancing while the content clock stands still.
  e) every arrivals response model in backend/models.py, per mode, and whether it
     carries any content-age or source-content-time field.

EXIT STATUS: 0 while the finding still behaves as recorded (DISPOSITION:
VERIFIED); non-zero with a named failure when reality has changed.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FIXTURES = BACKEND / "tests" / "fixtures"
FRONTEND = REPO / "frontend"
# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT. env_seams calls load_dotenv
# when it is imported, so a credential scrub that runs before that import is undone by
# it; the addresses set here are what make this process unable to reach NJ Transit at
# all. See _hermetic for the leak this closes and the measurement behind it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import main  # noqa: E402
import models  # noqa: E402
import pollers  # noqa: E402
from feeds.subway import SUBWAY_FEED_URLS, _platform_direction  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()


CAPTURE = FIXTURES / "subway_1_7_s.pb"
STOPS_FIXTURE = FIXTURES / "subway_1_7_s_stops.json"

INJECTED_LAG_S = 600.0  # "a header ten minutes old"
STATION_ID = "219"  # Prospect Av (2/5), reached by the 1-7+S capture
DIRECTION = "Northbound"
FEED_GROUP = "1-7+S"  # the group whose URL is served the old capture

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    """Record a named assertion; the script exits non-zero if any one fails."""
    if not ok:
        failures.append(f"{label}{(': ' + detail) if detail else ''}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def stamp(epoch: float | None) -> str:
    if epoch is None:
        return "None"
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


# ---------------------------------------------------------------------------
# The injected feed bytes: the committed capture, time-shifted by one constant.
# ---------------------------------------------------------------------------


def shift_capture(base_now: float) -> tuple[bytes, int, int, int]:
    """Committed capture with every timestamp shifted so its header sits exactly
    INJECTED_LAG_S before base_now. Returns (bytes, original header, new header,
    delta)."""
    feed = pb.FeedMessage()
    feed.ParseFromString(CAPTURE.read_bytes())
    original_header = int(feed.header.timestamp)
    delta = int(round(base_now - INJECTED_LAG_S)) - original_header
    feed.header.timestamp = original_header + delta
    for entity in feed.entity:
        if entity.HasField("trip_update"):
            tu = entity.trip_update
            if tu.timestamp:
                tu.timestamp += delta
            for stu in tu.stop_time_update:
                for field in ("arrival", "departure"):
                    if stu.HasField(field):
                        event = getattr(stu, field)
                        if event.HasField("time") and event.time:
                            event.time += delta
        if entity.HasField("vehicle") and entity.vehicle.timestamp:
            entity.vehicle.timestamp += delta
    return feed.SerializeToString(), original_header, int(feed.header.timestamp), delta


def quiet_feed(timestamp: int) -> bytes:
    """A valid, fresh, EMPTY feed for the other seven groups: a real header (the
    version field parse_feed requires) and no entities. Decodes as healthy."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "1.0"
    feed.header.timestamp = timestamp
    return feed.SerializeToString()


class OneOldGroupClient:
    """Stands in for httpx.AsyncClient inside the real fetch_subway_trains: the
    old capture on the 1-7+S URL, a fresh empty feed on the other seven. Returns
    real httpx.Response objects so raise_for_status/.content behave as in prod."""

    def __init__(self, old_bytes: bytes, fresh_bytes: bytes) -> None:
        self.old_url = SUBWAY_FEED_URLS[FEED_GROUP]
        self.old_bytes = old_bytes
        self.fresh_bytes = fresh_bytes
        self.calls: list[str] = []

    async def get(self, url: str) -> httpx.Response:
        self.calls.append(url)
        body = self.old_bytes if url == self.old_url else self.fresh_bytes
        return httpx.Response(200, content=body, request=httpx.Request("GET", url))


def prime_app() -> None:
    """The app.state a lifespan would have built, minus anything that needs the
    network: the committed platform stops, and the parent-station index derived
    from them with the production _platform_direction."""
    app = main.app
    app.state.feed_cache = {name: main._fresh_entry() for name in ("subways",)}
    app.state.subway_feed_health = None
    app.state.subway_static_status = "ready"
    stops = json.loads(STOPS_FIXTURE.read_text())
    app.state.subway_stops = stops
    stations: dict[str, dict] = {}
    for stop_id, stop in stops.items():
        _, station_id = _platform_direction(stop_id)
        stations.setdefault(
            station_id, {"name": stop["name"], "lat": stop["lat"], "lon": stop["lon"]}
        )
    app.state.subway_stations = stations
    app.state.subway_station_routes = {}
    app.state.subway_arrivals = {}
    app.state.subway_arrivals_by_system = {}
    app.state.subway_positions = {}
    return stations


# ---------------------------------------------------------------------------
# (c) the frontend, executed rather than read
# ---------------------------------------------------------------------------

NODE_DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const helpers = require(process.argv[2]);
const systemsSubwaySrc = fs.readFileSync(process.argv[3], "utf8");
const input = JSON.parse(fs.readFileSync(process.argv[4], "utf8"));

// systems/subway.js is a plain browser script: at load it calls L.canvas() and
// pushes onto systems/shared.js's staleTreatments array, so the vm context gets a
// Leaflet stub and that array alongside the real helpers as globals. Nothing
// about feedAgeLine or subwayArrivalsHtml is re-implemented here.
const stub = () => ({ addTo: () => ({}), bindPopup: () => ({ addTo: () => ({}) }) });
const ctx = Object.assign({}, helpers, {
  console,
  L: { canvas: stub, divIcon: stub, polyline: stub, marker: stub, layerGroup: stub },
  staleTreatments: [],
  minClockOffset: 0,
});
vm.createContext(ctx);
vm.runInContext(systemsSubwaySrc, ctx, { filename: "systems/subway.js" });

const now = input.now;
// subwayArrivalsHtml reads its clock as Date.now()/1000 - minClockOffset, so the
// offset is what pins it to the instant the response was served.
ctx.minClockOffset = Date.now() / 1000 - now;
const popupHtml = ctx.subwayArrivalsHtml(
  { id: input.arrivals.station_id, name: input.arrivals.station_name },
  input.arrivals,
);
ctx.minClockOffset = 0;

const shaped = helpers.shapeStationArrivals("subway", input.arrivals, now, {});
out = {
  feedStaleAfterS: helpers.FEED_STALE_AFTER_S,
  now,
  popupLineFromFetchedAt: helpers.feedAgeLine(input.arrivals.fetched_at, now),
  popupLineFromContentClock: helpers.feedAgeLine(input.feed_timestamp, now),
  popupAgeFromFetchedAt: now - input.arrivals.fetched_at,
  popupAgeFromContentClock: now - input.feed_timestamp,
  popupSaysStale: /popup-stale/.test(popupHtml),
  popupHtmlHead: popupHtml.slice(0, 160),
  panelAgeSeconds: shaped.ageSeconds,
  panelStaleLine: helpers.staleAge(shaped.ageSeconds)
    ? `as of ${helpers.humanizeAge(shaped.ageSeconds)} ago`
    : null,
  panelFirstRow: shaped.buckets.length ? shaped.buckets[0].rows[0] : null,
};
process.stdout.write(JSON.stringify(out));
"""


def run_frontend(arrivals_body: dict, feed_timestamp: float, now: float) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        driver = tmpdir / "f03_frontend.js"
        driver.write_text(NODE_DRIVER)
        payload = tmpdir / "payload.json"
        payload.write_text(
            json.dumps({"arrivals": arrivals_body, "feed_timestamp": feed_timestamp, "now": now})
        )
        proc = subprocess.run(
            [
                "node",
                str(driver),
                str(FRONTEND / "helpers.js"),
                str(FRONTEND / "systems" / "subway.js"),
                str(payload),
            ],
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        raise SystemExit(f"node driver failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# (e) every arrivals response model, per mode
# ---------------------------------------------------------------------------

# Any field that would let a client date the CONTENT rather than the fetch.
CONTENT_CLOCK_NAMES = {
    "feed_timestamp",
    "served_at",
    "content_age",
    "content_time",
    "source_time",
    "source_content_time",
    "generated_at",
    "observed_at",
    "updated_at",
}

MODE_OF_MODEL = {
    "StationArrivals": "Subway",
    "RailroadStationArrivals": "LIRR / Metro-North",
    "PathStationArrivals": "PATH",
    "NjtStationArrivals": "NJ Transit Rail",
    "FerryStationArrivals": "NYC Ferry",
}

ROUTE_DECORATOR_RE = re.compile(
    r'@router\.get\(\s*"(?P<path>[^"]+)"\s*,\s*response_model=(?P<model>\w+)\s*\)'
)


def arrivals_model_table() -> list[dict]:
    endpoints: dict[str, str] = {}
    for source in sorted((BACKEND / "routes").glob("*.py")):
        for match in ROUTE_DECORATOR_RE.finditer(source.read_text()):
            endpoints.setdefault(match.group("model"), match.group("path"))
    rows = []
    for name, obj in vars(models).items():
        if not (isinstance(obj, type) and name.endswith("StationArrivals")):
            continue
        fields = list(obj.model_fields)
        rows.append(
            {
                "model": name,
                "mode": MODE_OF_MODEL.get(name, "?"),
                "endpoint": endpoints.get(name, "(none)"),
                "fields": fields,
                "content_clock": sorted(set(fields) & CONTENT_CLOCK_NAMES),
            }
        )
    rows.sort(key=lambda r: r["mode"])
    return rows


def feed_model_table() -> list[dict]:
    rows = []
    for name in ("SubwayFeed", "RailroadFeed", "PathFeed", "NjtFeed", "FerryFeed", "BusFeed"):
        fields = list(getattr(models, name).model_fields)
        rows.append(
            {
                "model": name,
                "fields": fields,
                "content_clock": sorted(set(fields) & CONTENT_CLOCK_NAMES),
            }
        )
    return rows


# ---------------------------------------------------------------------------


async def poll_and_serve(client: OneOldGroupClient) -> tuple[dict, dict, float]:
    """One real refresh, then the two real endpoints. Returns
    (vehicles JSON, arrivals JSON, the poll's fetched_at)."""
    await pollers._refresh_subways(main.app, client)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://f03") as http:
        vehicles = await http.get("/api/subways")
        arrivals = await http.get("/api/subway-arrivals/" + STATION_ID)
    if vehicles.status_code != 200 or arrivals.status_code != 200:
        raise SystemExit(
            f"endpoints did not serve 200: /api/subways={vehicles.status_code} "
            f"/api/subway-arrivals/{STATION_ID}={arrivals.status_code}"
        )
    body = vehicles.json()
    return body, arrivals.json(), body["fetched_at"]


async def main_async() -> None:
    stations = prime_app()

    base_now = time.time()
    old_bytes, original_header, new_header, delta = shift_capture(base_now)
    fresh_bytes = quiet_feed(int(base_now))
    client = OneOldGroupClient(old_bytes, fresh_bytes)

    rule("SETUP: the injected feed (committed capture, one constant time shift)")
    print(f"  capture                : {CAPTURE.relative_to(REPO)} ({CAPTURE.stat().st_size} bytes)")
    print(f"  capture header (as committed) : {original_header}  {stamp(original_header)}")
    print(f"  constant delta applied        : {delta:+d} s (added to every timestamp in the feed)")
    print(f"  shifted header                : {new_header}  {stamp(new_header)}")
    print(f"  injected content lag          : {INJECTED_LAG_S:.0f} s before this run's poll clock")
    print(f"  station under test            : {STATION_ID} "
          f"({stations[STATION_ID]['name']}), {DIRECTION}")
    print(f"  other 7 feed groups           : valid header-only feed stamped {int(base_now)} "
          f"(fresh, healthy, empty)")

    # ---- poll 1 -----------------------------------------------------------
    vehicles, arrivals, fetched_at_1 = await poll_and_serve(client)
    lag_1 = vehicles["fetched_at"] - vehicles["feed_timestamp"]

    rule("(a) SERVED /api/subways : the content lag IS exposed there")
    print(f"  feed_timestamp (upstream content clock) : {vehicles['feed_timestamp']:.3f}"
          f"  {stamp(vehicles['feed_timestamp'])}")
    print(f"  fetched_at     (our poll clock)         : {vehicles['fetched_at']:.3f}"
          f"  {stamp(vehicles['fetched_at'])}")
    print(f"  served_at      (this response)          : {vehicles['served_at']:.3f}")
    print(f"  CONTENT LAG    = fetched_at - feed_timestamp = "
          f"{lag_1:.3f} s  ({lag_1 / 60:.2f} min)")
    print(f"  backend FEED_STALE_AFTER_S              : {main.FEED_STALE_AFTER_S} s"
          f"  -> lag exceeds it by {lag_1 - main.FEED_STALE_AFTER_S:.1f} s")
    print(f"  trains served                           : {len(vehicles['data'])}")
    check(
        abs(lag_1 - INJECTED_LAG_S) < 3.0,
        "the vehicles envelope exposes ~600 s of content lag",
        f"{lag_1:.3f} s",
    )
    check(
        lag_1 > main.FEED_STALE_AFTER_S,
        "that lag is past the backend staleness threshold",
        f"{lag_1:.1f} s > {main.FEED_STALE_AFTER_S} s",
    )

    rule("(b) SERVED /api/subway-arrivals/%s : the same poll, no content clock" % STATION_ID)
    vehicle_keys = sorted(vehicles)
    arrivals_keys = sorted(arrivals)
    width = max(len(k) for k in vehicle_keys + arrivals_keys) + 2
    print(f"  {'GET /api/subways'.ljust(width + 26)}GET /api/subway-arrivals/{STATION_ID}")
    print(f"  {'-' * (width + 24)}  {'-' * 34}")
    for left, right in zip(
        vehicle_keys + [""] * len(arrivals_keys), arrivals_keys + [""] * len(vehicle_keys)
    ):
        if not left and not right:
            continue
        left_note = ""
        if left in ("feed_timestamp", "served_at"):
            left_note = "  <- content / serve clock"
        right_note = "  <- the ONLY clock" if right == "fetched_at" else ""
        print(f"  {(left + left_note).ljust(width + 24)}  {right + right_note}")
    print()
    print(f"  arrivals.fetched_at        : {arrivals['fetched_at']:.3f}  {stamp(arrivals['fetched_at'])}")
    print(f"  the content it describes   : {vehicles['feed_timestamp']:.3f}"
          f"  {stamp(vehicles['feed_timestamp'])}")
    print(f"  arrivals clock is NEWER than its own content by "
          f"{arrivals['fetched_at'] - vehicles['feed_timestamp']:.3f} s")

    rows = arrivals["directions"][DIRECTION]
    soonest = rows[0]["arrival"]
    print(f"  {DIRECTION} rows served     : {len(rows)}; soonest trip "
          f"{soonest:.0f} ({stamp(soonest)}) route {rows[0]['route_id']}")
    print(f"  soonest arrival is         : {soonest - arrivals['fetched_at']:+.1f} s from "
          f"the arrivals fetched_at  ({(soonest - arrivals['fetched_at']) / 60:.2f} min ahead)")
    print(f"  arrival row keys           : {sorted(rows[0])}")

    check(
        set(arrivals) == {"fetched_at", "station_id", "station_name", "directions"},
        "the arrivals envelope serves exactly 4 keys",
        ", ".join(arrivals_keys),
    )
    check(
        not (set(arrivals) & CONTENT_CLOCK_NAMES),
        "no content-age / source-content-time key in the arrivals envelope",
        "checked against " + ", ".join(sorted(CONTENT_CLOCK_NAMES)),
    )
    check(
        not (set(rows[0]) & CONTENT_CLOCK_NAMES),
        "no content clock on the individual arrival rows either",
        ", ".join(sorted(rows[0])),
    )
    check(
        abs(arrivals["fetched_at"] - fetched_at_1) < 0.001,
        "the arrivals fetched_at is this poll's freshly stamped clock",
        f"{arrivals['fetched_at']:.3f} == poll {fetched_at_1:.3f}",
    )
    check(
        115.0 <= soonest - arrivals["fetched_at"] <= 122.0,
        "the served prediction is ~2 minutes ahead (the capture's own header+720 s stop)",
        f"{soonest - arrivals['fetched_at']:.1f} s ahead",
    )
    check(
        "feed_timestamp" in vehicles and "feed_timestamp" not in arrivals,
        "the content clock is on the vehicles envelope and absent from the arrivals one",
    )

    rule("(d) THE PER-GROUP HEALTH BLOCK during that same poll")
    health = main.app.state.subway_feed_health
    print(f"  subway_feed_health : total={health['total']} ok={health['ok']} "
          f"failed={health['failed']}")
    print(f"  {'group':<8} {'ok':<6} {'fetched_at':<16} {'retained_since':<15} routes")
    for group, block in sorted(vehicles["systems"].items()):
        marker = "  <- serving the 600 s old capture" if group == FEED_GROUP else ""
        print(f"  {group:<8} {str(block['ok']):<6} {block['fetched_at']:<16.3f} "
              f"{str(block['retained_since']):<15} {block['routes']}{marker}")
    print(f"  SystemFreshness fields : {list(models.SystemFreshness.model_fields)}")
    check(
        all(block["ok"] for block in vehicles["systems"].values()),
        "every one of the 8 groups reports ok=True while one serves 600 s old content",
        f"{sum(1 for b in vehicles['systems'].values() if b['ok'])}/8 ok",
    )
    check(
        health["failed"] == [] and health["ok"] == health["total"],
        "operational feed health is fully green during the same poll",
        f"ok={health['ok']}/{health['total']}",
    )
    check(
        abs(vehicles["systems"][FEED_GROUP]["fetched_at"] - fetched_at_1) < 0.001,
        f"the {FEED_GROUP} group's own fetched_at is stamped now, not its content time",
        f"{vehicles['systems'][FEED_GROUP]['fetched_at']:.3f}",
    )
    check(
        not (set(models.SystemFreshness.model_fields) & CONTENT_CLOCK_NAMES),
        "SystemFreshness carries no per-group content clock either",
    )

    # ---- poll 2: the SAME old bytes retrieved successfully again ----------
    await asyncio.sleep(1.0)
    vehicles_2, arrivals_2, fetched_at_2 = await poll_and_serve(client)
    lag_2 = vehicles_2["fetched_at"] - vehicles_2["feed_timestamp"]

    rule("(d) A SECOND SUCCESSFUL POLL OF THE SAME OLD BYTES")
    print(f"  upstream fetches issued        : {len(client.calls)} (2 polls x 8 groups)")
    print(f"  feed_timestamp poll 1 -> 2     : {vehicles['feed_timestamp']:.3f} -> "
          f"{vehicles_2['feed_timestamp']:.3f}   (delta "
          f"{vehicles_2['feed_timestamp'] - vehicles['feed_timestamp']:+.3f} s: frozen)")
    print(f"  arrivals fetched_at poll 1 -> 2: {arrivals['fetched_at']:.3f} -> "
          f"{arrivals_2['fetched_at']:.3f}   (delta "
          f"{arrivals_2['fetched_at'] - arrivals['fetched_at']:+.3f} s: advancing)")
    print(f"  content lag poll 1 -> 2        : {lag_1:.3f} s -> {lag_2:.3f} s  "
          f"(grew {lag_2 - lag_1:+.3f} s)")
    print(f"  arrivals rows still served     : "
          f"{len(arrivals_2['directions'][DIRECTION])} {DIRECTION}")
    check(
        vehicles_2["feed_timestamp"] == vehicles["feed_timestamp"],
        "the content clock did not move between the two successful polls",
    )
    check(
        fetched_at_2 - fetched_at_1 >= 0.9,
        "the arrivals clock advanced anyway on the second successful poll",
        f"+{fetched_at_2 - fetched_at_1:.3f} s",
    )
    check(
        lag_2 > lag_1 and all(b["ok"] for b in vehicles_2["systems"].values()),
        "lag grows while every group still reports ok",
        f"{lag_1:.1f} -> {lag_2:.1f} s, 8/8 ok",
    )

    # ---- (c) the frontend -------------------------------------------------
    now_client = fetched_at_2 + 0.5  # a browser reading the response it just got
    fe = run_frontend(arrivals_2, vehicles_2["feed_timestamp"], now_client)

    rule("(c) THE REAL FRONTEND, executed against the served arrivals JSON")
    print(f"  helpers.js FEED_STALE_AFTER_S            : {fe['feedStaleAfterS']} s")
    print(f"  age from body.fetched_at (what it uses)  : {fe['popupAgeFromFetchedAt']:.3f} s")
    print(f"  age from the content clock (unavailable) : {fe['popupAgeFromContentClock']:.3f} s")
    print(f"  feedAgeLine(body.fetched_at, now)        : "
          f"{fe['popupLineFromFetchedAt']!r}   <- what riders get")
    print(f"  feedAgeLine(feed_timestamp, now)         : "
          f"{fe['popupLineFromContentClock']!r}   <- what the content clock would say")
    print(f"  subwayArrivalsHtml contains popup-stale  : {fe['popupSaysStale']}")
    print(f"  popup head                               : {fe['popupHtmlHead']!r}")
    print(f"  shapeStationArrivals().ageSeconds (panel): {fe['panelAgeSeconds']:.3f} s")
    print(f"  station panel stale line                 : {fe['panelStaleLine']!r}")
    print(f"  station panel first row                  : {fe['panelFirstRow']}")
    check(
        fe["popupLineFromFetchedAt"] == "",
        "helpers.feedAgeLine returns NO stale line for the 600 s old content",
    )
    check(
        "popup-stale" in fe["popupLineFromContentClock"]
        and "10m" in fe["popupLineFromContentClock"],
        "the same helper WOULD say 'as of 10m ago' if handed the content clock",
        fe["popupLineFromContentClock"],
    )
    check(
        fe["popupSaysStale"] is False,
        "the real subwayArrivalsHtml popup renders with no staleness qualifier",
    )
    check(
        fe["panelAgeSeconds"] < fe["feedStaleAfterS"] and fe["panelStaleLine"] is None,
        "the station panel (shapeStationArrivals) also reads fresh",
        f"ageSeconds={fe['panelAgeSeconds']:.1f} < {fe['feedStaleAfterS']}",
    )
    call_site = (FRONTEND / "systems" / "subway.js").read_text()
    check(
        "feedAgeLine(body.fetched_at, now)" in call_site,
        "the popup call site passes body.fetched_at, not a content clock",
    )
    check(
        "payload.fetched_at == null ? null : now - payload.fetched_at"
        in (FRONTEND / "helpers.js").read_text(),
        "the panel's ageSeconds is defined as now - fetched_at",
    )

    rule("(c) CITED LINES, confirmed against the audited files")
    cites = [
        ("backend/routes/subway.py", 85, "THE OLDEST CONTRIBUTING GROUP'S poll time"),
        ("backend/models.py", 191, "class StationArrivals(BaseModel):"),
        ("frontend/helpers.js", 713, 'The "as of Xm ago" age line'),
    ]
    for rel, line_no, needle in cites:
        text = (REPO / rel).read_text().splitlines()[line_no - 1]
        print(f"  {rel}:{line_no}  {text.strip()[:70]}")
        check(needle in text, f"{rel}:{line_no} is the cited construct")

    # ---- (e) every arrivals model ----------------------------------------
    rule("(e) EVERY ARRIVALS RESPONSE MODEL IN backend/models.py, PER MODE")
    table = arrivals_model_table()
    print(f"  {'mode':<20} {'model':<26} {'endpoint':<40} content clock?")
    print(f"  {'-' * 20} {'-' * 26} {'-' * 40} {'-' * 14}")
    for row in table:
        verdict = ", ".join(row["content_clock"]) if row["content_clock"] else "NONE"
        print(f"  {row['mode']:<20} {row['model']:<26} {row['endpoint']:<40} {verdict}")
    print()
    for row in table:
        print(f"  {row['model']:<26} fields: {row['fields']}")
    print()
    print("  For contrast, the VEHICLE/BOAT envelopes of the same modes:")
    for row in feed_model_table():
        print(f"  {row['model']:<26} content clock: {', '.join(row['content_clock']) or 'NONE'}")
    print("  (AirTrain JFK has no arrivals endpoint: it is static-only reference data.)")

    check(len(table) == 5, "five arrivals response models found", f"{len(table)}")
    check(
        all(row["endpoint"] != "(none)" for row in table),
        "every arrivals model is served by a real endpoint",
    )
    without = [r["mode"] for r in table if not r["content_clock"]]
    with_clock = [r["mode"] for r in table if r["content_clock"]]
    check(
        not with_clock,
        "NO arrivals model of ANY mode carries a content-age field",
        f"without={without}; with={with_clock or 'none'}",
    )
    check(
        all(row["content_clock"] for row in feed_model_table()),
        "while every vehicle/boat envelope does carry one",
    )

    # ---- verdict ----------------------------------------------------------
    rule("MEASURED SUMMARY")
    print(f"  injected header age                     : {INJECTED_LAG_S:.0f} s")
    print(f"  measured lag on /api/subways (poll 1)   : {lag_1:.3f} s")
    print(f"  measured lag on /api/subways (poll 2)   : {lag_2:.3f} s")
    print(f"  lag visible on /api/subway-arrivals     : 0 s (no field can express it)")
    print(f"  arrivals age the frontend computes      : {fe['popupAgeFromFetchedAt']:.3f} s "
          f"(threshold {fe['feedStaleAfterS']} s, so: silent)")
    print(f"  groups reporting ok during all of this  : "
          f"{sum(1 for b in vehicles_2['systems'].values() if b['ok'])}/8")
    print(f"  arrivals models lacking a content clock : {len(without)}/{len(table)}")

    print()
    if failures:
        print("REALITY HAS CHANGED. Failed assertions:")
        for item in failures:
            print(f"  - {item}")
        print("DISPOSITION: NOT AS RECORDED, see the failed assertions above")
        raise SystemExit(1)
    print(
        "DISPOSITION: VERIFIED, a valid subway feed "
        f"{lag_1:.0f}s behind its own header serves /api/subways a "
        f"{lag_1:.0f}s content lag while /api/subway-arrivals/{STATION_ID} serves only a "
        "freshly stamped fetched_at, no content clock, 8/8 groups ok, and all 5 arrivals "
        "models of all 5 modes omit the field."
    )


if __name__ == "__main__":
    asyncio.run(main_async())
