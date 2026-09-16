"""F01, the old GPS observations (contract 6.3): the acceptance world, envelope half.

THE AUDITOR'S ACCEPTANCE, VERBATIM (docs/design/freshness-contract.md 1.2 and 6.3):

    "a fresh header containing an old vehicle observation never produces an unqualified
    live-GPS marker; a usable prediction can produce a clearly labeled estimated marker
    instead."

AND N2'S, WHICH THE GATE MUST NOT REOPEN (docs/reviews/audit-2026-09-05.md):

    "a positioned vehicle the GPS pass rejects is considered by the placement pass, and
    appears on some surface or on none for a stated reason."

THIS MODULE IS THE ENVELOPE HALF, in test_f03_boards.py's shape. It serves the committed
LIRR and Metro-North captures through the real fetch_railroad_trains, _refresh_railroads
and ASGI /api/railroads and /api/status, and asserts that the served body carries every
fact a rider's surface needs to say which step of section 3.4's ladder a train is at: its
provenance, its observation clock, and each system's counts, `suppressed` among them. The
browser half renders the same body. This module writes it to
tests/e2e/fixtures/f01_railroads.json and fails whenever the live body stops equalling it,
so a change to what the backend serves goes red here before the browser suite can drift
from it, which makes that file the handoff between the two halves.

THE WORLD. Production code throughout, except the socket:

  * Upstream bytes are the committed captures backend/tests/fixtures/railroad_lirr.pb and
    railroad_mnr.pb, re-stamped by ONE constant delta that puts the LIRR header exactly
    on the poll clock. Every time either feed carries moves by it: the header, each
    vehicle, each trip update, every stop time, and Metro-North's trip starts, which its
    not-yet-started filter reads as a local date and time and which a shift of the epoch
    fields alone would leave eleven days behind. So every age, and every stop's distance
    from the clock, is the capture's own: the committed world moved in time and not in
    shape, which test_the_world_is_the_committed_captures_moved_in_time_not_in_shape
    proves row for row against the goldens. Metro-North's header sits 223 s behind
    LIRR's in the captures and still does.
  * THE POLL CLOCK IS THE LIRR HEADER, because the design measured the ladder there: 27
    reported, 6 estimated, 11 qualified, 0 placed and 24 not drawn. At the header the
    placement pass's `now` and the ladder's clock coincide, so these are the counts to the
    vehicle (feeds/railroad.py _position_ladder says why a live poll's may differ).
  * main.fetch_railroad_trains does the fetching, called with a stub client that returns
    real httpx.Response objects per URL, so raise_for_status behaves as in production and
    a 503 fails its system. pollers._refresh_railroads refreshes, and main.app is driven
    over httpx.ASGITransport, so the body is what the response models serve.

THE CLOCK IS THE BROWSER SUITE'S. NOW is FROZEN_S of tests/e2e/fixtures/api.js, as in
test_f03_boards.py, and time.time is patched to it, so served_at and fetched_at are NOW to
the bit and the served JSON is already in the browser's fixture time: with fetched_at ==
FROZEN_S the frontend's clock-skew offset is 0, and a marker's observation age reads
exactly FROZEN_S - observed_at. test_the_f01_world_is_served_in_the_browser_suites_frozen_time
reads that constant out of api.js instead of trusting this copy of it.

To regenerate the handoff after an INTENTIONAL change to what /api/railroads serves, from
backend/:

    F01_WORLD_REGENERATE=1 pytest -q tests/test_f01_positions.py

Then review the diff of tests/e2e/fixtures/f01_railroads.json. The browser half renders
that file, so its diff IS the change a rider will see. It is written only at the END of a
run in which every acceptance assertion held, so a regeneration cannot commit a body that
fails the acceptance.
"""

import json
import os
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from google.transit import gtfs_realtime_pb2 as pb

import cache
import feeds
import main as app_module
import pollers
from feeds.railroad import _railroad_trip_start_ts
from feeds.shared import _DROP_TRIP_RELATIONSHIPS, NYC_TZ

_REPO = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).parent / "fixtures"
_API_JS = _REPO / "tests" / "e2e" / "fixtures" / "api.js"
GOLDEN = _REPO / "tests" / "e2e" / "fixtures" / "f01_railroads.json"
REGENERATE_VAR = "F01_WORLD_REGENERATE"

NOW = 1782993600.0  # FROZEN_S of tests/e2e/fixtures/api.js: 2026-07-02T12:00:00Z
REPOLL_S = 15.0


def _raw(system: str) -> bytes:
    return (_FIXTURES / f"railroad_{system.lower()}.pb").read_bytes()


def _stops(system: str) -> dict:
    return json.loads((_FIXTURES / f"railroad_{system.lower()}_stops.json").read_text())


# ONE DELTA FOR BOTH CAPTURES, the one that puts the LIRR header on NOW.
DELTA = int(NOW) - int(pb.FeedMessage.FromString(_raw("LIRR")).header.timestamp)

# The design's six (3.4, step 2) and eleven (step 3), by trip id, as
# tests/test_position_ladder.py and tests/test_feeds_railroad.py name them.
ESTIMATED = {
    "GO201_26_6187_1",
    "GO201_26_6468_2932_METS",
    "GO201_26_6188",
    "GO201_26_8768",
    "6029_2026-06-20",
    "GO201_26_6665",
}
QUALIFIED = {
    "GO201_26_7987",
    "GO201_26_6186_1",
    "GO201_26_7990_2",
    "6017_2026-06-20",
    "6027_2026-06-20",
    "GO201_26_7766",
    "GO201_26_8960",
    "GO201_26_6040",
    "GO201_26_8971",
    "GO201_26_6570",
    "GO201_26_6369_2932_METS",
}
LIRR_STEPS = {"reported": 27, "estimated": 6, "qualified": 11, "placed": 0, "suppressed": 24}
MNR_STEPS = {"reported": 33, "estimated": 0, "qualified": 0, "placed": 0, "suppressed": 0}
# One of the 24: its fix is over 600 s old and its one placeable prediction is 1114 s old,
# too old to use. The built world gives that prediction a clock 300 s old.
STEP_4_TRIP = "GO201_26_7768"
# The audit's own F01 witness, train 521: its fix is 53676 s (14h 54m 36s) old, the oldest
# on the capture, and it is one of the 24. The built world clears the clock on the one
# prediction behind it, which is the other door a marker for it could come through.
F01_WITNESS = "6006_2026-06-20"


def _shift_trip_start(trip) -> None:
    """Move a trip's start_date and start_time (a local date and time, not an epoch) by
    DELTA, when it carries both. Metro-North does; LIRR carries no start_time."""
    start = _railroad_trip_start_ts(trip)
    if start is None:
        return
    moved = datetime.fromtimestamp(start + DELTA, NYC_TZ)
    trip.start_date = moved.strftime("%Y%m%d")
    trip.start_time = moved.strftime("%H:%M:%S")
    assert _railroad_trip_start_ts(trip) == start + DELTA, "a DST edge inside the shift"


def _restamp(raw: bytes) -> bytes:
    """A committed capture moved by DELTA: every timestamp it carries, and nothing else."""
    feed = pb.FeedMessage.FromString(raw)
    feed.header.timestamp += DELTA
    for entity in feed.entity:
        if entity.HasField("trip_update"):
            tu = entity.trip_update
            if tu.timestamp:
                tu.timestamp += DELTA
            _shift_trip_start(tu.trip)
            for stu in tu.stop_time_update:
                for field in ("arrival", "departure"):
                    if stu.HasField(field):
                        event = getattr(stu, field)
                        if event.HasField("time") and event.time:
                            event.time += DELTA
        if entity.HasField("vehicle"):
            vehicle = entity.vehicle
            if vehicle.timestamp:
                vehicle.timestamp += DELTA
            _shift_trip_start(vehicle.trip)
    return feed.SerializeToString()


def _with_a_usable_old_prediction(lirr_bytes: bytes) -> bytes:
    """The built world for step 4, which the capture cannot reach and which no acceptance
    test may require of it (design 3.4): STEP_4_TRIP's trip update dated 300 s behind the
    clock, older than OBS_FRESH_S and within OBS_MAX_S. Its fix stays over 600 s old."""
    feed = pb.FeedMessage.FromString(lirr_bytes)
    (entity,) = [
        e
        for e in feed.entity
        if e.HasField("trip_update") and e.trip_update.trip.trip_id == STEP_4_TRIP
    ]
    entity.trip_update.timestamp = int(NOW) - 300
    return feed.SerializeToString()


def _with_an_undated_prediction(lirr_bytes: bytes) -> bytes:
    """The built world for the ladder's undated-PREDICTION rule: F01_WITNESS's trip update
    stripped of its own clock, protobuf zero being absence (_railroad_observed_at). The
    capture's only undated trip updates are its canceled trips, which are not placeable at
    all, so a vehicle riding a prediction with no time on it has to be built."""
    feed = pb.FeedMessage.FromString(lirr_bytes)
    (entity,) = [
        e
        for e in feed.entity
        if e.HasField("trip_update") and e.trip_update.trip.trip_id == F01_WITNESS
    ]
    entity.trip_update.timestamp = 0
    return feed.SerializeToString()


class _Upstream:
    """Stands in for httpx.AsyncClient inside the REAL fetch_railroad_trains. A body is
    served as a 200; an int is served as that status with no body, which raise_for_status
    turns into a failed system exactly as it does in production."""

    def __init__(self, lirr: bytes | int, mnr: bytes | int) -> None:
        self.bodies = {
            feeds.RAILROAD_FEED_URLS["LIRR"]: lirr,
            feeds.RAILROAD_FEED_URLS["MNR"]: mnr,
        }

    async def get(self, url: str) -> httpx.Response:
        body = self.bodies[url]
        request = httpx.Request("GET", url)
        if isinstance(body, int):
            return httpx.Response(body, request=request)
        return httpx.Response(200, content=body, request=request)


@pytest.fixture
def f01_world(monkeypatch):
    """The app.state a lifespan would have built for the railroad, and the patched clock.

    ISOLATED BY monkeypatch, as test_f03_boards.py isolates its world: every attribute the
    world primes or the poll writes is set through monkeypatch, so teardown restores each
    to what it was, or deletes it, and no later test inherits these trains.

    Returns the clock as a one-element list, so a test can advance it between polls.
    """
    primed = {
        "feed_cache": {"railroads": app_module._fresh_entry()},
        "railroad_feed_health": None,
        "railroad_static_status": "ready",
        "railroad_stops": {system: _stops(system) for system in feeds.RAILROAD_FEED_URLS},
        "railroad_arrivals": {},
        "railroad_positions": {},
    }
    for name, value in primed.items():
        monkeypatch.setattr(app_module.app.state, name, value, raising=False)
    clock = [NOW]
    monkeypatch.setattr(app_module.time, "time", lambda: clock[0])
    return clock


async def _poll_and_serve(upstream: _Upstream) -> tuple[dict, dict]:
    """One real railroad refresh, then /api/railroads and /api/status as SERVED JSON."""
    await pollers._refresh_railroads(app_module.app, upstream)
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://f01") as http:
        railroads = await http.get("/api/railroads")
        status = await http.get("/api/status")
    assert railroads.status_code == 200, railroads.text
    assert status.status_code == 200, status.text
    return railroads.json(), status.json()


def _own_fix_ages(raw: bytes) -> dict[str, float]:
    """Each positioned vehicle's own fix age against the header, by trip id, off the wire."""
    feed = pb.FeedMessage.FromString(raw)
    header = float(feed.header.timestamp)
    return {
        e.vehicle.trip.trip_id: header - e.vehicle.timestamp
        for e in feed.entity
        if e.HasField("vehicle") and e.vehicle.HasField("position")
    }


def _canceled(raw: bytes) -> set[str]:
    feed = pb.FeedMessage.FromString(raw)
    return {
        e.trip_update.trip.trip_id
        for e in feed.entity
        if e.HasField("trip_update")
        and e.trip_update.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS
    }


def _first_per_trip(rows: list[dict]) -> list[dict]:
    """The live path's first-wins (system, trip_id) dedupe, applied to one system's rows."""
    seen: set[str] = set()
    kept = []
    for row in rows:
        if row["trip_id"] not in seen:
            seen.add(row["trip_id"])
            kept.append(row)
    return kept


@pytest.mark.anyio
async def test_acceptance_no_old_fix_is_served_as_live_and_every_one_is_accounted_for(f01_world):
    """THE F01 ACCEPTANCE, ENVELOPE HALF, and N2's beside it, on ONE served /api/railroads
    body and the /api/status of the same poll.

    (a) No old fix is served as a live one: every LIRR row carries its observation clock,
    27 GPS rows are within OBS_FRESH_S, the other 11 are the design's eleven, each past
    OBS_FRESH_S (the age the rider's client qualifies) and none past OBS_MAX_S. (b) A usable
    prediction produces a clearly labeled estimate instead: the design's six, served as
    `estimated` placement rows. (c) The counts ride the systems blocks and /api/status
    alike: LIRR 27/6/11/0/24, `suppressed` 24, Metro-North 33 reported. (d) N2: of the 68
    vehicles the base rule accepts, the 30 the GPS pass no longer serves are the six
    estimates and 24 absent from every surface, each past OBS_MAX_S, and counted: the
    stated reason. (e) Metro-North is unchanged: its GPS rows are its golden's, deduped as
    the live path dedupes them. "Unqualified" and "clearly labeled" are the browser half's
    words, rendered from tests/e2e/fixtures/f01_railroads.json, which the last assertion
    pins to this body.
    """
    lirr_bytes = _restamp(_raw("LIRR"))
    body, status = await _poll_and_serve(_Upstream(_restamp(_raw("LIRR")), _restamp(_raw("MNR"))))

    # THE TRAP THE ACCEPTANCE IS ABOUT, asserted rather than assumed: both feeds decoded,
    # the LIRR header IS the poll clock, and by every operational measure the railroad is
    # healthy. Nothing older than the rows themselves could tell an old fix apart.
    systems = body["systems"]
    assert sorted(systems) == ["LIRR", "MNR"]
    assert all(block["ok"] and block["retained_since"] is None for block in systems.values())
    assert systems["LIRR"]["feed_timestamp"] == NOW
    assert systems["MNR"]["feed_timestamp"] is None
    assert body["fetched_at"] == body["served_at"] == NOW
    assert status["railroad_feeds"] == {"total": 2, "ok": 2, "failed": []}

    lirr = [t for t in body["data"] if t["system"] == "LIRR"]
    gps = [t for t in lirr if t["provenance"] == "reported"]
    estimated = {t["trip_id"]: t for t in lirr if t["provenance"] == "estimated"}
    placed = [t for t in lirr if t["provenance"] == "placed"]
    assert len(lirr) == len(gps) + len(estimated) + len(placed) == 38 + 6 + 56

    # (a) NO OLD FIX SERVED AS A LIVE ONE. Every row carries the clock a qualifier needs.
    assert all(t["observed_at"] is not None for t in lirr)
    ages = {t["trip_id"]: NOW - t["observed_at"] for t in gps}
    assert sum(1 for age in ages.values() if age <= cache.OBS_FRESH_S) == 27
    assert {trip for trip, age in ages.items() if age > cache.OBS_FRESH_S} == QUALIFIED
    # The oldest fix served. Before the gate it was 53676 s (14h 54m 36s, train 521).
    assert max(ages.values()) == 593.0 <= cache.OBS_MAX_S

    # (b) A USABLE PREDICTION, A CLEARLY LABELED ESTIMATE INSTEAD. Each of the six is a
    # placement row (memo D5): a station's coordinates, a prediction within OBS_FRESH_S as
    # its clock, and the anchors the client glides between.
    assert set(estimated) == ESTIMATED
    for trip, row in estimated.items():
        assert NOW - row["observed_at"] <= cache.OBS_FRESH_S, trip
        assert row["stop_id"] and row["next_time"] and row["prev_time"], trip
    # The 56 trips with no vehicle, placed as they always were whatever their prediction's
    # age (memo D1), and not one vehicle's trip among them: step 4 never fires here.
    wire = _own_fix_ages(lirr_bytes)
    assert len(placed) == 56 and not {t["trip_id"] for t in placed} & set(wire)

    # (c) THE COUNTS, on the rider's envelope and on the operator's snapshot alike.
    assert systems["LIRR"]["positions"] == LIRR_STEPS
    assert systems["MNR"]["positions"] == MNR_STEPS
    assert status["railroad_positions"] == {"LIRR": LIRR_STEPS, "MNR": MNR_STEPS}

    # (d) N2: EVERY VEHICLE THE GPS PASS NO LONGER SERVES IS ON ANOTHER SURFACE, OR ON NONE
    # FOR A STATED REASON. The 68 are the 69 on the wire less F02's canceled one.
    vehicles = set(wire) - _canceled(lirr_bytes)
    assert len(vehicles) == 68
    moved_off = vehicles - set(ages)
    assert len(moved_off) == 30
    assert moved_off & set(estimated) == ESTIMATED
    absent = moved_off - {t["trip_id"] for t in lirr}
    assert len(absent) == LIRR_STEPS["suppressed"] == 24
    assert all(wire[trip] > cache.OBS_MAX_S for trip in absent)

    # (e) METRO-NORTH UNCHANGED: exempt by policy, its rows are its golden's, one per trip.
    mnr = [t for t in body["data"] if t["system"] == "MNR"]
    golden = json.loads((_FIXTURES / "railroad_mnr_expected.json").read_text())["trains"]
    assert [t for t in mnr if t["provenance"] == "reported"] == _first_per_trip(golden)
    assert all(t["observed_at"] is None for t in mnr)

    # THE HANDOFF. Last, so a regeneration can only ever write a body that passed every
    # assertion above. Compared as parsed JSON: the browser half reads values.
    if os.environ.get(REGENERATE_VAR) == "1":
        GOLDEN.write_text(json.dumps(body, indent=2) + "\n")
    assert body == json.loads(GOLDEN.read_text()), (
        f"/api/railroads no longer serves {GOLDEN.name}, which is the body the browser half "
        f"renders. If the change is intentional, regenerate with {REGENERATE_VAR}=1 and "
        "review the diff; otherwise the backend has drifted."
    )


@pytest.mark.anyio
async def test_the_placement_passs_predictions_are_open_question_1s_numbers(f01_world):
    """The fact the boards measured, re-derived on the map's surface, for open question 1
    of docs/reviews/audit-2026-09-05.md.

    LIRR dates each prediction by its own trip update's last recomputation rather than by a
    header that moves every poll, so most LIRR predictions are old on an ordinary evening.
    The boards showed that in words a rider reads. Section 6.3 leaves a trip placed from a
    prediction alone outside the gate (memo D1), so the same fact now stands under a marker
    a rider believes, and these are its numbers. They are asked of the SERVED body, so the
    entry and the capture cannot drift apart, exactly as test_health_observations.py holds
    the boards' half. Nothing here is a threshold: no assertion below decides whether such a
    prediction should place a train, which is what the entry leaves open."""
    body, _status = await _poll_and_serve(_Upstream(_restamp(_raw("LIRR")), _restamp(_raw("MNR"))))
    placed = [t for t in body["data"] if t["system"] == "LIRR" and t["provenance"] == "placed"]
    ages = sorted(NOW - t["observed_at"] for t in placed)
    assert len(ages) == 56
    assert sum(1 for age in ages if age > cache.OBS_MAX_S) == 49
    assert sum(1 for age in ages if age > cache.OBS_FRESH_S) == 53
    assert statistics.median(ages) == 1778.0
    assert (min(ages), max(ages)) == (7.0, 7255.0)
    # What gating this surface on the operator band too would cost, which is the size of
    # the decision the entry defers: the six estimates ride predictions 4 and 5 s old and
    # would survive it, and seven placements would.
    estimated = [
        t for t in body["data"] if t["system"] == "LIRR" and t["provenance"] == "estimated"
    ]
    assert len(estimated) == 6
    survivors = len(estimated) + sum(1 for age in ages if age <= cache.OBS_MAX_S)
    assert len(placed) + len(estimated) == 62 and survivors == 13


@pytest.mark.anyio
async def test_step_4_is_served_as_placed_in_a_built_world(f01_world):
    """The world the capture cannot reach: one of the 24 given a prediction 300 s old. Its
    own fix is still past OBS_MAX_S and its prediction is past OBS_FRESH_S and within
    OBS_MAX_S, so it is drawn at the stop that prediction names, labeled `placed` and dated
    by that prediction, and the counts move by exactly that one vehicle: 27/6/11/1/23."""
    lirr_bytes = _with_a_usable_old_prediction(_restamp(_raw("LIRR")))
    body, status = await _poll_and_serve(_Upstream(lirr_bytes, _restamp(_raw("MNR"))))
    rows = [t for t in body["data"] if t["trip_id"] == STEP_4_TRIP]
    assert len(rows) == 1
    (row,) = rows
    assert row["provenance"] == "placed" and row["observed_at"] == NOW - 300
    assert _own_fix_ages(lirr_bytes)[STEP_4_TRIP] > cache.OBS_MAX_S
    moved = {**LIRR_STEPS, "placed": 1, "suppressed": 23}
    assert body["systems"]["LIRR"]["positions"] == moved
    assert status["railroad_positions"]["LIRR"] == moved


@pytest.mark.anyio
async def test_an_undated_prediction_serves_no_marker_for_the_f01_witness(f01_world):
    """THE ACCEPTANCE'S OWN WITNESS, THROUGH THE PREDICTION DOOR. Train 521's fix is
    53676 s old and the map stopped drawing it; this world also clears the clock on the one
    prediction behind it, so nothing about this train carries a time a rider could read.
    The body must carry no row for it, and the counts must not move: it is still one of the
    24, on the status line and on no surface.

    WHAT THIS FORBIDS, and it is the acceptance itself. The ladder gates on a prediction's
    OWN clock. Gating on the clock the ROW is served, whose fallback is the feed header
    (design 3.3), scored that undated prediction 0 s old, because at this poll the header
    IS the clock: the body then carried train 521 as an `estimated` marker with observed_at
    == NOW, a train last seen fifteen hours ago dated to the second, and the counts read
    27/7/11/0/23. A rider's client qualifies what the row says about itself, so a row
    dated now is an unqualified one."""
    lirr_bytes = _with_an_undated_prediction(_restamp(_raw("LIRR")))
    assert _own_fix_ages(lirr_bytes)[F01_WITNESS] == 53676.0
    body, status = await _poll_and_serve(_Upstream(lirr_bytes, _restamp(_raw("MNR"))))
    assert [t for t in body["data"] if t["trip_id"] == F01_WITNESS] == []
    assert body["systems"]["LIRR"]["positions"] == LIRR_STEPS
    assert status["railroad_positions"]["LIRR"] == LIRR_STEPS


@pytest.mark.anyio
async def test_the_counts_are_last_known_while_a_system_fails(f01_world):
    """The same last-known rule as feed_timestamp, on both surfaces. Poll 2, 15 s later:
    LIRR decodes the step-4 world, so its counts move to this poll's, while Metro-North
    answers 503, so its block goes not-ok and keeps the counts of its last decode. Poll 3:
    both answer 503, a total failure, and both blocks keep poll 2's counts."""
    clock = f01_world
    mnr_bytes = _restamp(_raw("MNR"))
    first, _ = await _poll_and_serve(_Upstream(_restamp(_raw("LIRR")), mnr_bytes))
    assert first["systems"]["MNR"]["positions"] == MNR_STEPS

    clock[0] = NOW + REPOLL_S
    moved = {**LIRR_STEPS, "placed": 1, "suppressed": 23}
    second, status = await _poll_and_serve(
        _Upstream(_with_a_usable_old_prediction(_restamp(_raw("LIRR"))), 503)
    )
    mnr = second["systems"]["MNR"]
    assert mnr["ok"] is False and mnr["retained_since"] == NOW + REPOLL_S
    assert mnr["positions"] == MNR_STEPS
    assert second["systems"]["LIRR"]["ok"] is True
    assert second["systems"]["LIRR"]["positions"] == moved
    assert status["railroad_feeds"] == {"total": 2, "ok": 1, "failed": ["MNR"]}
    assert status["railroad_positions"] == {"LIRR": moved, "MNR": MNR_STEPS}

    clock[0] = NOW + 2 * REPOLL_S
    third, status = await _poll_and_serve(_Upstream(503, 503))
    assert all(block["ok"] is False for block in third["systems"].values())
    assert third["systems"]["LIRR"]["positions"] == moved
    assert third["systems"]["MNR"]["positions"] == MNR_STEPS
    assert status["railroad_positions"] == {"LIRR": moved, "MNR": MNR_STEPS}


@pytest.mark.parametrize("system", ["LIRR", "MNR"])
def test_the_world_is_the_committed_captures_moved_in_time_not_in_shape(system):
    """The restamp moves every time by DELTA and changes nothing else. Decoded at its own
    moved header, each moved capture gives its committed goldens' rows back with every
    time field moved by DELTA, Metro-North's single placement included, which its trip
    starts decide: shifting only the epoch fields would leave those starts eleven days
    behind, and every Metro-North trip scheduled to start later would be placed."""
    raw = _raw(system)
    moved = _restamp(raw)
    header = float(pb.FeedMessage.FromString(moved).header.timestamp)
    assert header == float(pb.FeedMessage.FromString(raw).header.timestamp) + DELTA
    stops = _stops(system)
    key = system.lower()

    def shifted(rows: list[dict]) -> list[dict]:
        fields = ("observed_at", "prev_time", "next_time")
        return [
            {k: (v + DELTA if k in fields and v is not None else v) for k, v in row.items()}
            for row in rows
        ]

    gps, _ = feeds._decode_railroad_vehicles(moved, system, header, stops)
    placed = feeds._decode_railroad_placements(moved, system, stops, header)
    gps_golden = json.loads((_FIXTURES / f"railroad_{key}_expected.json").read_text())
    placed_golden = json.loads((_FIXTURES / f"railroad_{key}_placed_expected.json").read_text())
    assert gps == shifted(gps_golden["trains"])
    assert placed == shifted(placed_golden["trains"])


def test_the_f01_world_is_served_in_the_browser_suites_frozen_time():
    """NOW IS THE BROWSER SUITE'S CLOCK, read out of api.js rather than trusted from the
    copy above, for the reason test_f03_boards.py gives: the handoff is only useful to the
    browser half if its times are already in that suite's frozen time, where fetched_at ==
    FROZEN_S zeroes the frontend's clock-skew offset. JavaScript's Date.UTC counts months
    from 0, hence the + 1."""
    match = re.search(r"const FROZEN_MS = Date\.UTC\(([^)]*)\);", _API_JS.read_text())
    assert match, f"{_API_JS} no longer declares FROZEN_MS as a Date.UTC literal"
    year, month_index, day, hour, minute, second = (int(part) for part in match[1].split(","))
    frozen_s = datetime(
        year, month_index + 1, day, hour, minute, second, tzinfo=timezone.utc
    ).timestamp()
    assert frozen_s == NOW
    golden = json.loads(GOLDEN.read_text())
    assert golden["fetched_at"] == frozen_s
    assert golden["served_at"] == frozen_s
    assert golden["feed_timestamp"] == frozen_s
