#!/usr/bin/env python3
"""A11 (Low): "Estimated LIRR trains lose their train numbers."

Reproduction and regression check for finding A11 of the 2026-10-12 audit (the report's
"Additional confirmed narrow defects" table), over main at 1e3e73d.

THE AUDIT'S CLAIM, the row's evidence cell verbatim:

    "The committed LIRR capture produces six estimated placements with null train numbers
    although joined vehicle labels exist. The arrivals board retains these numbers.
    backend/feeds/railroad.py:975 and backend/feeds/railroad.py:1093."

    Recommended correction: "Apply the existing trip-to-vehicle-label join to placement
    rows too. Test GPS → estimated → GPS identity continuity."

    Cited code (where to LOOK; nothing below asserts a line, a name or a comment):
      https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/feeds/railroad.py#L975
      https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/feeds/railroad.py#L1093

THE TWO READS THE ROW IS ABOUT. One pass over the trip_updates produces both the
placement rows and the arrivals board. The board's train number is read off the
trip_update's own entity, and when that entity carries no vehicle (LIRR's split layout)
it is joined in by trip id from the separate positioned vehicle entity. The placement
row reads only its own entity. So the six LIRR trains the position ladder moves off the
GPS surface and draws as estimates (contract 6.3, step 2) are served with train_num null
while every one of their board rows names the train.

KNOWN BEFORE THE AUDIT, AND DEFERRED. N2's pull request (#109, claude/n2-shared-gps-set),
under "Adjacent, noted and not fixed", recorded the same read for the train N2's fallback
recovers, before contract 6.3 created the six estimates:

    "A recovered LIRR train is placed with `train_num` null. Placement reads the number
    only from its own entity's vehicle, and LIRR's `trip_update` entities carry none, so
    all 56 LIRR placements already have a null train number. This fix makes it visible
    on a trip that previously had a labelled GPS marker. Joining `label_by_trip` there
    would fix it, and would move the placement golden for all 56 records, destroying the
    byte-identical-on-the-captures property that makes this change reviewable. Separate
    concern."

That text is in the PR body only; no committed document at 1e3e73d carries it. Section B
measures one clause of it differently: the 56 placed rows are null, but none of them has a
vehicle to join, so the join would move the six estimates and not those 56.

"JOINABLE", AS THIS SCRIPT USES IT: the capture carries a VehiclePosition entity whose
trip id equals the placement row's and whose vehicle carries a label. That is the one
thing computed here rather than read off production output, and it is computed as a
minimal read of the wire (trip id -> the vehicle's label), to give a column to set beside
the production join's own OUTPUT, which is the train number the arrivals board serves for
the same trip. The two columns are checked equal. The production join itself is a local
of the decoder with no public surface, so its output is the only thing of it measured.

WHAT THIS SCRIPT MEASURES

  A. The capture, read with gtfs_realtime_pb2: every positioned LIRR vehicle carries a
     label, and no vehicle entity lacks a position (so the join's positioned-only scope
     loses nothing on these bytes).
  B. The real placement and arrivals pass at the capture's own header: the 62 placement
     rows split 56 placed and 6 estimated; the 6 are the joinable ones, each null on the
     placement row, each named on every one of its board rows. The 56 placed rows are
     null too, and none of them is joinable: the capture carries no vehicle for any of
     those trips, so there is no label to join and they are not this finding.
  C. The served surfaces, through the real poll and the real app at that same clock:
     GET /api/railroads serves the six `estimated` rows with train_num null, and GET
     /api/railroad-arrivals/LIRR/{stop} at the stop each one is drawn at names it.
     Metro-North's placements are the control: its combined entity carries the vehicle
     inline, so its placement rows are named.
  D. The correction's own test, GPS -> estimated -> GPS, as three real polls on one app:
     the six vehicles' fixes stamped 10 s behind the header (drawn at their own position),
     then the capture as captured (drawn as estimates), then stamped fresh again. The
     served train_num for each of the six goes label -> null -> label while the board
     names it throughout.
  E. Beyond the audit's six, the case N2's PR (#109) deferred: a positioned vehicle the
     railroad box rejects (the freshest on the capture moved to lat 0, lon 0) is placed
     from its trip_update by N2's fallback, and that row is null too while its board rows
     keep the number. Same read, a different door onto it.

WHAT IS PRODUCTION CODE HERE (nothing is re-implemented): feeds._decode_railroad_feed
(and through it the position ladder, _accepted_as_gps, the label join and the placement
row builder), feeds.fetch_railroad_trains, pollers._refresh_railroads, and the routes
GET /api/railroads and GET /api/railroad-arrivals/{system}/{stop_id} on main.app, served
through httpx.ASGITransport.

WHAT IS INJECTED, all in memory (the committed fixtures are never written):
  1. The two upstream bodies, served by a fake client inside the real fetch: the
     committed LIRR and Metro-North captures, and in D and E copies of the LIRR capture
     with named vehicles' timestamps or coordinates rewritten.
  2. time.time pinned to the LIRR capture's own FeedHeader.timestamp for every poll and
     every decode, so no age moves with the day this runs.
  3. app.state primed as a lifespan would leave it (railroad static ready, the committed
     stops), and re-primed between sections; D's three polls share one app on purpose.

HERMETIC: _hermetic.contain() runs before the first backend import and verify() after it;
the MTA realtime seams point at the loopback discard port and DATA_DIR at a fresh
temporary directory, both set before the backend reads them; the fake upstream records
and refuses any URL it was not given and any URL naming an NJ Transit host, and both lists
are checked empty. No socket, no credential, no dependence on today's date.

RUN (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a11_estimated_lirr_train_numbers.py

RECORDED DISPOSITION: VERIFIED. Exits 0 while the six estimated LIRR placements are served
with train_num null although the capture's vehicle labels join and the board names them;
non-zero, naming every check that moved, the moment any of that stops being true.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FIXTURES = BACKEND / "tests" / "fixtures"

# Seams the backend reads ONCE, at import, so they are set before the first backend
# import. Assigned rather than defaulted: a developer's shell or .env must not be able to
# point this process at the real MTA or at the repository's data/ directory.
_LOOPBACK = "http://127.0.0.1:9/a11"
os.environ["SUBWAY_RT_BASE"] = f"{_LOOPBACK}/subway"
os.environ["RAILROAD_RT_BASE"] = f"{_LOOPBACK}/railroad"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="a11-data-")
atexit.register(shutil.rmtree, os.environ["DATA_DIR"], ignore_errors=True)

# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT (see the 09-05 README).
sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import cache  # noqa: E402
import feeds  # noqa: E402
import main as app_module  # noqa: E402  (the real FastAPI app)
import pollers  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED.
_hermetic.verify()

APP = app_module.app
LIRR_URL = feeds.RAILROAD_FEED_URLS["LIRR"]
MNR_URL = feeds.RAILROAD_FEED_URLS["MNR"]

LIRR_RAW = (FIXTURES / "railroad_lirr.pb").read_bytes()
MNR_RAW = (FIXTURES / "railroad_mnr.pb").read_bytes()
LIRR_STOPS = json.loads((FIXTURES / "railroad_lirr_stops.json").read_text())
MNR_STOPS = json.loads((FIXTURES / "railroad_mnr_stops.json").read_text())
LIRR_FEED = pb.FeedMessage.FromString(LIRR_RAW)
# THE ONE CLOCK: the LIRR capture's own header. The position ladder reads every age
# against it, and at it the placement pass can still place the six (F01's step 2).
HEADER_TS = float(LIRR_FEED.header.timestamp)

# D's fresh fix: this many seconds behind the header, well inside OBS_FRESH_S, so the
# ladder draws the vehicle at its own position (step 1) and the GPS pass serves it.
FRESH_FIX_S = 10

# E's subject: the freshest positioned vehicle on the capture (train 7344, its fix 4 s
# old, ladder step 1), the vehicle N2's own fallback test displaces. Moving it to lat 0,
# lon 0 (the box rejects it before any age is read) routes its trip to N2's fallback.
DISPLACED = "GO201_26_7588"

# RECORDED EXPECTATIONS. The six by trip id, each with the label its vehicle entity
# carries on the wire, which is also what the board names it, and the stop its estimate
# is drawn at. A changed value fails the run rather than rewriting the record.
EXPECT = {
    "header": 1782006915,
    "positioned_vehicles": 69,
    "positioned_with_label": 69,
    "vehicles_without_position": 0,
    "placement_rows": 62,
    "placed_rows": 56,
    "estimated_rows": 6,
    "six": {
        "6029_2026-06-20": ("5019", "198"),
        "GO201_26_6187_1": ("7735", "102"),
        "GO201_26_6188": ("7024", "64"),
        "GO201_26_6468_2932_METS": ("7706", "171"),
        "GO201_26_6665": ("5011", "193"),
        "GO201_26_8768": ("7604", "102"),
    },
    # Board rows each of the six publishes at the header (every upcoming stop, capped
    # per direction), every one of them carrying the label.
    "board_rows": {
        "6029_2026-06-20": 5,
        "GO201_26_6187_1": 4,
        "GO201_26_6188": 7,
        "GO201_26_6468_2932_METS": 1,
        "GO201_26_6665": 6,
        "GO201_26_8768": 12,
    },
    "lirr_positions": {"reported": 27, "estimated": 6, "qualified": 11, "placed": 0,
                       "suppressed": 24},
    "lirr_positions_six_fresh": {"reported": 33, "estimated": 0, "qualified": 11, "placed": 0,
                                 "suppressed": 24},
    # Metro-North's placement rows at the same clock, each named by the vehicle its
    # combined entity carries inline: the control.
    "mnr_placed_numbers": ["8765", "8863", "8872"],
    "displaced_label": "7344",
    "displaced_fix_age_s": 4,
    "displaced_board_rows": 11,
}

FORBIDDEN_URLS: list[str] = []
UNEXPECTED_URLS: list[str] = []
FAILURES: list[str] = []


def check(label: str, got, want, audit: str | None = None) -> None:
    """One named expectation. Every check runs; the script fails once at the end."""
    ok = got == want
    if not ok:
        FAILURES.append(f"{label}: measured {got!r}, recorded {want!r}")
    beside = f"   (audit: {audit})" if audit else ""
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}{beside}")


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def wire_labels(feed) -> dict[str, list[str]]:
    """trip id -> the labels the capture's vehicle entities carry for it, positioned or
    not. The minimal read that defines "joinable" (see the docstring); the production
    join's output is the board's train_num, read separately and compared to this."""
    labels: dict[str, list[str]] = {}
    for entity in feed.entity:
        if entity.HasField("vehicle") and entity.vehicle.trip.trip_id:
            label = entity.vehicle.vehicle.label
            if label:
                labels.setdefault(entity.vehicle.trip.trip_id, []).append(label)
    return labels


def board_rows_by_trip(arrivals: dict) -> dict[str, list[tuple[str, dict]]]:
    """{stop_id: {bucket: [row]}} regrouped as trip id -> [(stop_id, row)]."""
    out: dict[str, list[tuple[str, dict]]] = {}
    for stop_id, buckets in arrivals.items():
        for rows in buckets.values():
            for row in rows:
                out.setdefault(row["trip_id"], []).append((stop_id, row))
    return out


def restamped(raw: bytes, trip_ids, age_s: float) -> bytes:
    """The capture with the named trips' vehicle fixes stamped `age_s` behind the header.
    Every other byte is left alone."""
    feed = pb.FeedMessage.FromString(raw)
    for entity in feed.entity:
        if entity.HasField("vehicle") and entity.vehicle.trip.trip_id in trip_ids:
            entity.vehicle.timestamp = int(feed.header.timestamp - age_s)
    return feed.SerializeToString()


def displaced(raw: bytes, trip_id: str) -> bytes:
    """The capture with one vehicle moved to lat 0 / lon 0, which the railroad box
    rejects: N2's own injection."""
    feed = pb.FeedMessage.FromString(raw)
    for entity in feed.entity:
        if entity.HasField("vehicle") and entity.vehicle.trip.trip_id == trip_id:
            entity.vehicle.position.latitude = 0.0
            entity.vehicle.position.longitude = 0.0
    return feed.SerializeToString()


class FakeUpstream:
    """Stands in for httpx.AsyncClient inside the REAL fetch. A URL it was not given, or
    one naming an NJ Transit host, is recorded and refused, and both records are checked
    empty, because a refusal inside the fetch's gather would otherwise read as an
    ordinary failed feed."""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies

    async def get(self, url: str) -> httpx.Response:
        lowered = url.lower()
        if "njtransit" in lowered or "raildata" in lowered:
            FORBIDDEN_URLS.append(url)
            raise RuntimeError(f"refused: {url} names an NJ Transit host")
        if url not in self.bodies:
            UNEXPECTED_URLS.append(url)
            raise RuntimeError(f"refused: {url} is not part of this world")
        request = httpx.Request("GET", url)
        return httpx.Response(200, content=self.bodies[url], request=request)


def prime() -> None:
    """The app.state a lifespan would have built for the railroad, and the state each poll
    writes back into, reset so no section inherits another's last-known values."""
    state = APP.state
    state.feed_cache = {"railroads": cache._fresh_entry()}
    state.railroad_feed_health = None
    state.railroad_static_status = "ready"
    state.railroad_stops = {"LIRR": LIRR_STOPS, "MNR": MNR_STOPS}
    state.railroad_arrivals = {}
    state.railroad_positions = {}


async def poll_and_serve(lirr: bytes, board_stops) -> dict:
    """One real railroad refresh over these LIRR bytes and the committed Metro-North
    capture, then what the app serves: the map body, the status, and the LIRR board at
    each stop asked for."""
    await pollers._refresh_railroads(APP, FakeUpstream({LIRR_URL: lirr, MNR_URL: MNR_RAW}))
    transport = httpx.ASGITransport(app=APP)
    out: dict = {"boards": {}}
    async with httpx.AsyncClient(transport=transport, base_url="http://a11.local") as http:
        response = await http.get("/api/railroads")
        if response.status_code != 200:
            FAILURES.append(f"GET /api/railroads answered {response.status_code}")
        out["body"] = response.json() if response.status_code == 200 else {"data": [],
                                                                            "systems": {}}
        out["status"] = (await http.get("/api/status")).json()
        for stop_id in sorted(set(board_stops)):
            path = f"/api/railroad-arrivals/LIRR/{stop_id}"
            response = await http.get(path)
            if response.status_code != 200:
                FAILURES.append(f"GET {path} answered {response.status_code}")
                out["boards"][stop_id] = {"directions": {}}
            else:
                out["boards"][stop_id] = response.json()
    return out


def board_numbers(board: dict, trip_id: str) -> list:
    """The train_num of every row this served board carries for one trip."""
    return [
        row["train_num"]
        for rows in board["directions"].values()
        for row in rows
        if row["trip_id"] == trip_id
    ]


def lirr_rows(body: dict) -> dict[str, dict]:
    return {t["trip_id"]: t for t in body["data"] if t["system"] == "LIRR"}


# ---------------------------------------------------------------------------
# A. the capture
# ---------------------------------------------------------------------------


def section_a() -> dict[str, list[str]]:
    rule("A. The capture, read with gtfs_realtime_pb2 alone")
    vehicles = [e for e in LIRR_FEED.entity if e.HasField("vehicle")]
    positioned = [e for e in vehicles if e.vehicle.HasField("position")]
    labelled = [e for e in positioned if e.vehicle.vehicle.label]
    labels = wire_labels(LIRR_FEED)

    print("  capture                        : backend/tests/fixtures/railroad_lirr.pb")
    print(f"  FeedHeader.timestamp           : {HEADER_TS:.0f} (the clock every section uses)")
    print(f"  vehicle entities               : {len(vehicles)}")
    print(f"  ... carrying a position        : {len(positioned)}")
    print(f"  ... of those, carrying a label : {len(labelled)}")
    print("  LIRR splits a train across two entities: a trip_update with no vehicle, and a")
    print("  vehicle entity carrying the label under the same trip id. The label is the")
    print("  rider-facing train number; the trip_update entity carries none.")

    check("LIRR header timestamp", int(HEADER_TS), EXPECT["header"])
    check("positioned LIRR vehicle entities", len(positioned), EXPECT["positioned_vehicles"])
    check("every one of them carries a label", len(labelled), EXPECT["positioned_with_label"])
    check("vehicle entities with no position (the join reads positioned ones only)",
          len(vehicles) - len(positioned), EXPECT["vehicles_without_position"])
    check("no trip_update entity carries a vehicle of its own",
          sum(1 for e in LIRR_FEED.entity if e.HasField("trip_update") and e.HasField("vehicle")),
          0)
    return labels


# ---------------------------------------------------------------------------
# B. the decoder
# ---------------------------------------------------------------------------


def section_b(labels: dict[str, list[str]]) -> None:
    rule("B. The real placement and arrivals pass, at the capture's own header")
    placed, arrivals = feeds._decode_railroad_feed(LIRR_RAW, "LIRR", LIRR_STOPS, HEADER_TS)
    board = board_rows_by_trip(arrivals)
    estimated = {t["trip_id"]: t for t in placed if t["provenance"] == "estimated"}
    plain = [t for t in placed if t["provenance"] == "placed"]
    joinable = {t["trip_id"] for t in placed if labels.get(t["trip_id"])}

    print("  feeds._decode_railroad_feed(capture, 'LIRR', stops, now=header)")
    print(f"    placement rows                 : {len(placed)}"
          f" ({len(plain)} placed, {len(estimated)} estimated)")
    print(f"    joinable (a labelled vehicle)  : {len(joinable)}, all of them estimated:"
          f" {joinable == set(estimated)}")
    print()
    print(f"    {'trip id':<26}{'wire label':>11}{'row train_num':>15}{'at stop':>9}"
          f"{'board rows':>12}{'board names':>13}")
    for trip_id in sorted(estimated):
        row = estimated[trip_id]
        names = sorted({r["train_num"] for _s, r in board.get(trip_id, [])}, key=str)
        print(f"    {trip_id:<26}{'/'.join(labels.get(trip_id, [])) or '-':>11}"
              f"{str(row['train_num']):>15}{row['stop_id']:>9}"
              f"{len(board.get(trip_id, [])):>12}{'/'.join(map(str, names)):>13}")
    print()
    print(f"  the {len(plain)} placed rows: train_num null on"
          f" {sum(1 for t in plain if t['train_num'] is None)}, joinable"
          f" {sum(1 for t in plain if labels.get(t['trip_id']))}, a board row naming the"
          f" train {sum(1 for t in plain if any(r['train_num'] for _s, r in board.get(t['trip_id'], [])))}")
    print("  (no vehicle entity exists for any of those trips, so no label exists to join:")
    print("  the board is null for them too, and they are not this finding)")

    six = EXPECT["six"]
    check("placement rows at the header", len(placed), EXPECT["placement_rows"])
    check("of which placed from a prediction alone", len(plain), EXPECT["placed_rows"])
    check("of which estimated (ladder step 2)", len(estimated), EXPECT["estimated_rows"],
          "six estimated placements")
    check("the estimated trips, by trip id", sorted(estimated), sorted(six))
    check("estimated rows served with train_num null",
          sum(1 for t in estimated.values() if t["train_num"] is None), EXPECT["estimated_rows"],
          "six ... with null train numbers")
    check("estimated rows whose trip the capture joins to a labelled vehicle",
          sum(1 for tid in estimated if labels.get(tid)), EXPECT["estimated_rows"],
          "although joined vehicle labels exist")
    check("the label the capture carries for each of the six",
          {tid: labels.get(tid) for tid in sorted(estimated)},
          {tid: [label] for tid, (label, _stop) in sorted(six.items())})
    check("the stop each estimate is drawn at",
          {tid: estimated[tid]["stop_id"] for tid in sorted(estimated)},
          {tid: stop for tid, (_label, stop) in sorted(six.items())})
    check("board rows each of the six publishes",
          {tid: len(board.get(tid, [])) for tid in sorted(estimated)}, EXPECT["board_rows"])
    check("every one of those board rows names the train by its wire label (the join's output)",
          all(r["train_num"] == six[tid][0] for tid in estimated for _s, r in board.get(tid, [])),
          True, "The arrivals board retains these numbers")
    check("each of the six has a board row at the stop its estimate is drawn at",
          all(any(s == estimated[tid]["stop_id"] for s, _r in board.get(tid, []))
              for tid in estimated), True)
    check("joinable placement rows are exactly the estimated ones", sorted(joinable),
          sorted(estimated))
    check("placed rows with train_num null (none joinable, so not this finding)",
          (sum(1 for t in plain if t["train_num"] is None),
           sum(1 for t in plain if labels.get(t["trip_id"]))),
          (EXPECT["placed_rows"], 0))


# ---------------------------------------------------------------------------
# C. the served surfaces
# ---------------------------------------------------------------------------


async def section_c() -> None:
    rule("C. Served: GET /api/railroads beside GET /api/railroad-arrivals, one real poll")
    six = EXPECT["six"]
    prime()
    got = await poll_and_serve(LIRR_RAW, [stop for _label, stop in six.values()])
    body = got["body"]
    rows = lirr_rows(body)
    estimated = {tid: t for tid, t in rows.items() if t["provenance"] == "estimated"}
    mnr_placed = [t for t in body["data"] if t["system"] == "MNR" and t["stop_id"] is not None]

    print(f"  poll clock {HEADER_TS:.0f} (the LIRR header); both committed captures served by")
    print("  the fake upstream inside the real fetch; then the app, over httpx.ASGITransport.")
    print()
    print(f"    {'trip id':<26}{'map provenance':>16}{'map train_num':>15}"
          f"{'board stop':>12}{'board train_num':>17}")
    for trip_id in sorted(estimated):
        row = estimated[trip_id]
        names = board_numbers(got["boards"].get(row["stop_id"], {"directions": {}}), trip_id)
        print(f"    {trip_id:<26}{row['provenance']:>16}{str(row['train_num']):>15}"
              f"{row['stop_id']:>12}{'/'.join(map(str, names)):>17}")
    print()
    print(f"  systems.LIRR.positions : {body['systems'].get('LIRR', {}).get('positions')}")
    print(f"  Metro-North placement rows (the control: its combined entity carries the vehicle"
          f" inline): {len(mnr_placed)},")
    print(f"    train numbers {[t['train_num'] for t in mnr_placed]}")

    check("served LIRR rows labelled estimated", sorted(estimated), sorted(six))
    check("each served with train_num null",
          {tid: t["train_num"] for tid, t in sorted(estimated.items())},
          {tid: None for tid in sorted(six)}, "null train numbers")
    check("the board at each estimate's stop names that train by its label",
          {tid: board_numbers(got["boards"].get(t["stop_id"], {"directions": {}}), tid)
           for tid, t in sorted(estimated.items())},
          {tid: [label] for tid, (label, _stop) in sorted(six.items())},
          "The arrivals board retains these numbers")
    check("the LIRR block's ladder counts, on the rider's envelope",
          body["systems"].get("LIRR", {}).get("positions"), EXPECT["lirr_positions"])
    check("no failed system", got["status"].get("railroad_feeds"),
          {"total": 2, "ok": 2, "failed": []})
    check("control: Metro-North's placement rows, every one named (the inline read works)",
          sorted(str(t["train_num"]) for t in mnr_placed), EXPECT["mnr_placed_numbers"])


# ---------------------------------------------------------------------------
# D. GPS -> estimated -> GPS
# ---------------------------------------------------------------------------


async def section_d() -> dict:
    rule("D. The correction's test: GPS -> estimated -> GPS, three real polls on one app")
    six = EXPECT["six"]
    fresh = restamped(LIRR_RAW, set(six), FRESH_FIX_S)
    stops = [stop for _label, stop in six.values()]
    prime()  # once: the three polls share one app, as a running server's would
    phases = (
        (f"fix {FRESH_FIX_S} s old", fresh),
        ("as captured", LIRR_RAW),
        (f"fix {FRESH_FIX_S} s old again", fresh),
    )
    served = []
    for _name, lirr in phases:
        served.append(await poll_and_serve(lirr, stops))

    print("  the six vehicles' fixes stamped as named, every other byte the capture's own;")
    print("  each cell is (provenance, train_num) on GET /api/railroads:")
    print()
    print(f"    {'trip id':<26}" + "".join(f"{name:>26}" for name, _ in phases))
    sequences: dict[str, list] = {}
    for trip_id in sorted(six):
        cells = []
        for got in served:
            row = lirr_rows(got["body"]).get(trip_id)
            cells.append((row["provenance"], row["train_num"]) if row else None)
        sequences[trip_id] = cells
        print(f"    {trip_id:<26}" + "".join(f"{str(cell):>26}" for cell in cells))
    print()
    for (name, _), got in zip(phases, served):
        print(f"  {name:<22} systems.LIRR.positions {got['body']['systems']['LIRR']['positions']}")
    board_mid = {
        tid: board_numbers(served[1]["boards"][stop], tid) for tid, (_l, stop) in six.items()
    }

    check("each of the six, poll by poll (provenance, train_num)",
          sequences,
          {tid: [("reported", label), ("estimated", None), ("reported", label)]
           for tid, (label, _stop) in sorted(six.items())},
          "Test GPS → estimated → GPS identity continuity")
    check("the served train number survives the middle poll for how many of the six",
          sum(1 for cells in sequences.values() if cells[1] and cells[1][1] is not None), 0)
    check("while the board names each of them at that same poll",
          {tid: board_mid[tid] for tid in sorted(board_mid)},
          {tid: [label] for tid, (label, _stop) in sorted(six.items())})
    check("ladder counts with the six fixes fresh",
          served[0]["body"]["systems"]["LIRR"]["positions"], EXPECT["lirr_positions_six_fresh"])
    check("and as captured, between them",
          served[1]["body"]["systems"]["LIRR"]["positions"], EXPECT["lirr_positions"])
    return sequences


# ---------------------------------------------------------------------------
# E. N2's fallback, the case PR #109 deferred
# ---------------------------------------------------------------------------


def section_e() -> None:
    rule("E. Beyond the audit's six: N2's box-rejected fallback, the case PR #109 deferred")
    moved = displaced(LIRR_RAW, DISPLACED)
    placed, arrivals = feeds._decode_railroad_feed(moved, "LIRR", LIRR_STOPS, HEADER_TS)
    row = next((t for t in placed if t["trip_id"] == DISPLACED), None)
    board = board_rows_by_trip(arrivals).get(DISPLACED, [])
    label = wire_labels(LIRR_FEED).get(DISPLACED)
    fix_age = next(
        int(HEADER_TS - e.vehicle.timestamp)
        for e in LIRR_FEED.entity
        if e.HasField("vehicle") and e.vehicle.trip.trip_id == DISPLACED
    )

    print(f"  {DISPLACED} (its fix {fix_age} s old against the header) moved to lat 0 / lon 0;")
    print("  the box rejects it before any age is read, so N2's fallback places its trip_update:")
    print(f"    wire label                : {label}")
    print(f"    placement row             : provenance {row and row['provenance']!r},"
          f" train_num {row and row['train_num']!r}, at stop {row and row['stop_id']}")
    print(f"    board rows for the trip   : {len(board)},"
          f" naming {sorted({r['train_num'] for _s, r in board}, key=str)}")

    check("the subject's own fix, against the header (fresh: only the box can reject it)",
          fix_age, EXPECT["displaced_fix_age_s"])
    check("the recovered trip is placed, labelled `placed`",
          row["provenance"] if row else None, "placed")
    check("its placement row's train_num", row["train_num"] if row else "absent", None)
    check("its wire label", label, [EXPECT["displaced_label"]])
    check("its board rows, every one naming the train",
          (len(board), all(r["train_num"] == EXPECT["displaced_label"] for _s, r in board)),
          (EXPECT["displaced_board_rows"], True))


async def run() -> tuple:
    await section_c()
    return await section_d()


def main_script() -> int:
    print("A11: estimated LIRR trains lose their train numbers")
    print(f"repository : {REPO}")
    print(f"DATA_DIR   : {os.environ['DATA_DIR']} (temporary)")
    print("upstreams  : in-memory fakes only; the MTA realtime seams point at loopback")
    labels = section_a()
    with mock.patch.object(time, "time", lambda: HEADER_TS):
        section_b(labels)
        sequences = asyncio.run(run())
        section_e()

    rule("HERMETIC")
    check("URLs naming an NJ Transit host requested", FORBIDDEN_URLS, [])
    check("URLs outside this world requested", UNEXPECTED_URLS, [])

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} check(s) no longer match the record:")
        for line in FAILURES:
            print(f"    - {line}")
        print()
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        return 1
    print("  every check matches the record.")
    print()
    six = EXPECT["six"]
    pairs = ", ".join(f"{tid} {label}" for tid, (label, _stop) in sorted(six.items()))
    print(
        f"DISPOSITION: VERIFIED  At the committed LIRR capture's header, /api/railroads serves "
        f"{len(sequences)} estimated LIRR rows with train_num null although each trip's vehicle "
        f"entity carries a label and the arrivals board names it ({pairs}); the 56 placed rows "
        "are null with no label to join, and through GPS -> estimated -> GPS each of the six "
        "is served label, null, label."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_script())
