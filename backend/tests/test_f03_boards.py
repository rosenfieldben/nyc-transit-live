"""F03, the arrival boards (contract 6.2): the acceptance world, envelope half.

THE AUDITOR'S ACCEPTANCE, VERBATIM (docs/design/freshness-contract.md 1.3 and 6.2):

    "repeatedly returning an old, valid HTTP-200 feed makes its countdowns visibly
    qualified as stale; other healthy contributors remain distinguishable."

THIS MODULE IS THE ENVELOPE HALF. It builds the world the audit named, with the one
addition 6.2 asks for: a healthy group beside the aged one, so that "qualified" is a
statement about SOME rows of a board rather than all of them. It asserts that the served
board carries every number such a qualifier needs, on every row and for every
contributor. The browser half renders the same board. This module writes the poll-1 body
of /api/subway-arrivals/219 to tests/e2e/fixtures/f03_board_219.json and fails whenever
the live body stops equalling it. A change to what the backend serves therefore goes red
here before the browser suite can drift from it, which makes that file the handoff
between the two halves.

THE WORLD. Production code throughout, except the socket:

  * Upstream bytes come from the committed capture backend/tests/fixtures/subway_1_7_s.pb.
    It is re-stamped by ONE constant delta, so its header sits exactly 600 s behind the
    poll clock. This is the same shift f03_arrivals_content_freshness.py and
    test_api._shift_capture apply: a feed that is old, not a feed that is wrong. It is
    served on the 1-7+S URL, the capture's own group and the one that reaches 219.
  * The healthy group is ACE, served a copy of the SAME capture stamped 5 s behind the
    clock. That is the "content 5s old at poll time (fresh)" convention of
    tests/e2e/fixtures/api.js. Every trip_id in the copy is renamed with an "h-"
    prefix. The rename is load-bearing: feeds/subway.combine_group_arrivals keeps a
    trip id once across groups, owned by the first group in SUBWAY_FEED_URLS order.
    1-7+S comes first, so without the rename every healthy row at 219 is swallowed
    (measured: 12 of 12 served rows come back aged). Route ids are NOT renamed, on
    purpose: an aged "5" and a healthy "5" share a board, so the only thing that tells
    them apart is the served clock, which is what the acceptance is about.
  * Why ACE: every group sorts after 1-7+S in SUBWAY_FEED_URLS, so no choice changes
    which copy owns a shared trip id. ACE is also the one other group the browser suite
    already names (subwaysWithSystems in tests/e2e/fixtures/api.js), so this board
    introduces no contributor the browser world has not seen.
  * The other six groups get a valid, fresh, header-only feed. The poll is therefore a
    fully successful eight-group poll with no failure anywhere.
  * main.fetch_subway_trains does the fetching, called with a stub client that returns
    real httpx.Response objects per URL. pollers._refresh_subways does the refresh.
    main.app is driven over httpx.ASGITransport. Station names come from
    subway_1_7_s_stops.json through feeds.subway._platform_direction, as the f03
    script's prime_app derives them, so 219 is served as "Prospect Av".

THE CLOCK IS THE BROWSER SUITE'S. NOW is FROZEN_S of tests/e2e/fixtures/api.js
(Date.UTC(2026, 6, 2, 12) / 1000). time.time is patched to it exactly as the 6.1
acceptance test patches it, so served_at and fetched_at are NOW to the bit and the served
JSON is already in the browser's fixture time. With fetched_at == FROZEN_S, the
frontend's clock-skew offset is 0 and a countdown reads exactly arrival - FROZEN_S.
test_the_f03_board_is_served_in_the_browser_suites_frozen_time reads that constant out of
api.js instead of trusting this copy of it.

MEASURED AT 219 (Prospect Av) ON POLL 1, before and after the per-direction cap
(feeds/shared.ARRIVALS_PER_DIRECTION = 6, soonest first):

    decoded rows at 219         Northbound  Southbound
    1-7+S (aged, header -600)       16           9
    ACE   (healthy, header -5)      18          10
    served after the cap         3 + 3       3 + 3     (aged + healthy, interleaved)

Northbound serves aged rows at +120, +174 and +598 s interleaved with healthy rows at
+177, +305 and +715 s. Southbound serves aged rows at -37, +152 and +567 s interleaved
with healthy rows at +194, +558 and +747 s. The Northbound +120 row is the capture's own
"prediction two minutes ahead": its header + 720 s, landing at NOW + 120.

To regenerate the golden after an INTENTIONAL change to what this board serves, from
backend/:

    F03_BOARD_REGENERATE=1 pytest -q tests/test_f03_boards.py

Then review the diff of tests/e2e/fixtures/f03_board_219.json. The browser half renders
that file, so its diff IS the change a rider will see. The file is written only at the
END of a run in which every acceptance assertion held, so a regeneration cannot commit a
board that fails the acceptance.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from google.transit import gtfs_realtime_pb2 as pb

import main as app_module
import models
import pollers
from feeds.subway import SUBWAY_FEED_URLS, _platform_direction
from routes import status as status_routes

_REPO = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).parent / "fixtures"
_CAPTURE = _FIXTURES / "subway_1_7_s.pb"
_STOPS = _FIXTURES / "subway_1_7_s_stops.json"
_API_JS = _REPO / "tests" / "e2e" / "fixtures" / "api.js"
GOLDEN = _REPO / "tests" / "e2e" / "fixtures" / "f03_board_219.json"
REGENERATE_VAR = "F03_BOARD_REGENERATE"

NOW = 1782993600.0  # FROZEN_S of tests/e2e/fixtures/api.js: 2026-07-02T12:00:00Z
AGED_LAG_S = 600.0  # the audit's "header ten minutes old"
HEALTHY_LAG_S = 5.0  # api.js's "content 5s old at poll time (fresh)"
REPOLL_S = 15.0  # the second successful poll of the same old bytes
STATION_ID = "219"  # Prospect Av (2/5), reached by the 1-7+S capture
AGED_GROUP = "1-7+S"
HEALTHY_GROUP = "ACE"
HEALTHY_PREFIX = "h-"


def _restamp(raw: bytes, base_now: float, lag_s: float, trip_prefix: str = "") -> bytes:
    """The committed capture with its header exactly `lag_s` before `base_now`.

    ONE CONSTANT DELTA reaches every timestamp the feed carries: header, trip update,
    each stop time, each vehicle. The capture's internal structure (which prediction is
    how far ahead of its own header) is therefore preserved exactly. That is the shift
    test_api._shift_capture and the f03 reproduction apply. `trip_prefix` is the one
    addition: it renames every trip_id, on the trip update and the vehicle alike, so a
    second copy of the capture survives the cross-group dedup as a distinct contributor.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    delta = int(round(base_now - lag_s)) - int(feed.header.timestamp)
    feed.header.timestamp = int(feed.header.timestamp) + delta
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
            if trip_prefix and tu.trip.trip_id:
                tu.trip.trip_id = trip_prefix + tu.trip.trip_id
        if entity.HasField("vehicle"):
            vehicle = entity.vehicle
            if vehicle.timestamp:
                vehicle.timestamp += delta
            if trip_prefix and vehicle.trip.trip_id:
                vehicle.trip.trip_id = trip_prefix + vehicle.trip.trip_id
    return feed.SerializeToString()


def _quiet_feed(timestamp: float) -> bytes:
    """A valid, fresh, EMPTY feed: a real header carrying the version field parse_feed
    requires, and no entities. It decodes as healthy and contributes nothing at 219."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "1.0"
    feed.header.timestamp = int(timestamp)
    return feed.SerializeToString()


class _Upstream:
    """Stands in for httpx.AsyncClient inside the REAL fetch_subway_trains.

    It returns real httpx.Response objects, so raise_for_status and .content behave as
    they do in production. An unknown URL raises KeyError, which the fetch turns into a
    failed group, so a mis-keyed world shows up as a group that is not ok.
    """

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self.bodies = bodies

    async def get(self, url: str) -> httpx.Response:
        return httpx.Response(200, content=self.bodies[url], request=httpx.Request("GET", url))


def _upstream(aged_bytes: bytes, raw: bytes, at: float) -> _Upstream:
    """One poll's upstream. The aged group gets `aged_bytes` as handed in, which is the
    same object on every poll. The healthy group gets a copy re-stamped against `at`,
    and the six quiet groups get a header stamped `at`."""
    bodies = {url: _quiet_feed(at) for url in SUBWAY_FEED_URLS.values()}
    bodies[SUBWAY_FEED_URLS[AGED_GROUP]] = aged_bytes
    bodies[SUBWAY_FEED_URLS[HEALTHY_GROUP]] = _restamp(raw, at, HEALTHY_LAG_S, HEALTHY_PREFIX)
    return _Upstream(bodies)


@pytest.fixture
def f03_world(monkeypatch):
    """The app.state a lifespan would have built for this world, and the patched clock.

    ISOLATED BY monkeypatch, NOT RESET BY HAND. test_api.py's cache fixture resets the
    feed cache and the health blocks but not the arrivals indexes, and _refresh_subways
    writes four attributes besides the cache. Every attribute this world primes or the
    poll writes is set through monkeypatch here. Teardown therefore restores each one to
    exactly what it was, or deletes it if it did not exist, and no later test inherits a
    219 board.

    THE OTHER FOUR ARRIVALS INDEXES ARE PRIMED EMPTY, the same five test_api.py's
    healthz_env resets (_ARRIVALS_INDEXES there), because the /healthz probe this world
    reads counts every one of them. Left alone, they hold whatever earlier tests left:
    run after test_api.py, the probe named observations-qualified off those leftover
    rows even with the handler blinded to the subway, so its assertion here did not
    depend on this world at all.

    Returns the clock as a one-element list, so the test can advance it between polls.
    """
    stops = json.loads(_STOPS.read_text())
    stations: dict[str, dict] = {}
    for stop_id, stop in stops.items():
        _, station_id = _platform_direction(stop_id)
        stations.setdefault(
            station_id, {"name": stop["name"], "lat": stop["lat"], "lon": stop["lon"]}
        )
    primed = {
        "feed_cache": {"subways": app_module._fresh_entry()},
        "subway_feed_health": None,
        "subway_static_status": "ready",
        "subway_stops": stops,
        "subway_stations": stations,
        "subway_station_routes": {},
        "subway_arrivals": {},
        "subway_arrivals_by_system": {},
        "subway_positions": {},
        "railroad_arrivals": {},
        "path_arrivals": {},
        "ferry_arrivals": {},
        "njt_arrivals": {},
    }
    for name, value in primed.items():
        monkeypatch.setattr(app_module.app.state, name, value, raising=False)
    clock = [NOW]
    monkeypatch.setattr(app_module.time, "time", lambda: clock[0])
    return clock


async def _poll_and_serve(upstream: _Upstream) -> tuple[dict, dict]:
    """One real refresh, then the board and the vehicle envelope as SERVED JSON."""
    await pollers._refresh_subways(app_module.app, upstream)
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://f03") as http:
        board = await http.get(f"/api/subway-arrivals/{STATION_ID}")
        vehicles = await http.get("/api/subways")
    assert board.status_code == 200, board.text
    assert vehicles.status_code == 200, vehicles.text
    return board.json(), vehicles.json()


def _split(board: dict) -> dict[str, tuple[list[dict], list[dict]]]:
    """Each direction's served rows as (aged, healthy), told apart by the trip-id prefix
    the healthy copy was given. The prefix is this world's label, not a field the system
    reads, so _assert_rows_dated_by_contributor also checks that each labelled row
    really came out of that group's own per-group index."""
    return {
        direction: (
            [row for row in rows if not row["trip_id"].startswith(HEALTHY_PREFIX)],
            [row for row in rows if row["trip_id"].startswith(HEALTHY_PREFIX)],
        )
        for direction, rows in board["directions"].items()
    }


def _assert_rows_dated_by_contributor(board: dict, aged_at: float, healthy_at: float) -> None:
    """Both kinds of row are on the board in both directions, and each carries its own
    contributor's content clock.

    BOTH DIRECTIONS, NOT "AT LEAST ONE", because both are measured to hold (3 aged and 3
    healthy of 6, interleaved, each way) and the browser half renders both. A board on
    which one direction lost its healthy rows would be a weaker world handed over under
    the same name.
    """
    by_system = app_module.app.state.subway_arrivals_by_system
    for direction, (aged, healthy) in _split(board).items():
        # THE CLAUSE "OTHER HEALTHY CONTRIBUTORS REMAIN" STARTS HERE: a healthy
        # contributor whose rows never reach the board is not distinguishable, it is
        # absent. combine_group_arrivals keeps a trip id once across groups, first group
        # wins, and 1-7+S is first, so an un-renamed healthy copy loses every row it
        # shares with the aged one.
        assert aged and healthy, (
            f"{direction} at {STATION_ID} must serve rows from both contributors; got "
            f"{len(aged)} aged and {len(healthy)} healthy. If healthy is 0, the "
            "cross-group trip_id dedup in feeds/subway.combine_group_arrivals swallowed "
            "the healthy copy."
        )
        aged_index = {row["trip_id"] for row in by_system[AGED_GROUP][STATION_ID][direction]}
        healthy_index = {row["trip_id"] for row in by_system[HEALTHY_GROUP][STATION_ID][direction]}
        # The label is the contributor: each row's trip is in its own group's index and
        # not in the other's.
        assert {row["trip_id"] for row in aged} <= aged_index - healthy_index
        assert {row["trip_id"] for row in healthy} <= healthy_index - aged_index
        # THE PER-ROW CONTENT CLOCK, which is what makes the qualification a statement
        # about some rows rather than the whole board.
        assert {row["observed_at"] for row in aged} == {aged_at}, aged
        assert {row["observed_at"] for row in healthy} == {healthy_at}, healthy
        # Freshly decoded on this poll, so every row is `reported`. A `retained` row
        # would mean the world had a failure in it, which it must not.
        assert {row["provenance"] for row in aged + healthy} == {"reported"}


@pytest.mark.anyio
async def test_acceptance_old_valid_feed_rows_are_dated_stale_and_healthy_rows_stay_distinguishable(
    f03_world,
):
    """THE F03 ACCEPTANCE, ENVELOPE HALF, in the auditor's words:

        "repeatedly returning an old, valid HTTP-200 feed makes its countdowns visibly
        qualified as stale; other healthy contributors remain distinguishable."

    Every clause is asserted on ONE served /api/subway-arrivals/219 body per poll. (a)
    The aged group's rows are dated 600 s old and the healthy group's 5 s old, on the
    same board. (b) The systems map names exactly those two contributors, each ok and
    each with its own content clock, and the envelope's clock is the worst contributor's.
    (c) The poll clocks are this poll's. (d) REPEATEDLY: a second successful poll 15 s
    later, of the identical old bytes, ages the aged rows from 600 s to 615 s while the
    healthy rows stay fresh and every group still reports ok. "Visibly" is the browser
    half's word. It is rendered from tests/e2e/fixtures/f03_board_219.json, which the
    last assertion pins to the poll-1 body asserted here.

    To regenerate that file after an INTENTIONAL change, from backend/:
    F03_BOARD_REGENERATE=1 pytest -q tests/test_f03_boards.py, then review its diff.
    """
    clock = f03_world
    raw = _CAPTURE.read_bytes()
    # BUILT ONCE AND SERVED ON BOTH POLLS, byte for byte: "repeatedly returning an old,
    # valid HTTP-200 feed" is a provider handing back the same message, not a new one
    # that happens to be old.
    aged_bytes = _restamp(raw, NOW, AGED_LAG_S)

    board, vehicles = await _poll_and_serve(_upstream(aged_bytes, raw, NOW))

    # THE TRAP THE ACCEPTANCE IS ABOUT, asserted rather than assumed: all eight groups
    # decoded and report ok, so no pre-6.1 measure could tell the aged one apart.
    assert len(vehicles["systems"]) == len(SUBWAY_FEED_URLS)
    assert all(block["ok"] for block in vehicles["systems"].values())
    assert all(block["retained_since"] is None for block in vehicles["systems"].values())

    # THE OPERATOR HALF, read in this same world: /healthz names the state riders are
    # in, which no pre-6.2 code could. Of every system the probe reads, exactly one is
    # serving nothing current, the aged group; ACE's current rows and the six quiet
    # groups keep the rule quiet for theirs. f03_world primes every other arrivals index
    # empty, so the code asserted below can only have come from this world's subway.
    served = status_routes._served_arrival_systems(app_module.app.state)
    assert status_routes._systems_serving_nothing_current(served, NOW) == [f"subway:{AGED_GROUP}"]
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://f03") as http:
        health = (await http.get("/healthz")).json()
    assert models.HEALTH_OBSERVATIONS_QUALIFIED in health["degraded"], health

    # (a) BOTH KINDS OF ROW, EACH DATED BY ITS OWN CONTRIBUTOR.
    _assert_rows_dated_by_contributor(board, NOW - AGED_LAG_S, NOW - HEALTHY_LAG_S)

    # THE COUNTDOWN THE AUDIT QUOTED is on this board: the capture's own stop at header
    # + 720 s, which is a two-minute countdown built from a ten-minute-old prediction.
    # The row now says so in a field.
    (prediction,) = [
        row for row in board["directions"]["Northbound"] if row["arrival"] == NOW + 120.0
    ]
    assert not prediction["trip_id"].startswith(HEALTHY_PREFIX)
    assert prediction["observed_at"] == NOW - AGED_LAG_S
    assert prediction["arrival"] - prediction["observed_at"] == 720.0

    # (b) THE TWO CONTRIBUTORS, BY NAME, EACH WITH ITS OWN CONTENT CLOCK. The six quiet
    # groups decoded too and contribute nothing at 219, so they are not on this board.
    systems = board["systems"]
    assert sorted(systems) == sorted([AGED_GROUP, HEALTHY_GROUP])
    assert all(block["ok"] for block in systems.values())
    assert all(block["retained_since"] is None for block in systems.values())
    assert systems[AGED_GROUP]["feed_timestamp"] == NOW - AGED_LAG_S
    assert systems[HEALTHY_GROUP]["feed_timestamp"] == NOW - HEALTHY_LAG_S
    assert systems[HEALTHY_GROUP]["feed_timestamp"] - systems[AGED_GROUP]["feed_timestamp"] == (
        AGED_LAG_S - HEALTHY_LAG_S
    )
    # The worst contributor answers for the envelope. Each block's clock is also the
    # one its own rows carry, which (a) pinned row by row.
    assert board["feed_timestamp"] == systems[AGED_GROUP]["feed_timestamp"]

    # (c) THE POLL CLOCK AND THE SERVE CLOCK ARE THIS POLL'S, to the bit. Both are NOW
    # because the patched clock is the browser suite's FROZEN_S.
    assert board["fetched_at"] == NOW
    assert app_module.app.state.feed_cache["subways"]["fetched_at"] == NOW
    assert all(block["fetched_at"] == NOW for block in systems.values())
    assert board["served_at"] == NOW
    first_ages = {
        row["trip_id"]: board["served_at"] - row["observed_at"]
        for aged, _ in _split(board).values()
        for row in aged
    }
    assert set(first_ages.values()) == {AGED_LAG_S}

    # (d) REPEATEDLY. The clock moves 15 s, the aged group hands back the IDENTICAL old
    # bytes, and the healthy group sends a freshly stamped copy. Both fetches succeed.
    clock[0] = NOW + REPOLL_S
    board_2, vehicles_2 = await _poll_and_serve(_upstream(aged_bytes, raw, clock[0]))

    assert board_2["served_at"] == NOW + REPOLL_S
    assert board_2["fetched_at"] == NOW + REPOLL_S
    # Every group still reports ok, and every one was polled just now: by every
    # operational measure this is a healthy feed.
    assert all(block["ok"] for block in vehicles_2["systems"].values())
    assert all(block["fetched_at"] == NOW + REPOLL_S for block in vehicles_2["systems"].values())
    # THE AGED ROWS' CLOCK STANDS STILL WHILE THE HEALTHY ROWS' ADVANCES.
    _assert_rows_dated_by_contributor(board_2, NOW - AGED_LAG_S, NOW + REPOLL_S - HEALTHY_LAG_S)
    second_ages = {
        row["trip_id"]: board_2["served_at"] - row["observed_at"]
        for aged, _ in _split(board_2).values()
        for row in aged
    }
    # CONTENT AGE GROWS WITH THE CLOCK, 600 s to 615 s, on every aged row that is on
    # both boards. That is the number a qualifier is computed from, and it is the one
    # a fetched_at-based age cannot see: fetched_at was fresh both times.
    assert set(second_ages.values()) == {AGED_LAG_S + REPOLL_S}
    assert set(first_ages) & set(second_ages)
    healthy_ages = {
        board_2["served_at"] - row["observed_at"]
        for _, healthy in _split(board_2).values()
        for row in healthy
    }
    assert healthy_ages == {HEALTHY_LAG_S}
    # The same two-minute prediction, 15 s closer and exactly as old as it was.
    (again,) = [
        row
        for row in board_2["directions"]["Northbound"]
        if row["trip_id"] == prediction["trip_id"]
    ]
    assert again["arrival"] == prediction["arrival"]
    assert again["observed_at"] == prediction["observed_at"]
    assert again["arrival"] - board_2["served_at"] == 120.0 - REPOLL_S

    systems_2 = board_2["systems"]
    assert sorted(systems_2) == sorted([AGED_GROUP, HEALTHY_GROUP])
    assert all(block["ok"] for block in systems_2.values())
    assert systems_2[AGED_GROUP]["feed_timestamp"] == NOW - AGED_LAG_S
    assert systems_2[HEALTHY_GROUP]["feed_timestamp"] == NOW + REPOLL_S - HEALTHY_LAG_S
    assert board_2["feed_timestamp"] == NOW - AGED_LAG_S

    # THE HANDOFF. Last, so a regeneration can only ever write a board that passed every
    # assertion above. Compared as parsed JSON: the browser half reads values, not
    # whitespace.
    if os.environ.get(REGENERATE_VAR) == "1":
        GOLDEN.write_text(json.dumps(board, indent=2) + "\n")
    assert board == json.loads(GOLDEN.read_text()), (
        f"/api/subway-arrivals/{STATION_ID} no longer serves {GOLDEN.name}, which is the "
        "board the browser half renders. If the change is intentional, regenerate with "
        f"{REGENERATE_VAR}=1 and review the diff; otherwise the backend has drifted."
    )


def test_the_f03_board_is_served_in_the_browser_suites_frozen_time():
    """NOW IS THE BROWSER SUITE'S CLOCK, and this reads it out of api.js rather than
    trusting the copy above.

    The golden is only useful to the browser half if its times are already in that
    suite's frozen time. There, fetched_at == FROZEN_S zeroes the frontend's clock-skew
    offset, so the +120 s row reads as a two-minute countdown. If FROZEN_MS moved and
    this module did not, every countdown on the rendered board would be off by the
    difference. C2i in tests/e2e/smoke.spec.js would fail then too, but only on the
    symptom, as a wall of countdown literals that no longer match; this test names the
    cause directly. JavaScript's Date.UTC counts months from 0, hence the + 1.
    """
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
