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

FIXED ON claude/freshness-6-3-positions, SO THIS SCRIPT NOW CHECKS THE FIX. Every check
that moved carries the audit's value in its label ("was: ..."), in the F02/F03 shape: it
exits 0 while the repaired behaviour holds and non-zero the moment it comes back. The
before-values are the audit's own measurement of this world: 69 positioned LIRR vehicles,
42 of them over 90 s, 32 over five minutes, 25 over ten minutes, the oldest 53676 s
(14h 54m 36s), and 68 of them served as live GPS with no per-vehicle age on any rider
surface. Contract 6.1 put the observation clock on every row and this script recorded,
until 6.3, that nothing gated on it and no surface read it.

THE WORLD IS THE ACCEPTANCE TEST'S, IMPORTED RATHER THAN COPIED, the way f03 imports its
own. backend/tests/test_f01_positions.py restamps both committed captures by ONE delta
onto one frozen clock and drives the real fetch_railroad_trains, _refresh_railroads and
the ASGI /api/railroads and /api/status; this script reuses its _Upstream, _restamp,
_stops and _poll_and_serve, its ESTIMATED set and its built step-4 world. Its app.state is
primed by a pytest fixture, which a script cannot call, so prime_app() below stands in for
exactly that fixture and nothing else here is reimplemented. The served body is compared to
tests/e2e/fixtures/f01_railroads.json, the handoff the browser half renders.
F01_WORLD_REGENERATE IS NEVER SET HERE: a record that can rewrite its own golden records
nothing.

WHAT THIS SCRIPT MEASURES, AND HOW

  A. The capture, exactly as the audit measured it, every age against that capture's own
     FeedHeader.timestamp. Nothing in this section moves: it is the wire, and it is what
     makes the rest a fix rather than a recapture.
  B. What the real GPS decoder does with those same bytes now, with the committed stops
     passed at every call site (without them the ladder cannot ask the placement pass for
     an estimate and the decoder answers 44 rows instead of 38), plus what the gate's
     clock is: the feed header, so no age moves with `now`.
  C. The served body, through the real poll and the real app, compared to the committed
     handoff fixture: 136 records, their provenance by system, and each system's counts.
  D. Section 3.4's cost table, measured against that same served body.
  E. The remedy note, now answered: the gate is not a filter on the GPS output but one
     rule both passes read, so the six trips it takes off that surface arrive on the
     other one labeled `estimated`, the 24 with nothing honest left are absent AND
     counted, N2's box case is unchanged, and step 4 fires in the acceptance world's
     built feed.
  F. What the placement surface rides, which the gate does not reach: the open question's
     own numbers, re-derived here so the ledger entry and the capture cannot drift apart.

  PRODUCTION CODE EXERCISED: feeds.railroad._position_ladder, _position_steps,
  _accepted_as_gps, _passes_base_rule, _decode_railroad_vehicles,
  _decode_railroad_placements, feeds.fetch_railroad_trains, feeds.shared._in_railroad_box,
  pollers._refresh_railroads, models.RailroadTrain / SystemFreshness / PositionSteps,
  routes.railroad.get_railroads and routes.status.

  INJECTED / SIMULATED (all in memory, the committed fixtures are never written):
  1. the acceptance world's _restamp DELTA on both captures, which moves every time either
     feed carries and nothing else, so each capture's own ages survive intact;
  2. the two upstream bodies, served by world._Upstream inside the real fetch, so no
     socket is opened and no NJ Transit host is ever named;
  3. time.time frozen to world.NOW (FROZEN_S of tests/e2e/fixtures/api.js) for the
     duration of both polls;
  4. in section B, the same bytes decoded at now=0.0 and at now=4.0e9;
  5. in section E, one vehicle moved to lat 0 / lon 0 (the audit's own experiment), a copy
     of the capture with its 42 stale positioned entities removed, and the acceptance
     world's built step-4 feed.

  HERMETIC: no network, no credentials, no NJ Transit host, no dependence on the current
  date. Every age is capture-internal or read against the world's frozen clock.

RECORDED DISPOSITION: FIXED. The script exits 0 while the repaired behaviour holds and
non-zero, naming every number that moved, the moment it comes back.

RUN (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-09-05/f01_lirr_gps_observation_age.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
from pathlib import Path
from unittest import mock

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
sys.path.insert(0, str(BACKEND / "tests"))

import test_f01_positions as world  # noqa: E402  (the acceptance world, not a copy)
from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import cache  # noqa: E402
import feeds  # noqa: E402  (the feeds package re-exports the railroad decoders)
import feeds.railroad as railroad_mod  # noqa: E402
import main as app_module  # noqa: E402  (the real FastAPI app plus its poller seams)
import models  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()


BRANCH = "claude/freshness-6-3-positions"

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    """Record one assertion as measured against recorded, f02's shape. Every check runs,
    then the script fails once, naming every number that moved."""
    ok = got == want
    if not ok:
        FAILURES.append(f"{label}: measured {got!r}, the record says {want!r}")
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: {got!r}")


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def hms(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600}h {(total % 3600) // 60:02d}m {total % 60:02d}s"


LIRR_RAW = (FIXTURES / "railroad_lirr.pb").read_bytes()
MNR_RAW = (FIXTURES / "railroad_mnr.pb").read_bytes()
LIRR_STOPS = world._stops("LIRR")
MNR_STOPS = world._stops("MNR")
LIRR_FEED = pb.FeedMessage.FromString(LIRR_RAW)
HEADER_TS = float(LIRR_FEED.header.timestamp)
GPS_GOLDEN = json.loads((FIXTURES / "railroad_lirr_expected.json").read_text())
PLACED_GOLDEN = json.loads((FIXTURES / "railroad_lirr_placed_expected.json").read_text())

# THE ONE TRIP F02's FIX REMOVES BEFORE THE GATE IS ASKED ANYTHING, named here because the
# base rule's population is the 69 on the wire less exactly it. It is a canceled trip that
# also carries a positioned vehicle. See f02_canceled_railroad_gps.py.
CANCELED_BY_F02 = "6004XX_2026-06-20"

# THE VEHICLE SECTION E DISPLACES, and it is deliberately NOT the audit's. The audit moved
# the oldest fix (6006_2026-06-20, 53676 s) out of the railroad box; the gate now withholds
# that vehicle at step 5 before the box is ever asked, so displacing it would measure the
# gate rather than N2. GO201_26_7588 is the freshest positioned vehicle on the capture
# (train 7344, its fix 4 s old, ladder step 1), which is what
# test_feeds_railroad.py::test_an_out_of_range_lirr_vehicle_falls_back_to_placement now
# displaces for the same reason.
DISPLACED = "GO201_26_7588"

# RECORDED EXPECTATIONS, each asserted below, each carrying the audit's own value where the
# fix moved it. A changed number fails the run with a message rather than quietly rewriting
# the audit record.
EXPECT = {
    # ---- the wire, which the fix does not touch and section A re-derives ----
    "positioned_on_the_wire": 69,
    "wire_over_90_s": 42,
    "wire_over_300_s": 32,
    "wire_over_600_s": 25,
    "wire_oldest_s": 53676,
    "wire_oldest_trip": "6006_2026-06-20",
    # The population the gate acts on: the 69 positioned vehicles less F02's canceled one.
    "base_rule_accepts": 68,
    # ---- the GPS surface ----
    # 38, was 68. The GPS pass serves only what the ladder draws at a vehicle's own
    # position, steps 1 and 3; the other 30 are 6 estimates and 24 withheld.
    "gps_rows": 38,
    "golden_gps_records": 38,
    "gated_off_the_gps_surface": 30,
    # Decoded WITHOUT the stops the live path passes, the ladder cannot ask whether a
    # prediction is placeable, so step 2 never fires and the six are drawn qualified: 44.
    "gps_rows_without_stops": 44,
    # 593 s, was 53676 s (14h 54m 36s). The oldest fix any rider surface now carries.
    "oldest_served_fix_s": 593,
    "freshest_served_fix_s": 4,
    "served_within_90_s": 27,
    # 11 rows past 90 s, and every one of them dimmed and dated: was 41 reaching a rider
    # drawn exactly like a 4 s fix.
    "served_over_90_s_qualified": 11,
    "served_over_90_s_unqualified": 0,
    # 0, was 25. Nothing past the operator band reaches the map at all.
    "served_over_600_s": 0,
    # ---- the ladder's counts ----
    "lirr_steps": {"reported": 27, "estimated": 6, "qualified": 11, "placed": 0, "suppressed": 24},
    "mnr_steps": {"reported": 33, "estimated": 0, "qualified": 0, "placed": 0, "suppressed": 0},
    # ---- the served body ----
    "api_records": 136,
    "api_lirr_reported": 38,
    "api_lirr_estimated": 6,
    "api_lirr_placed": 56,
    "api_mnr_reported": 33,
    "api_mnr_placed": 3,
    # 16, and this script said 14 until 6.1 added observed_at and provenance.
    "api_row_keys": 16,
    # ---- the remedy note ----
    # 6, where the audit measured 0 estimated positions restored. 15 is the no-limit
    # figure: with the 42 stale vehicles removed entirely, 15 of them have a placement of
    # any age behind them, and the ledger's "15 of the 42" is that number, not this one.
    "estimates_at_obs_fresh": 6,
    "placements_with_no_age_limit": 15,
    "withheld_and_counted": 24,
    # The displacement, against this capture's own baseline: 38 -> 37 and 62 -> 63.
    "displaced_gps_rows": 37,
    "displaced_placement_rows": 63,
    "displaced_steps": {
        "reported": 26,
        "estimated": 6,
        "qualified": 11,
        "placed": 1,
        "suppressed": 24,
    },
    "step_4_steps": {
        "reported": 27,
        "estimated": 6,
        "qualified": 11,
        "placed": 1,
        "suppressed": 23,
    },
    # ---- the placement surface the gate does not reach (open question 1) ----
    "placement_rows": 62,
    "placed_rows": 56,
    "placed_over_90_s": 53,
    "placed_over_600_s": 49,
    "placed_median_s": 1778,
    "placed_freshest_s": 7,
    "placed_oldest_s": 7255,
    "estimate_ages_s": [4, 4, 4, 4, 4, 5],
}


def prime_app() -> None:
    """The app.state a lifespan would have built for the railroad, and the state each poll
    writes back into, reset.

    THIS STANDS IN FOR test_f01_positions.py's f01_world FIXTURE, which primes exactly
    these six attributes and patches the clock. That fixture takes pytest's monkeypatch, so
    a script cannot call it; everything it primes is set here instead, by the same names
    and to the same values, and the clock is patched by frozen_clock() below. Called again
    before the second poll, because feed_cache and railroad_positions carry a poll's answer
    forward and the built step-4 world must not inherit the committed one's.
    """
    state = app_module.app.state
    state.feed_cache = {"railroads": app_module._fresh_entry()}
    state.railroad_feed_health = None
    state.railroad_static_status = "ready"
    state.railroad_stops = {system: world._stops(system) for system in feeds.RAILROAD_FEED_URLS}
    state.railroad_arrivals = {}
    state.railroad_positions = {}


def frozen_clock():
    """time.time pinned to the world's NOW, which is the browser suite's frozen instant and
    the clock the restamped LIRR header sits exactly on."""
    return mock.patch.object(app_module.time, "time", lambda: world.NOW)


async def poll_the_world() -> tuple[dict, dict, dict, dict]:
    """Two real polls at that frozen clock: the committed world, then the acceptance
    world's built step-4 feed (one of the 24 given a prediction 300 s old)."""
    prime_app()
    body, status = await world._poll_and_serve(
        world._Upstream(world._restamp(world._raw("LIRR")), world._restamp(world._raw("MNR")))
    )
    prime_app()
    step4_body, step4_status = await world._poll_and_serve(
        world._Upstream(
            world._with_a_usable_old_prediction(world._restamp(world._raw("LIRR"))),
            world._restamp(world._raw("MNR")),
        )
    )
    return body, status, step4_body, step4_status


def displaced(raw: bytes, trip_id: str) -> bytes:
    """A copy of the capture with one vehicle moved to lat 0 / lon 0, which is the audit's
    own injection: (0, 0) is in the Gulf of Guinea, the canonical garbage coordinate."""
    feed = pb.FeedMessage.FromString(raw)
    for entity in feed.entity:
        if (
            entity.HasField("vehicle")
            and entity.vehicle.HasField("position")
            and entity.vehicle.trip.trip_id == trip_id
        ):
            entity.vehicle.position.latitude = 0.0
            entity.vehicle.position.longitude = 0.0
    return feed.SerializeToString()


# ---------------------------------------------------------------------------
# A. the capture as the audit measured it
# ---------------------------------------------------------------------------


def section_a() -> dict[str, float]:
    rule("A. The capture, exactly as the audit measured it (the BEFORE, still true of the wire)")

    positioned = [
        e for e in LIRR_FEED.entity if e.HasField("vehicle") and e.vehicle.HasField("position")
    ]
    in_box = [
        e
        for e in positioned
        if feeds._in_railroad_box(e.vehicle.position.latitude, e.vehicle.position.longitude)
    ]
    canceled = railroad_mod._canceled_trip_ids(LIRR_FEED)
    accepted = {
        (e.vehicle.trip.trip_id or e.id)
        for e in in_box
        if railroad_mod._passes_base_rule(e, canceled)
    }
    ages = {
        (e.vehicle.trip.trip_id or e.id): HEADER_TS - float(e.vehicle.timestamp) for e in in_box
    }
    ordered = sorted(ages.values())

    over_90 = sum(1 for a in ordered if a > 90)
    over_300 = sum(1 for a in ordered if a > 300)
    over_600 = sum(1 for a in ordered if a > 600)
    oldest = ordered[-1]
    oldest_id = max(ages, key=lambda tid: ages[tid])

    print("  capture                    : backend/tests/fixtures/railroad_lirr.pb")
    print(f"  FeedHeader.timestamp       : {HEADER_TS:.0f} (the only clock used in this section)")
    print(f"  ... carrying a position    : {len(positioned)}")
    print(f"  ... inside the railroad box: {len(in_box)}")
    print(f"  ... the base rule accepts  : {len(accepted)} (the {len(in_box)} less F02's canceled")
    print(f"                               trip {CANCELED_BY_F02}, which the gate never sees)")
    print()
    print("  observation age = FeedHeader.timestamp - vehicle.timestamp")
    print(f"    freshest                 : {ordered[0]:>8.0f} s")
    print(f"    median                   : {statistics.median(ordered):>8.1f} s")
    print(f"    oldest                   : {oldest:>8.0f} s  = {hms(oldest)}")
    print()
    print("    bucket            count   (audit said)")
    print(f"    age >    90 s  :  {over_90:>4}    42")
    print(f"    age >   300 s  :  {over_300:>4}    32")
    print(f"    age >   600 s  :  {over_600:>4}    25")
    print(f"    age <=   90 s  :  {len(ordered) - over_90:>4}    (the rest)")
    print()
    print("    boundary check (>= instead of >, so no off-by-one hides here):")
    print(
        f"      >=  90 s: {sum(1 for a in ordered if a >= 90)}"
        f"   >= 300 s: {sum(1 for a in ordered if a >= 300)}"
        f"   >= 600 s: {sum(1 for a in ordered if a >= 600)}"
    )
    print()
    print("    the five oldest observations in the capture:")
    for trip_id, age in sorted(ages.items(), key=lambda kv: -kv[1])[:5]:
        print(f"      {trip_id:<24} {age:>8.0f} s  = {hms(age)}")

    check("positioned LIRR vehicles on the wire", len(positioned), EXPECT["positioned_on_the_wire"])
    check("all of them inside the railroad box", len(in_box), EXPECT["positioned_on_the_wire"])
    check("the base rule accepts, before any age is read", len(accepted),
          EXPECT["base_rule_accepts"])
    check("observations over 90 seconds old", over_90, EXPECT["wire_over_90_s"])
    check("observations over five minutes old", over_300, EXPECT["wire_over_300_s"])
    check("observations over ten minutes old", over_600, EXPECT["wire_over_600_s"])
    check(f"the oldest observation, {hms(oldest)}", int(oldest), EXPECT["wire_oldest_s"])
    check("the train carrying it", oldest_id, EXPECT["wire_oldest_trip"])
    check("no observation postdates the header", ordered[0] >= 0, True)
    return ages


# ---------------------------------------------------------------------------
# B. what the GPS decoder does with those same bytes
# ---------------------------------------------------------------------------


def section_b(ages: dict[str, float]) -> None:
    rule("B. What the GPS decoder now does with those same bytes: FIXED")

    gps, feed_ts = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", HEADER_TS, LIRR_STOPS)
    served = sorted(HEADER_TS - t["observed_at"] for t in gps)
    served_ids = {t["trip_id"] for t in gps}
    stale_ids = {tid for tid, age in ages.items() if age > 90}

    # THE STOPS ARE NOT OPTIONAL AT THIS CALL SITE, and a decode without them is the trap
    # this script itself fell into before the rewrite. The ladder asks the placement pass
    # whether a stale vehicle's trip can be estimated instead, and with no stops nothing is
    # placeable, so step 2 never fires and its six vehicles are drawn qualified at their own
    # positions. The live path passes the stops, so 38 is what a rider gets.
    without_stops, _ = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", HEADER_TS)
    extra = {t["trip_id"] for t in without_stops} - served_ids

    # THE GATE'S CLOCK IS THE FEED HEADER, NEVER `now` (memo D2), so no age moves when the
    # poll clock does. What `now` decides is placeability, which is step 2's second half:
    # at 0.0 every stop time in the capture is still ahead and the output is byte-identical,
    # and at 4.0e9 every stop is in the past, so the same six fall to step 3 and are drawn
    # qualified instead. Not one age changes between the two: the oldest served fix is 593 s
    # in both, where a clock-driven gate would have moved every one of them.
    at_zero, _ = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", 0.0, LIRR_STOPS)
    at_future, _ = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", 4.0e9, LIRR_STOPS)
    future_ages = sorted(HEADER_TS - t["observed_at"] for t in at_future)

    steps = railroad_mod._position_steps(LIRR_RAW, "LIRR", LIRR_STOPS, HEADER_TS)

    print(f"  _decode_railroad_vehicles(capture, 'LIRR', now=header, stops) : {len(gps)} rows")
    print(f"  feed_timestamp returned                                       : {feed_ts:.0f}")
    print(f"  the same call with no stops                                   : "
          f"{len(without_stops)} rows")
    print(f"  the same call at now=0.0                                      : "
          f"{len(at_zero)} rows, identical: {at_zero == gps}")
    print(f"  the same call at now=4.0e9                                    : "
          f"{len(at_future)} rows, oldest fix {future_ages[-1]:.0f} s")
    print()
    print("  the served observation ages, against the capture's own header:")
    print(f"    freshest                 : {served[0]:>8.0f} s")
    print(f"    median                   : {statistics.median(served):>8.1f} s")
    print(f"    oldest                   : {served[-1]:>8.0f} s   (was {EXPECT['wire_oldest_s']} s,"
          f" {hms(EXPECT['wire_oldest_s'])})")
    print(f"    within  90 s, unqualified: {sum(1 for a in served if a <= 90):>4}")
    print(f"    90 to 600 s, qualified   : {sum(1 for a in served if 90 < a <= 600):>4}"
          "   (dimmed, and each dated on the row)")
    print(f"    over   600 s             : {sum(1 for a in served if a > 600):>4}"
          f"   (was {EXPECT['wire_over_600_s']})")
    print()
    print(f"  ladder counts on this capture (_position_steps): {steps}")

    check("GPS rows served (was 68, the 69 on the wire less F02's canceled one)",
          len(gps), EXPECT["gps_rows"])
    check("the decoder returns the feed header as its only timestamp", feed_ts, HEADER_TS)
    check("positioned vehicles the gate moves off the GPS surface (was 0)",
          EXPECT["base_rule_accepts"] - len(gps), EXPECT["gated_off_the_gps_surface"])
    check("decoded with no stops the ladder cannot ask for an estimate: 6 more rows",
          (len(without_stops), extra == world.ESTIMATED),
          (EXPECT["gps_rows_without_stops"], True))
    check("the gate's clock is the header: at now=0.0 the output is byte-identical",
          at_zero == gps, True)
    check("at now=4.0e9 nothing is placeable, so only step 2 is lost and no age moves",
          (len(at_future), int(future_ages[-1])),
          (EXPECT["gps_rows_without_stops"], EXPECT["oldest_served_fix_s"]))
    check(f"the oldest served fix (was {EXPECT['wire_oldest_s']} s, train 521,"
          f" {EXPECT['wire_oldest_trip']})", int(served[-1]), EXPECT["oldest_served_fix_s"])
    check("the freshest served fix", int(served[0]), EXPECT["freshest_served_fix_s"])
    check("served rows within OBS_FRESH_S, drawn unqualified",
          sum(1 for a in served if a <= cache.OBS_FRESH_S), EXPECT["served_within_90_s"])
    check("served rows past OBS_FRESH_S, every one dimmed and dated (was 41, undated)",
          sum(1 for a in served if a > cache.OBS_FRESH_S), EXPECT["served_over_90_s_qualified"])
    check("served rows past OBS_MAX_S (was 25)",
          sum(1 for a in served if a > cache.OBS_MAX_S), EXPECT["served_over_600_s"])
    check("the oldest fix on the wire is no longer on the GPS surface",
          EXPECT["wire_oldest_trip"] in served_ids, False)
    check("of the audit's 42 stale observations, how many are still on the GPS surface,"
          " every one of them dimmed and dated",
          len(stale_ids & served_ids), EXPECT["served_over_90_s_qualified"])
    check("the decode equals the committed golden railroad_lirr_expected.json",
          gps == GPS_GOLDEN["trains"], True)
    check("golden records (was 68)", len(GPS_GOLDEN["trains"]), EXPECT["golden_gps_records"])
    check("the ladder's counts on this capture", steps, EXPECT["lirr_steps"])
    check("models.RailroadTrain carries the contract pair the gate and the rendering read",
          {"observed_at", "provenance"} <= set(models.RailroadTrain.model_fields), True)


# ---------------------------------------------------------------------------
# C. the served body
# ---------------------------------------------------------------------------


def section_c(body: dict, status: dict) -> None:
    rule("C. The served body, through the real poll and the real app")

    golden = json.loads(world.GOLDEN.read_text())
    lirr = [t for t in body["data"] if t["system"] == "LIRR"]
    mnr = [t for t in body["data"] if t["system"] == "MNR"]
    by_provenance = {
        system: {
            name: sum(1 for t in rows if t["provenance"] == name)
            for name in ("reported", "estimated", "placed")
        }
        for system, rows in (("LIRR", lirr), ("MNR", mnr))
    }
    gps_ages = sorted(world.NOW - t["observed_at"] for t in lirr if t["provenance"] == "reported")
    row_keys = sorted(body["data"][0])

    print("  world      : backend/tests/test_f01_positions.py, both captures restamped by one")
    print(f"               delta onto NOW={world.NOW:.0f} (FROZEN_S of tests/e2e/fixtures/api.js)")
    print("  poll       : pollers._refresh_railroads over the real fetch_railroad_trains")
    print("  served     : GET /api/railroads and GET /api/status over httpx.ASGITransport")
    print(f"  handoff    : {world.GOLDEN.relative_to(REPO)}")
    print()
    print(f"  records served             : {len(body['data'])}  "
          f"(LIRR {len(lirr)}, MNR {len(mnr)})")
    for system, counts in by_provenance.items():
        print(f"    {system:<4} reported {counts['reported']:>3}, estimated "
              f"{counts['estimated']:>3}, placed {counts['placed']:>3}")
    print(f"  systems.LIRR.positions     : {body['systems']['LIRR']['positions']}")
    print(f"  systems.MNR.positions      : {body['systems']['MNR']['positions']}")
    print(f"  /api/status railroad_positions: {status['railroad_positions']}")
    print(f"  envelope keys              : {sorted(body)}")
    print(f"  per-vehicle keys ({len(row_keys)})      : {row_keys}")
    print()
    print("  the LIRR GPS rows a rider receives, by the age each one carries:")
    print(f"    within  90 s : {sum(1 for a in gps_ages if a <= cache.OBS_FRESH_S):>4}"
          "   drawn as they always were")
    qualified = sum(1 for a in gps_ages if cache.OBS_FRESH_S < a <= cache.OBS_MAX_S)
    print(f"    90 to 600 s  : {qualified:>4}"
          "   dimmed, reading \"live GPS, as of Nm ago\"")
    print(f"    over   600 s : {sum(1 for a in gps_ages if a > cache.OBS_MAX_S):>4}"
          "   there are none: they are the 24 counted below")
    print(f"    oldest       : {gps_ages[-1]:>4.0f} s")
    print()
    print("  the pre-gate record count at THIS world's clock was never measured: the live path")
    print("  dedupes Metro-North's 49 entities to 33 and places 3 of them, so it is not derivable")
    print("  from the goldens. f02_canceled_railroad_gps.py measures 157 to 137 at its own clock,")
    print("  which is 51915 s before the header and not this one.")

    check("the served body IS the committed handoff fixture", body == golden, True)
    check("records served", len(body["data"]), EXPECT["api_records"])
    check("LIRR rows drawn at their own position", by_provenance["LIRR"]["reported"],
          EXPECT["api_lirr_reported"])
    check("LIRR rows drawn as estimates from a prediction", by_provenance["LIRR"]["estimated"],
          EXPECT["api_lirr_estimated"])
    check("LIRR rows placed from a prediction alone (no vehicle, outside the gate)",
          by_provenance["LIRR"]["placed"], EXPECT["api_lirr_placed"])
    check("Metro-North rows drawn at their own position, exempt by policy",
          by_provenance["MNR"]["reported"], EXPECT["api_mnr_reported"])
    check("Metro-North placements", by_provenance["MNR"]["placed"], EXPECT["api_mnr_placed"])
    check("every Metro-North row carries no observation clock, as before the gate",
          all(t["observed_at"] is None for t in mnr), True)
    check("every LIRR row carries one, which is what a qualifier needs",
          all(t["observed_at"] is not None for t in lirr), True)
    check("the LIRR block's counts, on the rider's envelope", body["systems"]["LIRR"]["positions"],
          EXPECT["lirr_steps"])
    check("the Metro-North block's counts", body["systems"]["MNR"]["positions"],
          EXPECT["mnr_steps"])
    check("the same counts on the operator's snapshot, from one projection",
          status["railroad_positions"], {"LIRR": EXPECT["lirr_steps"], "MNR": EXPECT["mnr_steps"]})
    check("per-vehicle keys (this script said 14 before 6.1 added the pair)",
          len(row_keys), EXPECT["api_row_keys"])
    check("the pair the map reads is on every served row",
          {"observed_at", "provenance"} <= set(row_keys), True)
    check("the envelope keys are unchanged",
          sorted(body), ["data", "feed_timestamp", "fetched_at", "served_at", "systems"])
    check("no failed system: the trap is that nothing operational looks wrong",
          status["railroad_feeds"], {"total": 2, "ok": 2, "failed": []})
    check("the oldest served LIRR fix (was 53676 s)", int(gps_ages[-1]),
          EXPECT["oldest_served_fix_s"])
    check("served LIRR GPS rows past OBS_MAX_S (was 25)",
          sum(1 for a in gps_ages if a > cache.OBS_MAX_S), EXPECT["served_over_600_s"])


# ---------------------------------------------------------------------------
# D. section 3.4's table
# ---------------------------------------------------------------------------

# Section 3.4's cost table, one row per ladder step: the design's count, the field of
# models.PositionSteps that carries it, and what the rider sees.
DESIGN_3_4 = (
    (1, "reported, unqualified", "reported", 27, "a live marker, unchanged"),
    (2, "estimated", "estimated", 6, 'a marker labeled "estimated from a prediction"'),
    (3, "reported, qualified", "qualified", 11, 'a dimmed marker reading "as of {age} ago"'),
    (4, "placed", "placed", 0, "nothing on this capture: design 3.4 says why"),
    (5, "nothing", "suppressed", 24, "the marker is gone, and the status line says so"),
)


def section_d(body: dict) -> None:
    rule("D. Section 3.4's table, measured")

    served = body["systems"]["LIRR"]["positions"]
    print("  design 3.4, at OBS_FRESH_S "
          f"{cache.OBS_FRESH_S:.0f} and OBS_MAX_S {cache.OBS_MAX_S:.0f}, against what is served:")
    print()
    print(f"    {'step':<24}{'design':>7}{'served':>8}   what the rider sees")
    for step, name, field, designed, rider in DESIGN_3_4:
        print(f"    {f'{step}. {name}':<24}{designed:>7}{served[field]:>8}   {rider}")
    print()
    print(f"    {'sum':<24}{sum(row[3] for row in DESIGN_3_4):>7}{sum(served.values()):>8}"
          f"   the {EXPECT['base_rule_accepts']} the base rule accepts")

    for step, name, field, designed, _rider in DESIGN_3_4:
        check(f"step {step}, {name}", served[field], designed)
    check("the five counts sum to the vehicles the base rule accepts",
          sum(served.values()), EXPECT["base_rule_accepts"])


# ---------------------------------------------------------------------------
# E. the remedy note
# ---------------------------------------------------------------------------


def section_e(ages: dict[str, float], body: dict, step4_body: dict, step4_status: dict) -> None:
    rule("E. The remedy note, now answered")

    stale_ids = {tid for tid, age in ages.items() if age > 90}
    lirr = [t for t in body["data"] if t["system"] == "LIRR"]
    served_gps = {t["trip_id"] for t in lirr if t["provenance"] == "reported"}
    estimated = {t["trip_id"]: t for t in lirr if t["provenance"] == "estimated"}
    canceled = railroad_mod._canceled_trip_ids(LIRR_FEED)
    accepted = set(ages) - canceled
    moved_off = accepted - served_gps
    absent = moved_off - {t["trip_id"] for t in lirr}

    print("  the note: filtering the GPS output alone restores no estimated position, because")
    print("  the placement pass independently calls every positioned entity GPS-equipped.")
    print("  THE GATE IS NOT A FILTER ON THE GPS OUTPUT. It is one rule both passes read")
    print("  (_accepted_as_gps, asked per entity), so a vehicle the GPS pass stops serving is")
    print("  the same vehicle the placement pass is now free to place:")
    print()
    print(f"    positioned vehicles the base rule accepts : {len(accepted)}")
    print(f"    still served at their own position        : {len(served_gps)}")
    print(f"    moved off that surface by the gate        : {len(moved_off)}")
    print(f"      ... of those, drawn as estimates        : {len(estimated)}")
    print(f"      ... absent from every surface           : {len(absent)}")
    print()
    print("  the six estimates, each riding a prediction the ladder judged fresh:")
    for trip_id, row in sorted(estimated.items()):
        print(f"      {trip_id:<24} prediction {world.NOW - row['observed_at']:>4.0f} s old,"
              f" own fix {ages[trip_id]:>5.0f} s, at stop {row['stop_id']} {row['stop_name']}")
    print()
    absent_ages = sorted(ages[tid] for tid in absent)
    print(f"  the {len(absent)} absent from every surface are counted `suppressed`, and the"
          " status line")
    print("  is the only place a rider meets them. Their own fixes, against the header:")
    print(f"      freshest {absent_ages[0]:.0f} s, median {statistics.median(absent_ages):.0f} s,"
          f" oldest {absent_ages[-1]:.0f} s ({hms(absent_ages[-1])})")

    check("of the 42 stale observations, how many the placement pass now estimates (audit: 0)",
          len(estimated), EXPECT["estimates_at_obs_fresh"])
    check("they are the design's six", set(estimated), world.ESTIMATED)
    check("every one rides a prediction within OBS_FRESH_S",
          all(world.NOW - row["observed_at"] <= cache.OBS_FRESH_S for row in estimated.values()),
          True)
    check("and every one is a placement row, drawn at a stop (memo D5)",
          all(row["stop_id"] and row["next_time"] and row["prev_time"]
              for row in estimated.values()), True)
    check("the vehicles absent from every surface", len(absent), EXPECT["withheld_and_counted"])
    check("every one of them past OBS_MAX_S, which is what the status line claims of them",
          all(ages[tid] > cache.OBS_MAX_S for tid in absent), True)
    check("they are exactly the LIRR block's `suppressed` count",
          len(absent), body["systems"]["LIRR"]["positions"]["suppressed"])
    served_ages = {t["trip_id"]: world.NOW - t["observed_at"] for t in lirr}
    check("no stale observation reaches a rider as an unqualified live marker (was 41)",
          sum(1 for tid in stale_ids & served_gps if served_ages[tid] <= cache.OBS_FRESH_S),
          EXPECT["served_over_90_s_unqualified"])

    # THE AUDIT'S OWN NO-LIMIT MEASUREMENT, KEPT, because the ledger quotes it and the two
    # numbers are answers to different questions. Removing the 42 stale positioned entities
    # entirely leaves 15 of those trips with a placement of ANY age behind them; asking the
    # same feed for a placement the ladder would accept at OBS_FRESH_S gives 6. "15 of the
    # 42" is the no-limit figure and was never the built one.
    trimmed = pb.FeedMessage()
    trimmed.header.CopyFrom(LIRR_FEED.header)
    dropped = 0
    for entity in LIRR_FEED.entity:
        if (
            entity.HasField("vehicle")
            and entity.vehicle.HasField("position")
            and (entity.vehicle.trip.trip_id or entity.id) in stale_ids
        ):
            dropped += 1
            continue
        trimmed.entity.add().CopyFrom(entity)
    recovered = feeds._decode_railroad_placements(
        trimmed.SerializeToString(), "LIRR", LIRR_STOPS, HEADER_TS
    )
    recovered_ids = {t["trip_id"] for t in recovered}
    print()
    print(f"  injected: the {dropped} stale positioned entities removed from an in-memory copy,")
    print("  which is the audit's own experiment and the source of the ledger's 15:")
    print(f"      of the 42, placed at a station with no age limit at all : "
          f"{len(recovered_ids & stale_ids)}")
    print(f"      of the 42, estimated by the ladder at OBS_FRESH_S       : {len(estimated)}")
    check("the no-limit figure the ledger quotes",
          len(recovered_ids & stale_ids), EXPECT["placements_with_no_age_limit"])

    # N2'S CASE, UNCHANGED BY THE GATE (memo D1), on a step-1 vehicle rather than the
    # audit's oldest: see DISPLACED above for why the subject moved.
    baseline_gps, _ = feeds._decode_railroad_vehicles(LIRR_RAW, "LIRR", HEADER_TS, LIRR_STOPS)
    baseline_placed = feeds._decode_railroad_placements(LIRR_RAW, "LIRR", LIRR_STOPS, HEADER_TS)
    moved_raw = displaced(LIRR_RAW, DISPLACED)
    moved_gps, _ = feeds._decode_railroad_vehicles(moved_raw, "LIRR", HEADER_TS, LIRR_STOPS)
    moved_placed = feeds._decode_railroad_placements(moved_raw, "LIRR", LIRR_STOPS, HEADER_TS)
    moved_gps_ids = {t["trip_id"] for t in moved_gps}
    moved_placed_ids = {t["trip_id"] for t in moved_placed}
    moved_row = [t for t in moved_placed if t["trip_id"] == DISPLACED]
    moved_steps = railroad_mod._position_steps(moved_raw, "LIRR", LIRR_STOPS, HEADER_TS)
    print()
    print(f"  injected: {DISPLACED} (train 7344, its fix {ages[DISPLACED]:.0f} s old,"
          " ladder step 1)")
    print("  moved to lat 0 / lon 0. The audit displaced the oldest fix; the gate withholds that")
    print("  vehicle before the box is ever asked, so displacing it would measure the gate:")
    print(f"      GPS rows        {len(baseline_gps)} -> {len(moved_gps)},"
          f" that trip present: {DISPLACED in moved_gps_ids}")
    print(f"      placement rows  {len(baseline_placed)} -> {len(moved_placed)},"
          f" that trip placed: {DISPLACED in moved_placed_ids}"
          f" as `{moved_row[0]['provenance'] if moved_row else 'nothing'}`")
    print(f"      counts          {moved_steps}")

    check("the box-rejected vehicle leaves the GPS surface (was 69 -> 68)",
          len(moved_gps), EXPECT["displaced_gps_rows"])
    check("and arrives on the placement surface (was 56 -> 57)",
          len(moved_placed), EXPECT["displaced_placement_rows"])
    check("placed as it always was, labeled `placed` rather than estimated (memo D1)",
          moved_row[0]["provenance"] if moved_row else None, "placed")
    check("so it reaches exactly one surface (the audit: it appeared on none at all)",
          (DISPLACED in moved_gps_ids) + (DISPLACED in moved_placed_ids), 1)
    check("and nothing is drawn on both", moved_gps_ids & moved_placed_ids, set())
    check("it is counted where it is drawn: one fewer reported, one more placed",
          moved_steps, EXPECT["displaced_steps"])

    # STEP 4, WHICH THIS CAPTURE CANNOT REACH AND WHICH NO ACCEPTANCE MAY REQUIRE OF IT
    # (design 3.4). The acceptance world builds it: one of the 24 given a prediction 300 s
    # old, past OBS_FRESH_S and within OBS_MAX_S, its own fix still past OBS_MAX_S.
    rows = [t for t in step4_body["data"] if t["trip_id"] == world.STEP_4_TRIP]
    print()
    print(f"  built world (test_f01_positions._with_a_usable_old_prediction): {world.STEP_4_TRIP}")
    print("  given a prediction 300 s old, its own fix still past OBS_MAX_S:")
    print(f"      served as        : {rows[0]['provenance'] if rows else 'nothing'}, at stop "
          f"{rows[0]['stop_id'] if rows else '-'}, dated "
          f"{world.NOW - rows[0]['observed_at'] if rows else 0:.0f} s old")
    print(f"      counts           : {step4_body['systems']['LIRR']['positions']}")
    check("step 4 fires in the built world, and nowhere on the capture",
          [r["provenance"] for r in rows], ["placed"])
    check("dated by that prediction, not by its own fix",
          world.NOW - rows[0]["observed_at"] if rows else None, 300.0)
    check("and the counts move by exactly that one vehicle",
          step4_body["systems"]["LIRR"]["positions"], EXPECT["step_4_steps"])
    check("on the operator's snapshot too",
          step4_status["railroad_positions"]["LIRR"], EXPECT["step_4_steps"])


# ---------------------------------------------------------------------------
# F. the placement surface, which the gate does not reach
# ---------------------------------------------------------------------------


def section_f(body: dict) -> None:
    rule("F. What the placement surface rides, which the gate does not reach")

    lirr = [t for t in body["data"] if t["system"] == "LIRR"]
    placed = [t for t in lirr if t["provenance"] == "placed"]
    estimated = [t for t in lirr if t["provenance"] == "estimated"]
    ages = sorted(world.NOW - t["observed_at"] for t in placed)
    estimate_ages = sorted(world.NOW - t["observed_at"] for t in estimated)

    print("  A trip with no vehicle entity has no fix of its own to age, so the ladder has")
    print("  nothing to judge it by and 6.3 leaves it placed exactly as it was placed before,")
    print("  whatever its prediction's age (memo D1). These are the predictions those markers")
    print("  ride, read off the SERVED body so this record and the capture cannot drift apart:")
    print()
    print(f"    placement rows (no vehicle)  : {len(placed)}")
    print(f"      riding a prediction > 90 s : {sum(1 for a in ages if a > cache.OBS_FRESH_S):>4}"
          f"   ({100 * sum(1 for a in ages if a > cache.OBS_FRESH_S) / len(ages):.0f}%)")
    print(f"      riding one over 600 s      : {sum(1 for a in ages if a > cache.OBS_MAX_S):>4}"
          f"   ({100 * sum(1 for a in ages if a > cache.OBS_MAX_S) / len(ages):.0f}%)")
    print(f"      freshest                   : {ages[0]:>8.0f} s")
    print(f"      median                     : {statistics.median(ages):>8.0f} s"
          f"  = {hms(statistics.median(ages))}")
    print(f"      oldest                     : {ages[-1]:>8.0f} s  = {hms(ages[-1])}")
    print()
    print(f"    estimated rows (a vehicle behind them): {len(estimated)}, riding predictions"
          f" {estimate_ages[0]:.0f} to {estimate_ages[-1]:.0f} s old")
    print()
    print("  That is the same threshold working at both ends of one surface, and the gate does")
    print("  not reach the placements: gating them on their prediction alone would take LIRR's")
    print(f"  placement surface from {len(placed) + len(estimated)} rows to"
          f" {len(estimated) + sum(1 for a in ages if a <= cache.OBS_MAX_S)} on this capture.")
    print("  What 6.3 does instead is say the age out loud, so those rows dim and hold still.")
    print("  Open question 1 of docs/reviews/audit-2026-09-05.md is where that stands.")

    check("placement rows with no vehicle behind them", len(placed), EXPECT["placed_rows"])
    check("riding a prediction past OBS_FRESH_S",
          sum(1 for a in ages if a > cache.OBS_FRESH_S), EXPECT["placed_over_90_s"])
    check("riding one past OBS_MAX_S",
          sum(1 for a in ages if a > cache.OBS_MAX_S), EXPECT["placed_over_600_s"])
    check("the median placement's prediction", int(statistics.median(ages)),
          EXPECT["placed_median_s"])
    check("the freshest", int(ages[0]), EXPECT["placed_freshest_s"])
    check("the oldest", int(ages[-1]), EXPECT["placed_oldest_s"])
    check("the whole placement surface, estimates included", len(placed) + len(estimated),
          EXPECT["placement_rows"])
    check("the six estimates' predictions, the other end of the same threshold",
          [int(a) for a in estimate_ages], EXPECT["estimate_ages_s"])
    check("and the placed golden carries the same 56 rows",
          sum(1 for t in PLACED_GOLDEN["trains"] if t["provenance"] == "placed"),
          EXPECT["placed_rows"])


def main_script() -> int:
    print("F01: old individual LIRR GPS observations can look live")
    print(f"repository : {REPO}")
    print(f"capture    : {(FIXTURES / 'railroad_lirr.pb').relative_to(REPO)}")
    print(f"world      : {(BACKEND / 'tests' / 'test_f01_positions.py').relative_to(REPO)}")
    print("clocks     : the capture's own FeedHeader.timestamp, and the world's frozen NOW")

    ages = section_a()
    section_b(ages)
    with frozen_clock():
        body, status, step4_body, step4_status = asyncio.run(poll_the_world())
    section_c(body, status)
    section_d(body)
    section_e(ages, body, step4_body, step4_status)
    section_f(body)

    rule("MEASURED SUMMARY")
    served = body["systems"]["LIRR"]["positions"]
    gps_ages = sorted(
        world.NOW - t["observed_at"]
        for t in body["data"]
        if t["system"] == "LIRR" and t["provenance"] == "reported"
    )
    print(f"  positioned LIRR vehicles on the wire  : {EXPECT['positioned_on_the_wire']}"
          "  (42 over 90 s, 32 over 5 min, 25 over 10 min)")
    print(f"  the base rule accepts                 : {EXPECT['base_rule_accepts']}")
    print(f"  served as live GPS                    : {len(gps_ages)}  (was"
          f" {EXPECT['base_rule_accepts']})")
    print(f"  oldest served fix                     : {gps_ages[-1]:.0f} s  (was"
          f" {EXPECT['wire_oldest_s']} s, {hms(EXPECT['wire_oldest_s'])})")
    print(f"  ladder counts, LIRR                   : {served['reported']}/{served['estimated']}"
          f"/{served['qualified']}/{served['placed']}/{served['suppressed']}")
    print(f"  ladder counts, Metro-North            : "
          f"{'/'.join(str(v) for v in body['systems']['MNR']['positions'].values())}")
    print(f"  records at /api/railroads             : {len(body['data'])}")
    print(f"  placements the gate does not reach    : {EXPECT['placed_rows']},"
          f" {EXPECT['placed_over_600_s']} of them past OBS_MAX_S")
    print()
    if FAILURES:
        print(f"  {len(FAILURES)} check(s) no longer match the recorded disposition:")
        for line in FAILURES:
            print(f"    - {line}")
        print()
        print("DISPOSITION: CHANGED, this script no longer matches the recorded finding.")
        return 1
    print("  every check matches the recorded disposition.")
    print()
    print(
        f"DISPOSITION: FIXED ({BRANCH})  "
        "BEFORE: the capture's 69 positioned LIRR vehicles included 42 over 90 s, 32 over 5 min "
        "and 25 over 10 min with the oldest at 53676 s (14h 54m 36s), and the full aggregation "
        "served 68 of them as live GPS with no per-vehicle age on any surface. AFTER: the same "
        "bytes serve 38, the oldest served fix is 593 s, 6 aged trains are drawn as estimates "
        "from predictions 4 to 5 s old, 11 are drawn qualified and dimmed, and the 24 with "
        "nothing honest left to draw are absent from every surface and counted 24 on the LIRR "
        "block, which is exactly section 3.4's 27/6/11/0/24."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_script())
