#!/usr/bin/env python3
"""F01 (P1) reproduction: "Old individual LIRR GPS observations can look live."

WHAT THE AUDIT CLAIMED (section 1 of docs/reviews/audit-2026-09-05.md, verbatim):

    "The GPS decoder accepts a positioned vehicle inside the geographic bounds,
    discards its individual `vehicle.timestamp`, and leaves `now` unused. The
    frontend then receives a position accompanied by feed-level freshness, with
    no observation age.

    The capture contains 69 positioned LIRR vehicles. Relative to that capture's
    own feed header, 42 observations are over 90 seconds old, 32 over five
    minutes, and 25 over ten minutes. The oldest is 14 hours, 54 minutes, 36
    seconds old. Running the complete railroad aggregation against the committed
    LIRR/MNR captures still emits all 69 LIRR GPS records, with no failed
    systems."

    Remedy note: "Filtering the GPS output alone will not restore estimated
    positions: the placement pass independently classifies every positioned
    entity as GPS-equipped and suppresses its schedule placement."

WHAT THIS SCRIPT MEASURES, AND HOW

  A. Decoder behavior. Calls the real backend.feeds.railroad._decode_railroad_vehicles
     three times with three wildly different `now` values, and again over
     in-memory rewrites of the capture in which every vehicle.timestamp is
     replaced. If the outputs are byte-identical in all cases, the decoder
     provably reads neither `now` nor the per-vehicle observation time.
  B. The five numbers. Re-derives every count from the committed capture
     backend/tests/fixtures/railroad_lirr.pb, measuring each vehicle.timestamp
     against THAT CAPTURE'S OWN FeedHeader.timestamp. Today's wall clock is
     never used for an age.
  C. The complete aggregation. Drives the real poller backend.pollers._refresh_railroads
     (which calls the real fetch_railroad_trains) over both committed captures
     through an httpx.MockTransport, with the committed per-system stops loaded
     so the placement pass runs too.
  D. The served JSON. Drives the real FastAPI app over httpx.ASGITransport and
     reads GET /api/railroads, so the per-vehicle field set comes from the real
     route and its real response_model, not from prose.
  E. The remedy note. Calls the real _decode_railroad_placements to show the
     placement pass keeps its own notion of "has GPS", including the decisive
     case where the two passes disagree.

  PRODUCTION CODE EXERCISED: feeds.railroad._decode_railroad_vehicles,
  feeds.railroad._decode_railroad_placements, feeds.railroad.fetch_railroad_trains,
  feeds.shared._in_railroad_box, pollers._refresh_railroads, models.RailroadFeed /
  models.RailroadTrain, routes.railroad.get_railroads.

  INJECTED / SIMULATED (all in memory, the committed fixtures are never written):
  1. the two HTTP responses, served from the committed .pb captures by a
     MockTransport, so nothing reaches the MTA;
  2. time.time frozen to the LIRR capture's own header for the duration of the
     aggregation, so the placement half is deterministic instead of depending on
     the day the script runs;
  3. in section A, copies of the capture with every vehicle.timestamp rewritten;
  4. in section E, a copy of the capture with the stale vehicles' entities
     removed, and a copy with one vehicle's coordinates moved outside the
     railroad bounding box.

  HERMETIC: no network, no credentials, no NJ Transit host, no dependence on the
  current date. Ages are capture-internal throughout.

RECORDED DISPOSITION: VERIFIED. The script exits 0 while the finding still
behaves as recorded and non-zero the moment any part of it changes.

RUN (from the repository root):

    ./.venv/bin/python docs/reviews/audit-2026-09-05/f01_lirr_gps_observation_age.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FIXTURES = BACKEND / "tests" / "fixtures"
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

import feeds  # noqa: E402  (the feeds package re-exports the railroad decoders)
import main as app_module  # noqa: E402  (the real FastAPI app plus its poller seams)
import models  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()


FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    """Record one assertion. Every check runs, then the script exits on the tally."""
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(': ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def parsed(raw: bytes) -> pb.FeedMessage:
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    return feed


def hms(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600}h {(total % 3600) // 60:02d}m {total % 60:02d}s"


LIRR_RAW = (FIXTURES / "railroad_lirr.pb").read_bytes()
MNR_RAW = (FIXTURES / "railroad_mnr.pb").read_bytes()
STOPS = {
    system: json.loads((FIXTURES / f"railroad_{system.lower()}_stops.json").read_text())
    for system in ("LIRR", "MNR")
}
LIRR_FEED = parsed(LIRR_RAW)
HEADER_TS = float(LIRR_FEED.header.timestamp)

# THE ONE TRIP F02's FIX REMOVES FROM THE SERVED OUTPUT, named here because F01's
# served counts move by exactly it and by nothing else. It is a canceled trip that
# also carries a positioned vehicle, and its observation is 50517 s old, so it was
# both one of this capture's 69 positioned vehicles and one of its 42 stale ones. F01
# is unchanged in substance: 41 stale observations are still published with no
# per-vehicle age. See f02_canceled_railroad_gps.py.
CANCELED_BY_F02 = "6004XX_2026-06-20"


# ---------------------------------------------------------------------------
# Section A: does the decoder read the individual observation time, or `now`?
# ---------------------------------------------------------------------------


def section_a() -> None:
    rule("A. What the real GPS decoder reads (production: _decode_railroad_vehicles)")

    baseline, feed_ts = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", HEADER_TS)
    at_zero, _ = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", 0.0)
    at_far_future, _ = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", 4.0e9)
    print(f"  decoded trains at now=header ({HEADER_TS:.0f}): {len(baseline)}")
    print(f"  decoded trains at now=0.0                    : {len(at_zero)}")
    print(f"  decoded trains at now=4000000000.0           : {len(at_far_future)}")
    check(
        "`now` is unused: three now values give byte-identical output",
        baseline == at_zero == at_far_future,
    )
    check(
        "the decoder returns the feed header time as the only timestamp",
        feed_ts == HEADER_TS,
        f"feed_timestamp={feed_ts:.0f}",
    )

    # Rewrite every vehicle.timestamp in memory. If the decode is unchanged, the
    # individual observation time provably reaches nothing in the output.
    for label, replacement in (("zeroed", 0), ("set to the header", int(HEADER_TS))):
        mutated = parsed(LIRR_RAW)
        touched = 0
        for entity in mutated.entity:
            if entity.HasField("vehicle"):
                entity.vehicle.timestamp = replacement
                touched += 1
        rewritten, _ = feeds._decode_railroad_vehicles(mutated.SerializeToString(), "LIRR", HEADER_TS)
        check(
            f"vehicle.timestamp is discarded: {touched} timestamps {label}, output identical",
            rewritten == baseline,
        )

    emitted_fields = sorted(baseline[0])
    print(f"  fields the decoder emits per train: {emitted_fields}")
    age_words = ("timestamp", "observed", "age", "seen", "measured")
    check(
        "no emitted per-train field names an observation time",
        not [f for f in emitted_fields if any(word in f for word in age_words)],
    )

    model_fields = sorted(models.RailroadTrain.model_fields)
    print(f"  models.RailroadTrain fields       : {model_fields}")
    # CONTRACT 6.1 MOVED THIS HALF AND NOT THE OTHER. The audit recorded that the
    # model carried no observation age at all; 6.1 gave it observed_at and
    # provenance and filled neither, which is the step's whole point (it produces
    # the values and nothing consumes them yet). So the model half of the finding
    # is closed and the DECODER half is not, and this script now pins that split
    # rather than the original conjunction: an assertion that quietly kept passing
    # across a change this size would be worth nothing.
    check(
        "models.RailroadTrain now carries the contract pair (6.1)",
        {"observed_at", "provenance"} <= set(model_fields),
    )
    check(
        "and the decoder still fills neither, so F01 stands",
        not [f for f in emitted_fields if any(word in f for word in age_words)],
    )


# ---------------------------------------------------------------------------
# Section B: re-derive the five numbers from the capture's own header
# ---------------------------------------------------------------------------


def section_b() -> dict[str, float]:
    rule("B. The five numbers, re-derived against the capture's own feed header")

    positioned = [
        e for e in LIRR_FEED.entity if e.HasField("vehicle") and e.vehicle.HasField("position")
    ]
    in_box = [
        e
        for e in positioned
        if feeds._in_railroad_box(e.vehicle.position.latitude, e.vehicle.position.longitude)
    ]
    decoded, _ = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", HEADER_TS)
    stamped = [e for e in in_box if e.vehicle.timestamp]

    ages: dict[str, float] = {}
    for entity in in_box:
        trip_id = entity.vehicle.trip.trip_id or entity.id
        ages[trip_id] = HEADER_TS - float(entity.vehicle.timestamp)
    ordered = sorted(ages.values())

    over_90 = sum(1 for a in ordered if a > 90)
    over_300 = sum(1 for a in ordered if a > 300)
    over_600 = sum(1 for a in ordered if a > 600)
    oldest = ordered[-1]

    print("  capture                    : backend/tests/fixtures/railroad_lirr.pb")
    print(f"  FeedHeader.timestamp       : {HEADER_TS:.0f} (the only clock used below)")
    print(f"  entities with vehicle      : {sum(1 for e in LIRR_FEED.entity if e.HasField('vehicle'))}")
    print(f"  ... carrying a position    : {len(positioned)}")
    print(f"  ... inside the railroad box: {len(in_box)}")
    print(f"  ... with a nonzero stamp   : {len(stamped)}")
    print(f"  trains the decoder emits   : {len(decoded)}")
    print(f"  distinct trip ids among    : {len(ages)}")
    print()
    print("  observation age = FeedHeader.timestamp - vehicle.timestamp")
    print(f"    freshest                 : {ordered[0]:>8.0f} s")
    print(f"    median                   : {ordered[len(ordered) // 2]:>8.0f} s")
    print(f"    oldest                   : {oldest:>8.0f} s  = {hms(oldest)}")
    print()
    print("    bucket            count   (audit said)")
    print(f"    age >    90 s  :  {over_90:>4}    42")
    print(f"    age >   300 s  :  {over_300:>4}    32")
    print(f"    age >   600 s  :  {over_600:>4}    25")
    print(f"    age <=   90 s  :  {len(ordered) - over_90:>4}    (the rest)")
    print()
    print("    boundary check (>= instead of >, so no off-by-one hides here):")
    print(f"      >=  90 s: {sum(1 for a in ordered if a >= 90)}"
          f"   >= 300 s: {sum(1 for a in ordered if a >= 300)}"
          f"   >= 600 s: {sum(1 for a in ordered if a >= 600)}")
    print()
    print("    the five oldest observations in the capture:")
    for trip_id, age in sorted(ages.items(), key=lambda kv: -kv[1])[:5]:
        print(f"      {trip_id:<24} {age:>8.0f} s  = {hms(age)}")

    check("positioned LIRR vehicles == 69", len(positioned) == 69, str(len(positioned)))
    # 69 IN THE BOX, 68 DECODED, AND THE ONE DIFFERENCE IS NOT F01's. F02 was fixed on
    # claude/release1-small-fixes: the canceled trip 6004XX_2026-06-20 is dropped
    # before emission, and it happens to be one of the stale observations (50517 s).
    # The wire numbers below are untouched, because F01 is a statement about what the
    # CAPTURE contains and the audit measured it there; only the served counts move,
    # and they move by exactly that one trip.
    check("all 69 are inside the railroad box", len(in_box) == 69, str(len(in_box)))
    check(
        "68 decode: the 69 in the box less the one F02 now drops as canceled",
        len(decoded) == 68 and CANCELED_BY_F02 not in {t["trip_id"] for t in decoded},
        f"decoded={len(decoded)}",
    )
    check("observations over 90 seconds old == 42", over_90 == 42, str(over_90))
    check("observations over five minutes old == 32", over_300 == 32, str(over_300))
    check("observations over ten minutes old == 25", over_600 == 25, str(over_600))
    check(
        "oldest observation == 53676 s (14h 54m 36s)",
        int(oldest) == 53676,
        f"{oldest:.0f} s = {hms(oldest)}",
    )
    check("no observation postdates the header", ordered[0] >= 0, f"freshest {ordered[0]:.0f} s")
    return ages


# ---------------------------------------------------------------------------
# Sections C and D: the complete aggregation, and the JSON a rider's browser gets
# ---------------------------------------------------------------------------


async def section_c_and_d(ages: dict[str, float]) -> None:
    rule("C. The complete railroad aggregation over both committed captures")

    by_url = {
        feeds.RAILROAD_FEED_URLS["LIRR"]: LIRR_RAW,
        feeds.RAILROAD_FEED_URLS["MNR"]: MNR_RAW,
    }

    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=by_url[str(request.url)])

    app = app_module.app
    app.state.feed_cache = {
        name: app_module._fresh_entry()
        for name in ("buses", "subways", "railroads", "path", "ferry", "njt")
    }
    app.state.railroad_stops = STOPS
    app.state.railroad_feed_health = None

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        # Freeze the clock to the LIRR capture's own header: the GPS half ignores
        # `now` (section A), and freezing makes the placement half deterministic.
        with unittest.mock.patch("time.time", lambda: HEADER_TS):
            await app_module._refresh_railroads(app, client=client)

    entry = app.state.feed_cache["railroads"]
    data = entry["data"]
    lirr = [t for t in data if t["system"] == "LIRR"]
    lirr_gps = [t for t in lirr if t["stop_id"] is None]
    lirr_placed = [t for t in lirr if t["stop_id"] is not None]
    mnr = [t for t in data if t["system"] == "MNR"]
    health = app.state.railroad_feed_health

    print("  poller                     : pollers._refresh_railroads (real)")
    print("  transport                  : httpx.MockTransport over the committed .pb captures")
    print(f"  cache error                : {entry['error']}")
    print(f"  feed health                : {health}")
    print(f"  envelope feed_timestamp    : {entry['feed_timestamp']:.0f} (LIRR header)")
    print(f"  trains published           : {len(data)}  (LIRR {len(lirr)}, MNR {len(mnr)})")
    print(f"    LIRR GPS records         : {len(lirr_gps)}")
    print(f"    LIRR placed records      : {len(lirr_placed)}")

    published_gps_ids = {t["trip_id"] for t in lirr_gps}
    stale_ids = {tid for tid, age in ages.items() if age > 90}
    survivors = stale_ids & published_gps_ids
    oldest_id = max(ages, key=lambda tid: ages[tid])
    print(f"  of the 42 stale observations, still published as GPS: {len(survivors)}")
    print(f"  the 42nd is {CANCELED_BY_F02}, dropped as CANCELED by F02's fix rather")
    print("  than for its age; F01's finding is the other 41, which are published with")
    print("  no per-vehicle age of any kind.")
    print(f"  the 14h 54m 36s observation ({oldest_id}) is published: {oldest_id in published_gps_ids}")

    check("no failed systems", health == {"total": 2, "ok": 2, "failed": []}, str(health))
    check("the cache records no error", entry["error"] is None)
    check("68 LIRR GPS records are emitted (69 less F02's canceled trip)",
          len(lirr_gps) == 68, str(len(lirr_gps)))
    check(
        "41 of the 42 stale observations are published; the 42nd is F02's canceled trip",
        len(survivors) == 41 and (stale_ids - survivors) == {CANCELED_BY_F02},
        f"survivors={len(survivors)} unpublished={sorted(stale_ids - survivors)}",
    )
    check("the envelope's feed_timestamp is the LIRR header", entry["feed_timestamp"] == HEADER_TS)

    rule("D. What the served JSON exposes per vehicle (real app, real response_model)")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://f01.local") as client:
        response = await client.get("/api/railroads")
        body = response.json()

    oldest_record = next(t for t in body["data"] if t["trip_id"] == oldest_id)
    freshest_id = min(ages, key=lambda tid: ages[tid])
    freshest_record = next(t for t in body["data"] if t["trip_id"] == freshest_id)
    per_vehicle_keys = sorted(oldest_record)

    print(f"  GET /api/railroads          -> HTTP {response.status_code}")
    print(f"  envelope keys               : {sorted(body)}")
    print(f"  envelope freshness          : fetched_at={body['fetched_at']:.0f}, "
          f"feed_timestamp={body['feed_timestamp']:.0f}, served_at={body['served_at']:.0f}")
    print(f"  per-vehicle keys            : {per_vehicle_keys}")
    print()
    print("  two vehicles, side by side, as the browser receives them:")
    print(f"    oldest   trip {oldest_record['trip_id']:<22} train {oldest_record['train_num']}, "
          f"lat/lon {oldest_record['latitude']:.5f}/{oldest_record['longitude']:.5f}")
    print(f"             true observation age in the capture: {hms(ages[oldest_id])}")
    print(f"    freshest trip {freshest_record['trip_id']:<22} train {freshest_record['train_num']}, "
          f"lat/lon {freshest_record['latitude']:.5f}/{freshest_record['longitude']:.5f}")
    print(f"             true observation age in the capture: {hms(ages[freshest_id])}")
    print("    difference in the JSON that distinguishes them by age: none of the 14 keys.")

    # 6.1 again: the KEY is on the served record now, and it is empty on every row.
    # An empty field is not a freshness signal, so the rider-facing defect is
    # unchanged and the two vehicles below still differ in nothing.
    check(
        "the served vehicle record carries observed_at (6.1)",
        "observed_at" in per_vehicle_keys,
        str(per_vehicle_keys),
    )
    check(
        "and it is None on every served vehicle, so it distinguishes nothing",
        all(rec.get("observed_at") is None for rec in body["data"]),
    )
    check(
        "the oldest and the freshest vehicle differ in no freshness key",
        {k: v for k, v in oldest_record.items() if k not in ("trip_id", "route_id", "train_num",
                                                             "latitude", "longitude", "bearing")}
        == {k: v for k, v in freshest_record.items() if k not in ("trip_id", "route_id", "train_num",
                                                                  "latitude", "longitude", "bearing")},
    )
    check(
        "the only freshness on the response is feed level",
        sorted(body) == ["data", "feed_timestamp", "fetched_at", "served_at", "systems"],
    )

    # THE INVERSE OF THE ORIGINAL CHECK, and the clearest single statement of what
    # 6.1 changed. The audit found that the response model did not merely lack the
    # field, it STRIPPED one that was added: a value put on a cached train never
    # reached the wire. It does now. That is the whole of the models step, measured
    # end to end, and it is why the decoder step can fill the field and expect it to
    # arrive.
    injected = HEADER_TS - ages[oldest_id]
    entry["data"] = [{**lirr_gps[0], "observed_at": injected}]
    async with httpx.AsyncClient(transport=transport, base_url="http://f01.local") as client:
        carried = (await client.get("/api/railroads")).json()["data"][0]
    print(f"  an observed_at added to a cached train survives the response model: "
          f"{'observed_at' in carried}")
    check(
        "models.RailroadFeed now CARRIES an added observed_at (6.1; it stripped it before)",
        carried.get("observed_at") == injected,
    )


# ---------------------------------------------------------------------------
# Section E: the remedy note about the placement pass
# ---------------------------------------------------------------------------


def section_e(ages: dict[str, float]) -> None:
    rule("E. The remedy note: does the placement pass have its own notion of GPS?")

    stops = STOPS["LIRR"]
    stale_ids = {tid for tid, age in ages.items() if age > 90}
    fresh_ids = set(ages) - stale_ids

    placed = feeds._decode_railroad_placements(LIRR_RAW, "LIRR", stops, HEADER_TS)
    placed_ids = {t["trip_id"] for t in placed}
    print("  real capture, now frozen to the header:")
    print(f"    GPS records                        : {len(ages)}")
    print(f"    schedule-placed records            : {len(placed)}")
    print(f"    stale trips that got a placement   : {len(placed_ids & stale_ids)}")

    # Simulate the remedy the note warns about: filter the GPS OUTPUT only.
    print()
    print("  simulating 'filter the GPS output alone' (drop the 42 stale GPS records):")
    print(f"    GPS records surviving the filter   : {len(fresh_ids)}")
    print(f"    placements unchanged at            : {len(placed)}")
    positioned_anywhere = (fresh_ids | placed_ids) & stale_ids
    print(f"    of the 42 filtered trains, how many get any position instead: "
          f"{len(positioned_anywhere)}")
    check(
        "filtering the GPS output alone restores no estimated position",
        len(positioned_anywhere) == 0,
    )

    # Now show the placements those trips WOULD have had, by removing their
    # positioned entities from an in-memory copy (injected fault, not the fixture).
    trimmed = pb.FeedMessage()
    trimmed.header.CopyFrom(LIRR_FEED.header)
    dropped = 0
    for entity in LIRR_FEED.entity:
        is_stale_vehicle = (
            entity.HasField("vehicle")
            and entity.vehicle.HasField("position")
            and (entity.vehicle.trip.trip_id or entity.id) in stale_ids
        )
        if is_stale_vehicle:
            dropped += 1
            continue
        trimmed.entity.add().CopyFrom(entity)
    recovered = feeds._decode_railroad_placements(
        trimmed.SerializeToString(), "LIRR", stops, HEADER_TS
    )
    recovered_ids = {t["trip_id"] for t in recovered}
    print()
    print(f"  injected: the {dropped} stale positioned entities removed from an in-memory copy:")
    print(f"    schedule-placed records            : {len(recovered)} (was {len(placed)})")
    print(f"    of the 42, now placed at a station : {len(recovered_ids & stale_ids)}")
    check(
        "the placement pass reads the feed, not the GPS output: 15 of the 42 have a "
        "usable prediction it suppresses",
        len(recovered_ids & stale_ids) == 15,
        str(len(recovered_ids & stale_ids)),
    )

    # WHAT USED TO BE THE DECISIVE DISAGREEMENT, AND IS NOW THE FIX (N2). The GPS
    # decoder applied the railroad bounding box and the placement pass did not, so a
    # vehicle moved outside the box was dropped by both passes from opposite sides and
    # reached no surface at all. Both passes now read one acceptance rule
    # (feeds.railroad._accepted_as_gps), so the same injection routes the trip to
    # placement instead of off the map. The injection is kept exactly as the audit ran
    # it; only the expectations below moved, and each carries what the audit measured.
    oldest_id = max(ages, key=lambda tid: ages[tid])
    moved = pb.FeedMessage()
    moved.CopyFrom(LIRR_FEED)
    for entity in moved.entity:
        if (
            entity.HasField("vehicle")
            and entity.vehicle.HasField("position")
            and entity.vehicle.trip.trip_id == oldest_id
        ):
            entity.vehicle.position.latitude = 0.0
            entity.vehicle.position.longitude = 0.0
    moved_raw = moved.SerializeToString()
    moved_gps, _ = feeds._decode_railroad_vehicles(moved_raw, "LIRR", HEADER_TS)
    moved_placed = feeds._decode_railroad_placements(moved_raw, "LIRR", stops, HEADER_TS)
    moved_gps_ids = {t["trip_id"] for t in moved_gps}
    moved_placed_ids = {t["trip_id"] for t in moved_placed}
    print()
    print(f"  injected: trip {oldest_id} (the 14h 54m 36s one) moved to lat 0 / lon 0:")
    # The baseline is what THIS decoder emits from the untouched capture (68 since
    # F02's fix), not the 69 observations on the wire: the injected fault removes one
    # more, and comparing against the wire count would silently absorb F02's drop.
    baseline_gps, _ = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", HEADER_TS)
    print(f"    GPS records                        : {len(moved_gps)} (was {len(baseline_gps)}), "
          f"that trip present: {oldest_id in moved_gps_ids}")
    print(f"    schedule-placed records            : {len(moved_placed)} (was {len(placed)}), "
          f"that trip placed: {oldest_id in moved_placed_ids}")
    print("    the GPS decoder rejects it on the bounding box, and the placement pass now")
    print("    reads that same rejection and places it at its next station. One notion.")
    check(
        "the box-rejected vehicle leaves the GPS output",
        len(moved_gps) == len(baseline_gps) - 1 and oldest_id not in moved_gps_ids,
    )
    check(
        "the placement pass picks it up (audit: it suppressed it, its own 'has GPS' set)",
        len(moved_placed) == len(placed) + 1 and oldest_id in moved_placed_ids,
    )
    check(
        "so it reaches exactly one surface (audit: it appeared on no surface at all)",
        (oldest_id in moved_gps_ids) + (oldest_id in moved_placed_ids) == 1,
    )
    check(
        "and nothing is drawn on both",
        not (moved_gps_ids & moved_placed_ids),
    )


def main() -> int:
    print("F01: old individual LIRR GPS observations can look live")
    print(f"repository : {REPO}")
    print(f"capture    : {FIXTURES / 'railroad_lirr.pb'}")
    print("clock      : the capture's own FeedHeader.timestamp, never today's date")

    section_a()
    ages = section_b()
    asyncio.run(section_c_and_d(ages))
    section_e(ages)

    rule("Result")
    if FAILURES:
        print(f"  {len(FAILURES)} check(s) no longer match the recorded disposition:")
        for name in FAILURES:
            print(f"    - {name}")
        print()
        print("DISPOSITION MISMATCH: reality has changed since the audit record was written; "
              "re-verify F01 and update docs/reviews/audit-2026-09-05.md.")
        return 1
    print("  every check matches the recorded disposition.")
    print()
    print("DISPOSITION: VERIFIED  All four claims hold: the decoder ignores `now` and "
          "discards vehicle.timestamp, the capture's 69 positioned LIRR vehicles include "
          "42 over 90 s, 32 over 5 min and 25 over 10 min with the oldest at 53676 s "
          "(14h 54m 36s), the full aggregation publishes 68 of them (the 69th is the trip "
          "F02's fix now drops as canceled, not an age filter) with no failed systems and "
          "no per-vehicle age in the served JSON, and the placement pass still does not "
          "rescue an age-stale train: it reads the FEED, so the 42 keep their positions "
          "and only 15 have a prediction to fall back to. Its notion of GPS is no longer "
          "independent, though: N2's fix gave both passes one acceptance rule, so a "
          "box-rejected vehicle now falls through to placement instead of vanishing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
