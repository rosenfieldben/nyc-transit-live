#!/usr/bin/env python3
"""Capture NYC Ferry realtime golden fixtures (backend/tests/fixtures/ferry_*).

The ferry realtime decode (14b) has behaviors no synthetic fixture can prove we
handle against the REAL feed, so the golden tests need a captured set:

  - The trip descriptors carry an EMPTY route_id, so a boat's route is recovered
    only by joining trip_id through 14a's static trip -> route map. The golden
    pins that the join resolves against a REAL captured pair.
  - Boats carry live GPS that advances between polls; a second VehiclePositions
    snapshot ~60s later lets the manual eyeball confirm the boats actually moved.
  - Docks report BOTH arrival and departure (a dwell); the golden pins that both
    survive the decode.

This script captures, into backend/tests/fixtures/ following the path fixture
naming:

  - ferry_vp_a.pb / ferry_vp_b.pb: two VehiclePositions snapshots ~60s apart.
  - ferry_tu_a.pb: a TripUpdates snapshot taken with ferry_vp_a.
  - ferry_rt_static.json: the trip -> route join inputs captured in the SAME
    session ({trips, routes} from load_ferry_static()), so the decode goldens
    are network-free, mirroring path_stops.json.
  - ferry_rt_expected.json: the decoder's expected output for the a-pair
    ({now, feed_timestamp, boats, arrivals}, now frozen to ferry_vp_a's header
    timestamp), to be verified MANUALLY against the printed counts and sample
    rows before committing, per house rules.

SERVICE HOURS: NYC Ferry runs roughly 06:00-22:30 ET and the feeds return zero
entities overnight (with fresh headers). A golden captured empty would assert
nothing, so this script REFUSES to run outside a 06:00-22:30 ET window (the
rush-mode precedent in gen_path_rt_fixture.py), naming the reason.

Run from the repo root during service hours:
  python backend/scripts/gen_ferry_rt_fixture.py
Requires egress to nycferry.connexionz.net.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))  # import the app modules directly

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import feeds  # noqa: E402
import ferry_static  # noqa: E402

FIXTURES = REPO_ROOT / "backend" / "tests" / "fixtures"

# The two realtime endpoints (same host/path base as the decoder). https first,
# http fallback, mirroring feeds.ferry._fetch_ferry_endpoint.
_HOST = feeds.FERRY_RT_HOST
VP_PATHS = (f"https://{_HOST}/vehicleposition", f"http://{_HOST}/vehicleposition")
TU_PATHS = (f"https://{_HOST}/tripupdate", f"http://{_HOST}/tripupdate")

# NYC Ferry daily service window in ET; a capture outside it would be empty.
SERVICE_START = dt_time(6, 0)
SERVICE_END = dt_time(22, 30)

# Seconds between the two VehiclePositions snapshots, long enough for a moving
# boat to visibly advance (every observed boat moved over ~75s in the probe).
SECOND_SNAPSHOT_DELAY_S = 60


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _poll(paths: tuple[str, ...], ctx: ssl.SSLContext) -> bytes:
    """GET the first of `paths` (https, then http) that succeeds; raise the last
    error if all fail. The decoder falls back the same way for the same host."""
    last_exc: Exception | None = None
    for url in paths:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nyc-transit-live"})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:  # noqa: S310
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - try the next scheme, keep the first error
            last_exc = last_exc or exc
    assert last_exc is not None
    raise last_exc


def _service_window_open() -> bool:
    now = datetime.now(ZoneInfo("America/New_York")).time()
    return SERVICE_START <= now <= SERVICE_END


def _entity_counts(raw: bytes) -> tuple[int, int]:
    """(vehicle entities, trip_update entities) in a feed, for the log."""
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    vehicles = sum(1 for e in feed.entity if e.HasField("vehicle"))
    trip_updates = sum(1 for e in feed.entity if e.HasField("trip_update"))
    return vehicles, trip_updates


def _positions_by_id(raw: bytes) -> dict[str, tuple[float, float]]:
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    out: dict[str, tuple[float, float]] = {}
    for e in feed.entity:
        if e.HasField("vehicle") and e.vehicle.HasField("position"):
            vid = e.vehicle.vehicle.id or e.id
            out[vid] = (e.vehicle.position.latitude, e.vehicle.position.longitude)
    return out


def main() -> int:
    if not _service_window_open():
        print(
            "REFUSED: NYC Ferry realtime returns zero entities outside the roughly "
            "06:00-22:30 America/New_York service window, so a capture now would freeze "
            "an EMPTY golden that asserts nothing. Re-run during service hours."
        )
        return 1

    FIXTURES.mkdir(parents=True, exist_ok=True)
    ctx = _ssl_context()

    print("Loading the static trip -> route map (load_ferry_static) ...")
    static = asyncio.run(ferry_static.load_ferry_static())
    trips = static.get("trips") or {}
    routes = static.get("routes") or {}
    if not trips or not routes:
        print("FAILED: load_ferry_static returned no trips/routes; cannot freeze the join inputs.")
        return 1

    print("Capturing ferry_vp_a + ferry_tu_a (simultaneous) ...")
    vp_a = _poll(VP_PATHS, ctx)
    tu_a = _poll(TU_PATHS, ctx)
    vp_a_vehicles, _ = _entity_counts(vp_a)
    _, tu_a_trips = _entity_counts(tu_a)
    if vp_a_vehicles == 0 or tu_a_trips == 0:
        print(
            f"FAILED: captured {vp_a_vehicles} vehicles / {tu_a_trips} trip updates. The window "
            "says in-service but the feed is empty; re-run (a between-sailings lull is possible)."
        )
        return 1

    print(f"Waiting {SECOND_SNAPSHOT_DELAY_S}s for the second VehiclePositions snapshot ...")
    time.sleep(SECOND_SNAPSHOT_DELAY_S)
    vp_b = _poll(VP_PATHS, ctx)

    # Freeze `now` to ferry_vp_a's header timestamp, the same freeze the path/
    # railroad goldens use, so the decode is deterministic.
    feed_a = pb.FeedMessage()
    feed_a.ParseFromString(vp_a)
    now = float(feed_a.header.timestamp)

    boats, feed_ts, deadheads, join_misses = feeds._decode_ferry_vehicles(vp_a, trips, routes, now)
    arrivals, arr_deadheads, arr_join_misses = feeds._decode_ferry_arrivals(
        tu_a, trips, routes, now
    )
    if not boats:
        print("FAILED: ferry_vp_a decoded zero boats; re-run during active service.")
        return 1
    if join_misses:
        print(
            f"FAILED: {join_misses} boats did not join the static route map captured this "
            "session; the static and realtime disagree right now. Re-run (the static may be "
            "mid-refresh) rather than committing a fixture that fails its own join golden."
        )
        return 1

    (FIXTURES / "ferry_vp_a.pb").write_bytes(vp_a)
    (FIXTURES / "ferry_vp_b.pb").write_bytes(vp_b)
    (FIXTURES / "ferry_tu_a.pb").write_bytes(tu_a)
    (FIXTURES / "ferry_rt_static.json").write_text(
        json.dumps({"trips": trips, "routes": routes}, indent=0, sort_keys=True)
    )
    expected = {"now": now, "feed_timestamp": feed_ts, "boats": boats, "arrivals": arrivals}
    (FIXTURES / "ferry_rt_expected.json").write_text(json.dumps(expected, indent=0, sort_keys=True))
    print(
        "\nwrote ferry_vp_a.pb / ferry_vp_b.pb / ferry_tu_a.pb, ferry_rt_static.json, "
        "ferry_rt_expected.json"
    )

    # Manual verification block (eyeball before committing, per house rules).
    print("\n" + "=" * 72)
    print("MANUAL VERIFICATION (eyeball before committing, per house rules)")
    print("=" * 72)
    pos_a = _positions_by_id(vp_a)
    pos_b = _positions_by_id(vp_b)
    moved = sum(1 for vid, p in pos_a.items() if vid in pos_b and pos_b[vid] != p)
    joined = len(trips)
    print(f"static join inputs: {joined} trips, {len(routes)} routes")
    print(
        f"ferry_vp_a: {vp_a_vehicles} vehicle entities -> {len(boats)} decoded boats "
        f"(deadheads dropped: {deadheads}, join misses: {join_misses})"
    )
    print(
        f"ferry_tu_a: {tu_a_trips} trip updates -> {len(arrivals)} docks with arrivals "
        f"(deadheads dropped: {arr_deadheads}, join misses: {arr_join_misses})"
    )
    print(f"boats moved between vp_a and vp_b (~{SECOND_SNAPSHOT_DELAY_S}s): {moved}/{len(pos_a)}")
    statuses: dict[str, int] = {}
    for b in boats:
        statuses[b["status"] or "(none)"] = statuses.get(b["status"] or "(none)", 0) + 1
    print(f"boat statuses: {statuses}")
    for b in boats[:3]:
        print(
            f"  sample boat: {b['label']} route {b['route_id']} status {b['status']} "
            f"speed {b['speed']} at ({b['latitude']:.4f}, {b['longitude']:.4f})"
        )
    # A multi-route dock (more than one route bucket) is the best arrivals sample:
    # it shows the by-route bucketing AND the dock dwell (arrival + departure).
    multi = sorted(arrivals, key=lambda s: len(arrivals[s]), reverse=True)
    for sid in multi[:2]:
        print(f"  sample arrivals at dock {sid} ({len(arrivals[sid])} route bucket(s)):")
        for route_name, rows in arrivals[sid].items():
            first = rows[0]
            print(
                f"    {route_name}: trip {first['trip_id']} arrive {first['arrival']} "
                f"depart {first['departure']} ({len(rows)} upcoming)"
            )
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(f"unknown argument {sys.argv[1]!r}; usage: gen_ferry_rt_fixture.py")
        sys.exit(2)
    sys.exit(main())
