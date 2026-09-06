#!/usr/bin/env python
"""F02 (P1): "Canceled railroad services remain normal GPS markers."

Reproduction and regression check for the 2026-09-05 audit finding F02, recorded
in docs/reviews/audit-2026-09-05.md section 1.

THE AUDIT'S CLAIM, quoted verbatim from that section:

    "The captured LIRR TripUpdate `6004XX_2026-06-20_T` marks its trip
    `CANCELED`, while a separate GPS entity remains present. The application
    emits that trip as route 5, train 508. The canceled train is the first
    record in the expected golden output. Synthetic checks also reproduce the
    issue in the combined TripUpdate/VehiclePosition layout used by Metro-North.
    The existing cancellation filter correctly removes predictions and station
    placement, but runs after GPS records have already been accepted. Thus the
    station board and map can disagree about whether this is a passenger
    service."

RUN (from the repository root):

    .venv/bin/python docs/reviews/audit-2026-09-05/f02_canceled_railroad_gps.py

Exits 0 while the finding still behaves as recorded, non-zero with a message
naming the changed measurement when it does not.

WHAT IS MEASURED, AND HOW

  Panel A  Fixture forensics. The committed capture backend/tests/fixtures/
           railroad_lirr.pb is parsed with the real gtfs_realtime_bindings and
           the CANCELED TripUpdate is matched against the positioned vehicle
           entities by trip_id. Establishes claim (a): the real trip id, the
           CANCELED schedule relationship, and the separate GPS entity.
           (The audit wrote "6004XX" as if the X characters were placeholders.
           They are not: the literal trip id in the capture is
           "6004XX_2026-06-20", TripUpdate entity "6004XX_2026-06-20_T",
           vehicle entity "6004XX_2026-06-20_V".)

  Panel B  Claim (b). Production feeds._decode_railroad_vehicles is run over the
           capture with `now` frozen to that capture's own header timestamp, and
           its output is compared to the committed golden
           backend/tests/fixtures/railroad_lirr_expected.json.

  Panel C  Claim (c) at the decoder. Production feeds._decode_railroad_feed (the
           placement plus arrivals pass) is run over the same bytes and the same
           committed stops, and every emitted arrival row and placed train is
           searched for the trip. A CONTROL then isolates the cause: the same
           capture is re-serialized with ONLY that one entity's
           trip.schedule_relationship changed (to SCHEDULED, to DELETED, and a
           variant where the VEHICLE descriptor rather than the TripUpdate is
           marked CANCELED), and the decode clock is frozen inside the capture's
           own world, just before the trip's two stop times, so the arrivals side
           is not vacuous. Injected: only the schedule_relationship value.

  Panel D  Claim (c) on the rider surfaces. The real FastAPI app is driven: the
           production poller main._refresh_railroads runs against an
           httpx.MockTransport that serves the two committed captures (no socket
           is opened, no NJ Transit host is ever named), then GET /api/railroads
           and GET /api/railroad-arrivals/LIRR/{83,198} are served through
           httpx.ASGITransport. The map endpoint and the station board are read
           from the same poll. The same two runs are made with the captured
           CANCELED bytes and with the SCHEDULED control bytes.

  Panel E  Claim (d). A synthetic feed in Metro-North's COMBINED
           TripUpdate + VehiclePosition entity layout, shaped after the real
           combined entity in railroad_mnr.pb, is built at SCHEDULED, CANCELED
           and DELETED and run through the same two production decoders with the
           committed MNR stops. Injected: the whole feed, which is why the
           SCHEDULED control is built too.

  Panel F  Claim (e). DELETED versus CANCELED on the GPS path, plus the enum
           value the installed binding resolves and the requirements pin that
           the repository added for it.

  Panel G  Whether the emitted GPS record carries ANY marking of cancellation.

PRODUCTION CODE EXERCISED (nothing here re-implements it):
  feeds._decode_railroad_vehicles, feeds._decode_railroad_feed,
  feeds._decode_railroad_placements, feeds.fetch_railroad_trains (through the
  poller), main._refresh_railroads, and the /api/railroads and
  /api/railroad-arrivals routes on main.app.

HERMETIC AND DETERMINISTIC: committed fixtures only, no live feed, no network
transport, no NJ Transit anything, and no dependence on today's date. Every
clock used by the decoders is a constant taken from inside the captures.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT. env_seams calls load_dotenv
# when it is imported, so a credential scrub that runs before that import is undone by
# it; the addresses set here are what make this process unable to reach NJ Transit at
# all. See _hermetic for the leak this closes and the measurement behind it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import feeds  # noqa: E402
import feeds.railroad as railroad_mod  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402
from feeds.shared import _DROP_TRIP_RELATIONSHIPS, NYC_TZ  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()


FIX = ROOT / "backend" / "tests" / "fixtures"
SR = pb.TripDescriptor.ScheduleRelationship

# The trip under test, read out of the committed capture (Panel A re-derives it).
TRIP_ID = "6004XX_2026-06-20"
TU_ENTITY_ID = "6004XX_2026-06-20_T"
VEHICLE_ENTITY_ID = "6004XX_2026-06-20_V"

# Recorded expectations. Each is asserted below; a changed number fails the run
# with a message rather than silently rewriting the audit record.
EXPECT = {
    "lirr_header": 1782006915,
    "lirr_entities": 201,
    "canceled_trip_updates": 8,
    "positioned_trip_ids": 69,
    "gps_trains": 69,
    "golden_records": 69,
    "placed_at_header_now": 56,
    "arrival_stations_at_header_now": 117,
    "arrival_rows_at_header_now": 765,
    "control_rows_canceled": 1005,
    "control_rows_scheduled": 1007,
    "control_own_rows_scheduled": 2,
    "api_railroad_records": 158,
    "api_stop83_rows_captured": 6,
    "api_stop83_rows_control": 7,
    "api_stop198_rows_captured": 6,
    "api_stop198_rows_control": 7,
    "mnr_synth_arrival_rows_scheduled": 3,
    "deleted_enum": 7,
}

# The capture's canceled trip has stop times about 14 hours BEFORE its own feed
# header, so at header time it has no upcoming stop at all and its absence from
# the arrivals board would be over-determined. CONTROL_NOW is a clock taken from
# inside the capture, 622 seconds before that trip's first stop time, so the
# arrivals comparison is about the cancellation filter and nothing else. It is
# never today's wall clock.
CONTROL_NOW = 1781955000.0

failures: list[str] = []


def check(label: str, got, want) -> None:
    """Record one assertion. Everything is checked, then the run fails once."""
    if got != want:
        failures.append(f"{label}: measured {got!r}, audit record says {want!r}")


def banner(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def arrival_rows(arrivals: dict) -> list[tuple[str, str, dict]]:
    """Flatten {stop_id: {bucket: [row]}} to (stop_id, bucket, row) triples."""
    return [
        (stop_id, bucket, row)
        for stop_id, buckets in arrivals.items()
        for bucket, rows in buckets.items()
        for row in rows
    ]


# ---------------------------------------------------------------- fixtures ---

LIRR_RAW = (FIX / "railroad_lirr.pb").read_bytes()
MNR_RAW = (FIX / "railroad_mnr.pb").read_bytes()
LIRR_STOPS = json.loads((FIX / "railroad_lirr_stops.json").read_text())
MNR_STOPS = json.loads((FIX / "railroad_mnr_stops.json").read_text())
GOLDEN = json.loads((FIX / "railroad_lirr_expected.json").read_text())


def flipped(raw: bytes, *, trip_sr=None, vehicle_sr=None) -> bytes:
    """The captured LIRR feed with ONLY the schedule_relationship of the trip
    under test changed. Every other byte of the capture is left alone: this is
    the single injected fault in Panel C."""
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    for entity in feed.entity:
        if trip_sr is not None and entity.HasField("trip_update"):
            if entity.trip_update.trip.trip_id == TRIP_ID:
                entity.trip_update.trip.schedule_relationship = trip_sr
        if vehicle_sr is not None and entity.HasField("vehicle"):
            if entity.vehicle.trip.trip_id == TRIP_ID:
                entity.vehicle.trip.schedule_relationship = vehicle_sr
    return feed.SerializeToString()


# ------------------------------------------------- Panel A: the capture -------

banner("PANEL A: what the committed LIRR capture actually contains (claim a)")

lirr_feed = pb.FeedMessage()
lirr_feed.ParseFromString(LIRR_RAW)
HEADER_NOW = float(lirr_feed.header.timestamp)

canceled_ids = {
    e.trip_update.trip.trip_id
    for e in lirr_feed.entity
    if e.HasField("trip_update")
    and e.trip_update.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS
}
positioned_ids = {
    e.vehicle.trip.trip_id
    for e in lirr_feed.entity
    if e.HasField("vehicle") and e.vehicle.HasField("position")
}
both = sorted(canceled_ids & positioned_ids)

tu_entity = next(e for e in lirr_feed.entity if e.id == TU_ENTITY_ID)
veh_entity = next(e for e in lirr_feed.entity if e.id == VEHICLE_ENTITY_ID)

print("fixture                    backend/tests/fixtures/railroad_lirr.pb")
print(f"feed header timestamp      {int(HEADER_NOW)}")
print(f"entities in the capture    {len(lirr_feed.entity)}")
print(f"TripUpdates dropped by the cancellation filter   {len(canceled_ids)}")
print(f"distinct positioned vehicle trip_ids             {len(positioned_ids)}")
print(f"trip_ids that are BOTH canceled and positioned   {both}")
print()
print(f"TripUpdate entity  id={tu_entity.id!r}")
print(f"    trip_id                {tu_entity.trip_update.trip.trip_id!r}")
print(f"    route_id               {tu_entity.trip_update.trip.route_id!r}")
print(
    f"    schedule_relationship  {tu_entity.trip_update.trip.schedule_relationship}"
    f" ({SR.Name(tu_entity.trip_update.trip.schedule_relationship)})"
)
print(f"    carries a vehicle?     {tu_entity.HasField('vehicle')}")
print(f"    stop_time_updates      {len(tu_entity.trip_update.stop_time_update)}")
for stu in tu_entity.trip_update.stop_time_update:
    when = stu.arrival.time or stu.departure.time
    name = LIRR_STOPS.get(stu.stop_id, {}).get("name")
    print(
        f"        stop {stu.stop_id:>4} {name:<14} t={when}"
        f"  ({when - int(HEADER_NOW):+d} s from this capture's header)"
    )
print()
print(f"SEPARATE vehicle entity  id={veh_entity.id!r}")
print(f"    vehicle.trip.trip_id   {veh_entity.vehicle.trip.trip_id!r}")
print(
    f"    vehicle.trip.schedule_relationship  "
    f"{veh_entity.vehicle.trip.schedule_relationship}"
    f" ({SR.Name(veh_entity.vehicle.trip.schedule_relationship)})"
)
print(
    f"    position               "
    f"lat={veh_entity.vehicle.position.latitude} lon={veh_entity.vehicle.position.longitude}"
)
print(f"    vehicle label / id     {veh_entity.vehicle.vehicle.label!r} / {veh_entity.vehicle.vehicle.id!r}")
print(f"    vehicle.timestamp      {veh_entity.vehicle.timestamp}")

check("LIRR capture header timestamp", int(HEADER_NOW), EXPECT["lirr_header"])
check("LIRR capture entity count", len(lirr_feed.entity), EXPECT["lirr_entities"])
check("canceled/deleted TripUpdates", len(canceled_ids), EXPECT["canceled_trip_updates"])
check("positioned vehicle trip_ids", len(positioned_ids), EXPECT["positioned_trip_ids"])
check("canceled AND positioned trip_ids", both, [TRIP_ID])
check(
    "TripUpdate schedule_relationship",
    SR.Name(tu_entity.trip_update.trip.schedule_relationship),
    "CANCELED",
)
check("TripUpdate route_id", tu_entity.trip_update.trip.route_id, "5")
check("TripUpdate entity carries no vehicle", tu_entity.HasField("vehicle"), False)
check("vehicle entity has a position", veh_entity.vehicle.HasField("position"), True)
check("vehicle label", veh_entity.vehicle.vehicle.label, "508")

# ------------------------------------ Panel B: GPS emission and the golden ----

banner("PANEL B: the production GPS decode and the committed golden (claim b)")

gps_trains, feed_ts = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", HEADER_NOW)
gps_hits = [t for t in gps_trains if t["trip_id"] == TRIP_ID]
first = gps_trains[0]

print(f"feeds._decode_railroad_vehicles(capture, 'LIRR', now=header)  -> {len(gps_trains)} GPS trains")
print(f"feed_timestamp returned                                        {feed_ts}")
print(f"records for the canceled trip                                  {len(gps_hits)}")
print(f"index of the canceled trip in the emitted list                 {gps_trains.index(gps_hits[0])}")
print()
print("the emitted record:")
for key, value in first.items():
    print(f"    {key:<11} {value!r}")
print()
print(f"golden railroad_lirr_expected.json: now={GOLDEN['now']} records={len(GOLDEN['trains'])}")
print(f"golden record [0] trip_id/route_id/train_num  "
      f"{GOLDEN['trains'][0]['trip_id']!r} / {GOLDEN['trains'][0]['route_id']!r}"
      f" / {GOLDEN['trains'][0]['train_num']!r}")
print(f"decoder output equals the golden list exactly  {gps_trains == GOLDEN['trains']}")

check("GPS trains decoded", len(gps_trains), EXPECT["gps_trains"])
check("canceled trip emitted as GPS", len(gps_hits), 1)
check("canceled trip is the FIRST emitted record", gps_trains.index(gps_hits[0]), 0)
check("emitted route_id", first["route_id"], "5")
check("emitted train_num", first["train_num"], "508")
check("emitted trip_id", first["trip_id"], TRIP_ID)
check("golden record count", len(GOLDEN["trains"]), EXPECT["golden_records"])
check("golden first record trip_id", GOLDEN["trains"][0]["trip_id"], TRIP_ID)
check("golden first record route_id", GOLDEN["trains"][0]["route_id"], "5")
check("golden first record train_num", GOLDEN["trains"][0]["train_num"], "508")
check("decoder output matches the golden", gps_trains == GOLDEN["trains"], True)

# ------------------------- Panel C: both sides, at the production decoders ----

banner("PANEL C: map side versus board side, in the same decode (claim c)")

placed, arrivals = feeds._decode_railroad_feed(LIRR_RAW, "LIRR", LIRR_STOPS, HEADER_NOW)
rows = arrival_rows(arrivals)
placed_hits = [t for t in placed if t["trip_id"] == TRIP_ID]
row_hits = [r for r in rows if r[2]["trip_id"] == TRIP_ID]

print("at now = the capture's own header timestamp:")
print(f"    GPS pass       _decode_railroad_vehicles  {len(gps_trains)} trains,"
      f" canceled trip present: {bool(gps_hits)}")
print(f"    placement pass _decode_railroad_feed      {len(placed)} placed trains,"
      f" canceled trip present: {bool(placed_hits)}")
print(f"    arrivals pass  _decode_railroad_feed      {len(arrivals)} stations,"
      f" {len(rows)} rows, canceled trip rows: {len(row_hits)}")

check("placed trains at header now", len(placed), EXPECT["placed_at_header_now"])
check("arrival stations at header now", len(arrivals), EXPECT["arrival_stations_at_header_now"])
check("arrival rows at header now", len(rows), EXPECT["arrival_rows_at_header_now"])
check("canceled trip placed", len(placed_hits), 0)
check("canceled trip in arrivals", len(row_hits), 0)

print()
print("CONTROL. Same capture, same stops, decode clock frozen at"
      f" {CONTROL_NOW:.0f} (inside the capture, 622 s before the trip's first stop")
print("time), with ONLY that one entity's schedule_relationship changed:")
print()
print(f"    {'variant':<34}{'GPS':>5}{'trip in GPS':>13}{'placed':>8}"
      f"{'rows':>7}{'trip rows':>11}")

control_gps: dict[str, list[dict]] = {}
control_rows: dict[str, int] = {}
control_own: dict[str, int] = {}
variants = (
    ("as captured (TripUpdate CANCELED)", {}),
    ("TripUpdate flipped to SCHEDULED", {"trip_sr": SR.SCHEDULED}),
    ("TripUpdate flipped to DELETED", {"trip_sr": SR.DELETED}),
    ("VehicleDescriptor set CANCELED", {"vehicle_sr": SR.CANCELED}),
)
for label, kwargs in variants:
    raw = flipped(LIRR_RAW, **kwargs) if kwargs else LIRR_RAW
    v_gps, _ = feeds._decode_railroad_vehicles(raw, "LIRR", CONTROL_NOW)
    v_placed, v_arrivals = feeds._decode_railroad_feed(raw, "LIRR", LIRR_STOPS, CONTROL_NOW)
    v_rows = arrival_rows(v_arrivals)
    v_own = [r for r in v_rows if r[2]["trip_id"] == TRIP_ID]
    control_gps[label] = v_gps
    control_rows[label] = len(v_rows)
    control_own[label] = len(v_own)
    in_gps = any(t["trip_id"] == TRIP_ID for t in v_gps)
    print(f"    {label:<34}{len(v_gps):>5}{str(in_gps):>13}{len(v_placed):>8}"
          f"{len(v_rows):>7}{len(v_own):>11}")
    for stop_id, bucket, row in v_own:
        name = LIRR_STOPS[stop_id]["name"]
        print(f"        board row: stop {stop_id} {name}, bucket {bucket},"
              f" train {row['train_num']}, arrival {row['arrival']:.0f}")

captured_label = variants[0][0]
scheduled_label = variants[1][0]
deleted_label = variants[2][0]
vehicle_label = variants[3][0]

print()
print("GPS output identical across all four variants: "
      f"{all(control_gps[label] == control_gps[captured_label] for label, _ in variants)}")
print("So the GPS pass reads no schedule_relationship at all: from the map's point")
print("of view a canceled trip, a deleted trip and a running trip are the same feed.")

check("control: canceled arrival rows", control_rows[captured_label], EXPECT["control_rows_canceled"])
check("control: canceled trip rows when CANCELED", control_own[captured_label], 0)
check("control: scheduled arrival rows", control_rows[scheduled_label], EXPECT["control_rows_scheduled"])
check(
    "control: canceled trip rows when SCHEDULED",
    control_own[scheduled_label],
    EXPECT["control_own_rows_scheduled"],
)
check("control: DELETED behaves like CANCELED", control_own[deleted_label], 0)
check(
    "control: vehicle-descriptor CANCELED does not reach the board",
    control_own[vehicle_label],
    0,
)
for label, _ in variants:
    check(f"control: GPS output unchanged by {label}", control_gps[label], control_gps[captured_label])

# ------------------------------- Panel D: the same disagreement over HTTP ----

banner("PANEL D: the rider surfaces, through the real ASGI app (claim c)")


def mock_transport(lirr_bytes: bytes) -> httpx.MockTransport:
    """Serves the two committed captures for the two railroad feed URLs. Any
    other host is a bug in this script and raises rather than opening a socket."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == feeds.RAILROAD_FEED_URLS["LIRR"]:
            return httpx.Response(200, content=lirr_bytes)
        if url == feeds.RAILROAD_FEED_URLS["MNR"]:
            return httpx.Response(200, content=MNR_RAW)
        raise AssertionError(f"unexpected upstream request: {url}")

    return httpx.MockTransport(handler)


async def poll_and_serve(lirr_bytes: bytes) -> tuple[dict, dict, dict]:
    """One production railroad poll over the given LIRR bytes, then the map
    endpoint and two station boards read from that same poll."""
    app = main.app
    app.state.feed_cache = {
        name: main._fresh_entry()
        for name in ("buses", "subways", "railroads", "path", "ferry", "njt")
    }
    app.state.railroad_feed_health = None
    app.state.railroad_stops = {"LIRR": LIRR_STOPS, "MNR": MNR_STOPS}
    app.state.railroad_arrivals = {}
    app.state.railroad_positions = {}
    async with httpx.AsyncClient(transport=mock_transport(lirr_bytes)) as upstream:
        await main._refresh_railroads(app, upstream)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://f02.local") as api:
        feed = (await api.get("/api/railroads")).json()
        board83 = (await api.get("/api/railroad-arrivals/LIRR/83")).json()
        board198 = (await api.get("/api/railroad-arrivals/LIRR/198")).json()
    return feed, board83, board198


async def run_panel_d() -> dict:
    out = {}
    for label, lirr_bytes in (
        ("as captured (CANCELED)", LIRR_RAW),
        ("control (SCHEDULED)", flipped(LIRR_RAW, trip_sr=SR.SCHEDULED)),
    ):
        out[label] = await poll_and_serve(lirr_bytes)
    return out


# The decoders take `now` from feeds.railroad's module clock. Freeze it to the
# same in-capture constant Panel C used, so the poll is deterministic and does
# not depend on the day this script runs. Restored immediately afterwards.
real_time_module = railroad_mod.time
railroad_mod.time = types.SimpleNamespace(time=lambda: CONTROL_NOW)
try:
    panel_d = asyncio.run(run_panel_d())
finally:
    railroad_mod.time = real_time_module

for label, (feed, board83, board198) in panel_d.items():
    marker = [t for t in feed["data"] if t["trip_id"] == TRIP_ID]
    print(f"{label}")
    print(f"    GET /api/railroads                    {len(feed['data'])} markers,"
          f" canceled trip present: {bool(marker)}")
    if marker:
        print(f"        {json.dumps(marker[0], sort_keys=True)}")
    for stop_id, board in (("83", board83), ("198", board198)):
        board_rows = [row for rows in board["directions"].values() for row in rows]
        hits = [row for row in board_rows if row["trip_id"] == TRIP_ID]
        print(f"    GET /api/railroad-arrivals/LIRR/{stop_id:<4}"
              f"({board['stop_name']}): {len(board_rows)} rows,"
              f" train 508 present: {bool(hits)}")
    print()

cap_feed, cap_83, cap_198 = panel_d["as captured (CANCELED)"]
ctl_feed, ctl_83, ctl_198 = panel_d["control (SCHEDULED)"]


def board_hits(board: dict) -> int:
    return sum(1 for rows in board["directions"].values() for row in rows if row["trip_id"] == TRIP_ID)


def board_total(board: dict) -> int:
    return sum(len(rows) for rows in board["directions"].values())


print("The map endpoint serves the canceled train in BOTH runs and its record is")
print("byte-identical between them: "
      f"{[t for t in cap_feed['data'] if t['trip_id'] == TRIP_ID] == [t for t in ctl_feed['data'] if t['trip_id'] == TRIP_ID]}")
print("The station board serves it only in the control. Map and board disagree.")

check("api: marker count (captured)", len(cap_feed["data"]), EXPECT["api_railroad_records"])
check("api: marker count (control)", len(ctl_feed["data"]), EXPECT["api_railroad_records"])
check("api: canceled trip on the map (captured)",
      sum(1 for t in cap_feed["data"] if t["trip_id"] == TRIP_ID), 1)
check("api: canceled trip on the map (control)",
      sum(1 for t in ctl_feed["data"] if t["trip_id"] == TRIP_ID), 1)
check("api: map record identical across variants",
      [t for t in cap_feed["data"] if t["trip_id"] == TRIP_ID],
      [t for t in ctl_feed["data"] if t["trip_id"] == TRIP_ID])
check("api: stop 83 rows (captured)", board_total(cap_83), EXPECT["api_stop83_rows_captured"])
check("api: stop 83 rows (control)", board_total(ctl_83), EXPECT["api_stop83_rows_control"])
check("api: stop 83 canceled-trip rows (captured)", board_hits(cap_83), 0)
check("api: stop 83 canceled-trip rows (control)", board_hits(ctl_83), 1)
check("api: stop 198 rows (captured)", board_total(cap_198), EXPECT["api_stop198_rows_captured"])
check("api: stop 198 rows (control)", board_total(ctl_198), EXPECT["api_stop198_rows_control"])
check("api: stop 198 canceled-trip rows (captured)", board_hits(cap_198), 0)
check("api: stop 198 canceled-trip rows (control)", board_hits(ctl_198), 1)

# ------------------------------ Panel E: the combined entity layout (MNR) ----

banner("PANEL E: the combined TripUpdate + VehiclePosition layout (claim d)")

SYNTH_NOW = 1782006692.0  # the committed MNR capture's own header timestamp
SYNTH_TRIP = "3114306C"
SYNTH_VEHICLE = "1797C"

real_combined = sum(
    1
    for e in pb.FeedMessage.FromString(MNR_RAW).entity
    if e.HasField("trip_update") and e.HasField("vehicle") and e.vehicle.HasField("position")
)
real_canceled_mnr = sum(
    1
    for e in pb.FeedMessage.FromString(MNR_RAW).entity
    if e.HasField("trip_update")
    and e.trip_update.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS
)
print(f"committed railroad_mnr.pb: {real_combined} combined positioned entities,"
      f" {real_canceled_mnr} canceled TripUpdates")
print("The capture has no canceled combined entity, so the layout is built"
      " synthetically,")
print("shaped after a real combined entity from that same capture. A SCHEDULED")
print("control is built alongside it so the comparison is not an artifact of the")
print("synthetic feed.")
print()


def synthetic_mnr(schedule_relationship: int) -> bytes:
    """One Metro-North style COMBINED entity: trip_update and vehicle (with a
    position) on the SAME entity. Everything here is injected."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(SYNTH_NOW - 30)
    entity = feed.entity.add()
    entity.id = SYNTH_VEHICLE
    tu = entity.trip_update
    tu.trip.trip_id = SYNTH_TRIP
    tu.trip.route_id = "4"
    started = datetime.fromtimestamp(SYNTH_NOW - 600, NYC_TZ)
    tu.trip.start_date = started.strftime("%Y%m%d")
    tu.trip.start_time = started.strftime("%H:%M:%S")
    tu.trip.schedule_relationship = schedule_relationship
    for index, stop_id in enumerate(("157", "155", "154")):
        stu = tu.stop_time_update.add()
        stu.stop_id = stop_id
        stu.arrival.time = int(SYNTH_NOW + 300 * (index + 1))
        stu.departure.time = int(SYNTH_NOW + 300 * (index + 1))
    vehicle = entity.vehicle
    vehicle.trip.trip_id = SYNTH_VEHICLE  # MNR's vehicle carries the train number
    vehicle.position.latitude = 41.0475349
    vehicle.position.longitude = -73.54013
    vehicle.vehicle.id = SYNTH_VEHICLE
    vehicle.vehicle.label = SYNTH_VEHICLE
    vehicle.timestamp = int(SYNTH_NOW - 20)
    return feed.SerializeToString()


print(f"    {'trip.schedule_relationship':<28}{'GPS':>5}{'placed':>8}{'board rows':>12}")
synth_gps: dict[str, list[dict]] = {}
synth_rows: dict[str, int] = {}
for name in ("SCHEDULED", "CANCELED", "DELETED"):
    raw = synthetic_mnr(SR.Value(name))
    s_gps, _ = feeds._decode_railroad_vehicles(raw, "MNR", SYNTH_NOW)
    s_placed, s_arrivals = feeds._decode_railroad_feed(raw, "MNR", MNR_STOPS, SYNTH_NOW)
    s_rows = arrival_rows(s_arrivals)
    synth_gps[name] = s_gps
    synth_rows[name] = len(s_rows)
    print(f"    {name:<28}{len(s_gps):>5}{len(s_placed):>8}{len(s_rows):>12}")
    for stop_id, bucket, row in s_rows:
        print(f"        board row: stop {stop_id} {MNR_STOPS[stop_id]['name']},"
              f" bucket {bucket}, train {row['train_num']}, arrival {row['arrival']:.0f}")
print()
print(f"GPS record identical for SCHEDULED, CANCELED and DELETED: "
      f"{synth_gps['SCHEDULED'] == synth_gps['CANCELED'] == synth_gps['DELETED']}")
print(f"the emitted GPS record: {json.dumps(synth_gps['CANCELED'][0], sort_keys=True)}")

check("synthetic MNR: GPS emitted when SCHEDULED", len(synth_gps["SCHEDULED"]), 1)
check("synthetic MNR: GPS emitted when CANCELED", len(synth_gps["CANCELED"]), 1)
check("synthetic MNR: GPS emitted when DELETED", len(synth_gps["DELETED"]), 1)
check("synthetic MNR: GPS record unchanged by CANCELED",
      synth_gps["CANCELED"], synth_gps["SCHEDULED"])
check("synthetic MNR: GPS record unchanged by DELETED",
      synth_gps["DELETED"], synth_gps["SCHEDULED"])
check("synthetic MNR: board rows when SCHEDULED",
      synth_rows["SCHEDULED"], EXPECT["mnr_synth_arrival_rows_scheduled"])
check("synthetic MNR: board rows when CANCELED", synth_rows["CANCELED"], 0)
check("synthetic MNR: board rows when DELETED", synth_rows["DELETED"], 0)

# ---------------------------------- Panel F: DELETED versus CANCELED ---------

banner("PANEL F: how DELETED is handled on each path (claim e)")

deleted_value = SR.Value("DELETED")
pin_line = next(
    line.strip()
    for line in (ROOT / "backend" / "requirements.txt").read_text().splitlines()
    if line.startswith("gtfs-realtime-bindings")
)
lock_line = next(
    line.strip()
    for line in (ROOT / "backend" / "requirements.lock").read_text().splitlines()
    if line.startswith("gtfs-realtime-bindings")
)
gps_source = railroad_mod._decode_railroad_vehicles.__doc__ or ""
gps_reads_sr = "schedule_relationship" in (
    (ROOT / "backend" / "feeds" / "railroad.py")
    .read_text()
    .split("def _decode_railroad_vehicles")[1]
    .split("def _infer_railroad_direction")[0]
)

print(f"requirements pin                 {pin_line}")
print(f"lock                             {lock_line}")
print(f"TripDescriptor DELETED resolves  {deleted_value}")
print(f"_DROP_TRIP_RELATIONSHIPS         {sorted(_DROP_TRIP_RELATIONSHIPS)}"
      f"  (CANCELED={SR.Value('CANCELED')}, DELETED={deleted_value})")
print("placement/arrivals filter at feeds/railroad.py:305 drops both")
print(f"_decode_railroad_vehicles mentions schedule_relationship anywhere: {gps_reads_sr}")
print()
print("So the pin bought a working DELETED filter for the board only. On the GPS")
print("path DELETED is treated exactly like CANCELED and exactly like SCHEDULED:")
print("the marker is emitted either way (measured in Panels C and E).")

check("DELETED enum value", deleted_value, EXPECT["deleted_enum"])
check("DELETED is in the drop set", deleted_value in _DROP_TRIP_RELATIONSHIPS, True)
check("CANCELED is in the drop set", SR.Value("CANCELED") in _DROP_TRIP_RELATIONSHIPS, True)
check("GPS decoder never reads schedule_relationship", gps_reads_sr, False)

# ------------------------------------- Panel G: is the marker marked at all --

banner("PANEL G: does the emitted GPS record carry ANY cancellation marking")

model_fields = sorted(models.RailroadTrain.model_fields)
served = [t for t in cap_feed["data"] if t["trip_id"] == TRIP_ID][0]
suspicious = [
    field
    for field in model_fields
    if any(word in field for word in ("cancel", "delete", "status", "state", "relationship", "service"))
]
print(f"models.RailroadTrain fields ({len(model_fields)}): {model_fields}")
print(f"keys on the served record   ({len(served)}): {sorted(served)}")
print(f"fields that could carry a cancellation marking: {suspicious or 'none'}")
print(f"the served record's non-null fields: "
      f"{ {k: v for k, v in served.items() if v is not None} }")
print()
print("Nothing in the record distinguishes it from a running train. The nulls it")
print("does carry (stop_id, direction, next_time) mean 'this is a GPS train, not a")
print("placed one', which is what every healthy GPS marker carries too.")

check("no cancellation-bearing field on the model", suspicious, [])
check("served record has exactly the model's fields", sorted(served), model_fields)

# ------------------------------------------------------------- disposition ---

banner("MEASURED SUMMARY")
print(f"""
capture                     backend/tests/fixtures/railroad_lirr.pb, header {int(HEADER_NOW)}
canceled trip                {TRIP_ID}  (TripUpdate entity {TU_ENTITY_ID},
                            schedule_relationship CANCELED, no vehicle on that entity)
separate GPS entity          {VEHICLE_ENTITY_ID}, position
                            ({veh_entity.vehicle.position.latitude}, {veh_entity.vehicle.position.longitude}),
                            label 508, vehicle.timestamp {veh_entity.vehicle.timestamp}
it is the ONLY one of the {len(canceled_ids)} canceled LIRR TripUpdates that also has a position

GPS pass    {len(gps_trains)} trains, the canceled trip FIRST, route 5, train 508,
            identical to the {len(GOLDEN['trains'])}-record golden railroad_lirr_expected.json
board pass  {len(placed)} placed trains and {len(rows)} arrival rows over {len(arrivals)} stations,
            zero of them the canceled trip

control at now={CONTROL_NOW:.0f} (inside the capture): flipping only that entity's
    schedule_relationship changes the board from {control_own[captured_label]} to
    {control_own[scheduled_label]} rows for the trip
    ({control_rows[captured_label]} -> {control_rows[scheduled_label]} rows overall),
    and leaves the GPS output bit-for-bit unchanged in all four variants

over HTTP   /api/railroads served {len(cap_feed['data'])} markers including the canceled train,
            same record in the CANCELED and SCHEDULED runs;
            /api/railroad-arrivals/LIRR/83 (Hampton Bays) served
            {board_total(cap_83)} rows without train 508, and {board_total(ctl_83)} rows WITH it in the control;
            /api/railroad-arrivals/LIRR/198 (Speonk) the same, {board_total(cap_198)} versus {board_total(ctl_198)}

combined layout (synthetic MNR entity): GPS 1 record for SCHEDULED, CANCELED and
    DELETED alike; board {synth_rows['SCHEDULED']} rows SCHEDULED,
    {synth_rows['CANCELED']} CANCELED, {synth_rows['DELETED']} DELETED

DELETED     enum {deleted_value} under {lock_line}; in the board's drop set,
            absent from the GPS path, which reads no schedule_relationship at all

marking     the served record carries {len(served)} fields, none of them a status,
            cancellation or service-relationship field
""")

if failures:
    print("MEASUREMENT DISAGREES WITH THE AUDIT RECORD:")
    for line in failures:
        print(f"  - {line}")
    print()
    print("DISPOSITION: CHANGED, this script no longer matches the recorded finding.")
    sys.exit(1)

print("DISPOSITION: VERIFIED")
print(
    "Canceled LIRR trip 6004XX_2026-06-20 is dropped from placement and arrivals"
    " but still served as an unmarked route 5 / train 508 GPS marker (first golden"
    " record, present at /api/railroads while absent from both of its station"
    " boards); the same holds for the combined MNR entity layout and for DELETED."
)
