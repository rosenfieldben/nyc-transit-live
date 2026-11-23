"""NJ Transit identity under a schedule that is no longer the feed's.

THE INCIDENT (2026-11-12 to 2026-11-22, on the 2026-10-12 ledger). NJ Transit numbers its
trips 1..N afresh in each publication, a process loads one at start and never again, and
the decoder joined by trip_id and believed the join. Production held a schedule replaced at
least three times, so every joined row read on the 22nd wore another train's destination
and number.

TWO PUBLICATIONS, BUILT FROM THE COMMITTED FIXTURE. Its 131 trips, every one a different
train number, are put in route order and numbered 1 to 131: the NEWER publication. The
OLDER one is the same list before five trips were added to it at the twenty-first place, so
it numbers 126. The realtime feed is keyed to the newer, and the process holds the older:
trips 1 to 20 kept their ids, 21 to 126 now name other trains, and 127 to 131 are ids the
older publication never had. Nothing here reaches the network or can spend a mint.
"""

from __future__ import annotations

import inspect
import io
import json
import logging
import zipfile
from pathlib import Path

import httpx
import pytest
import test_q9_departed_rows as q9
from google.transit import gtfs_realtime_pb2 as pb

import main as app_module
import models
import njt_auth
import njt_static
import pollers
from feeds import njt

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures"
NOW = 1_795_400_000.0

KEPT, REFUTED, UNJOINED = 20, 106, 5
CODE = "njt-schedule-behind"


def _fixture_tables() -> dict:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted((FIXTURES / "njt_gtfs").iterdir()):
            if path.suffix == ".txt":
                zf.writestr(path.name, path.read_text(encoding="utf-8"))
    return njt_static._parse_zip(buffer)


PARSED = _fixture_tables()
STOPS = PARSED["stops"]
LINES = njt_static.build_njt_line_names(PARSED["routes"])
_INDEX = njt_static.build_njt_trip_index(PARSED["trips"])
_ORDER = sorted(
    PARSED["trips"],
    key=lambda t: (
        int(PARSED["trips"][t]["route_id"]),
        PARSED["trips"][t]["direction_id"] or "",
        PARSED["trips"][t]["short_name"],
        t,
    ),
)
# The newer publication's number for each fixture trip, and the two trip indexes a process
# would hold: the one the feed is keyed to, and the one it replaced.
NEWER_ID = {trip: str(n) for n, trip in enumerate(_ORDER, start=1)}
NEWER = {NEWER_ID[trip]: _INDEX[trip] for trip in _ORDER}
OLDER = {str(n): _INDEX[trip] for n, trip in enumerate([*_ORDER[:20], *_ORDER[25:]], start=1)}


def _realtime() -> bytes:
    """Every trip of the newer publication, running: a minute past its first call and two
    minutes short of its second, its calls the schedule's own stops in order."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(NOW)
    for trip in _ORDER:
        entity = feed.entity.add()
        entity.id = _INDEX[trip]["short_name"]
        tu = entity.trip_update
        tu.trip.trip_id = NEWER_ID[trip]
        tu.trip.route_id = _INDEX[trip]["route_id"]
        for at, call in enumerate(PARSED["stop_times"][trip]):
            stu = tu.stop_time_update.add()
            stu.stop_id = call["stop_id"]
            stu.stop_sequence = call["seq"]
            stu.arrival.time = int(NOW - 90 + 210 * at)
            stu.departure.time = int(NOW - 60 + 210 * at)
    return feed.SerializeToString()


RAW = _realtime()


def _decode(trips: dict, raw: bytes = RAW):
    return njt.decode_njt_trip_updates(raw, STOPS, trips, NOW, LINES)


def _rows(trains, arrivals):
    return [*trains, *(row for rows in arrivals.values() for row in rows)]


# ---------------------------------------------------------------------------
# The world itself
# ---------------------------------------------------------------------------


def test_the_world_is_the_incident():
    """WHAT THE OLD DECODER SERVED, stated before anything is claimed about the new one:
    against the older publication 106 of the 131 trips join another train, whose number is
    never theirs and whose destination is another place for 87 of them."""
    joined = {trip_id for trip_id in NEWER if trip_id in OLDER}
    wrong = {t for t in joined if OLDER[t]["short_name"] != NEWER[t]["short_name"]}
    assert (len(NEWER), len(OLDER), len(joined), len(wrong)) == (131, 126, 126, REFUTED)
    assert len(NEWER) - len(joined) == UNJOINED
    assert sum(OLDER[t]["headsign"] != NEWER[t]["headsign"] for t in wrong) == 87
    # (a) of the branch's Part 1, on this fixture: a headsign is the last stop's name.
    for trip in _ORDER:
        last = PARSED["stop_times"][trip][-1]["stop_id"]
        assert STOPS[last]["name"] == _INDEX[trip]["headsign"]


def test_no_row_shows_a_number_or_a_destination_the_newer_publication_does_not_give():
    """THE CLAIM. Every train and every board row, against the older publication, reads the
    number, the destination and the line the newer one gives the trip the feed named, and
    the mark says how it was come by."""
    trains, arrivals, _ts, identity = _decode(OLDER)
    assert len(trains) == 131
    for row in _rows(trains, arrivals):
        truth = NEWER[row["trip_id"]]
        assert (row["train_num"], row["headsign"], row["route_id"]) == (
            truth["short_name"],
            truth["headsign"],
            truth["route_id"],
        ), row
    assert [
        sum(t.get("identity") == mark for t in trains) for mark in (None, "refuted", "unjoined")
    ] == [
        KEPT,
        REFUTED,
        UNJOINED,
    ]
    board_rows = [row for rows in arrivals.values() for row in rows]
    assert [
        sum(r.get("identity") == mark for r in board_rows) for mark in (None, "refuted", "unjoined")
    ] == [146, 742, 53]
    assert identity.counts() == {
        "joined": 126,
        "number_mismatches": REFUTED,
        "no_identity": 0,
        "repeated_identity": 0,
    }


def test_a_clean_join_is_the_row_it_always_was():
    """BYTE FOR BYTE. The 20 trips whose ids both publications share decode, under either,
    to the same rows, with no identity key at all; under the publication the feed is keyed
    to, every row is one of those and no warning is raised."""
    old_trains, _old_arrivals, _ts, _identity = _decode(OLDER)
    trains, arrivals, _ts, identity = _decode(NEWER)
    kept = [t for t in old_trains if int(t["trip_id"]) <= KEPT]
    assert len(kept) == KEPT
    assert json.dumps(kept) == json.dumps([t for t in trains if int(t["trip_id"]) <= KEPT])
    assert not any("identity" in row for row in _rows(trains, arrivals))
    assert identity.warnings == []
    assert identity.counts() == {
        "joined": 131,
        "number_mismatches": 0,
        "no_identity": 0,
        "repeated_identity": 0,
    }
    # One whole row, literally, keys in order: what a clean join has always been.
    assert json.dumps(trains[0]) == (
        '{"system": "njt", "id": "1", "trip_id": "1", "route_id": "1", '
        '"headsign": "Atlantic City Terminal", "train_num": "4633", '
        '"observed_at": 1795400000.0, "latitude": 39.963633333333334, '
        '"longitude": -75.142148, "status": "in-transit", "provenance": "estimated", '
        '"stop_id": "124", "stop_name": "Pennsauken", "delay": null, '
        '"prev_lat": 39.956565, "prev_lon": -75.182327, "prev_time": 1795399940.0, '
        '"next_time": 1795400120.0}'
    )


def test_the_real_captures_shapes_survive_the_same_drift():
    """THE COMMITTED CAPTURE, RE-KEYED to the newer numbering and read against the older:
    its partial cancellations, skipped terminals and reordered stops are NJ Transit's own.
    All 86 running trips keep their own number. All but one keep their destination: the
    exception, 5435, is headsigned Raritan and its own update runs on to High Bridge, and
    the update is the one that is right about this run."""
    feed = pb.FeedMessage.FromString((FIXTURES / "njt_tu.pb").read_bytes())
    for entity in feed.entity:
        trip = entity.trip_update.trip
        trip.trip_id = NEWER_ID.get(trip.trip_id, "")
    raw = feed.SerializeToString()
    at = float(feed.header.timestamp)
    _t, _a, _ts, identity = njt.decode_njt_trip_updates(raw, STOPS, OLDER, at, LINES)
    assert identity.counts() == {
        "joined": 83,
        "number_mismatches": 75,
        "no_identity": 0,
        "repeated_identity": 0,
    }
    said = {}
    seen: set[str] = set()
    for position, entity in enumerate(feed.entity):
        tu = entity.trip_update
        if tu.trip.trip_id and tu.trip.schedule_relationship != pb.TripDescriptor.CANCELED:
            fields, _w = njt._identity(tu, entity.id, OLDER, STOPS, LINES, position, seen)
            said[tu.trip.trip_id] = (fields["train_num"], fields["headsign"])
    assert len(said) == 86
    differ = {
        t: (num, dest, NEWER[t]["headsign"])
        for t, (num, dest) in said.items()
        if (num, dest) != (NEWER[t]["short_name"], NEWER[t]["headsign"])
    }
    assert list(differ.values()) == [("5435", "High Bridge", "Raritan")]


# ---------------------------------------------------------------------------
# The rule's arms, one train at a time
# ---------------------------------------------------------------------------

S = {
    "1": {"id": "1", "name": "Trenton", "lat": 40.2, "lon": -74.7},
    "2": {"id": "2", "name": "Newark Penn Station", "lat": 40.7, "lon": -74.1},
    "3": {"id": "3", "name": "New York Penn Station", "lat": 40.75, "lon": -73.99},
}
T = {
    "77": {"route_id": "9", "headsign": "Port Jervis", "short_name": "0043"},
    "78": {"route_id": "9", "headsign": None, "short_name": "0045"},
    "79": {"route_id": "9", "headsign": "Dover", "short_name": None},
}
L = {"10": "Northeast Corridor"}
SKIPPED = pb.TripUpdate.StopTimeUpdate.SKIPPED
NO_DATA = pb.TripUpdate.StopTimeUpdate.NO_DATA


def _one(entity_id="7856", trip_id="77", calls=(("1", 1), ("2", 2), ("3", 3)), *, route_id="10"):
    """One trip update; a call is (stop_id, stop_sequence or None[, relationship])."""
    entity = pb.FeedEntity()
    entity.id = entity_id
    tu = entity.trip_update
    tu.trip.trip_id = trip_id
    if route_id:
        tu.trip.route_id = route_id
    for n, (stop_id, seq, *rel) in enumerate(calls):
        stu = tu.stop_time_update.add()
        stu.stop_id = stop_id
        if seq is not None:
            stu.stop_sequence = seq
        if rel:
            stu.schedule_relationship = rel[0]
        stu.arrival.time = int(NOW - 60 + 300 * n)
        stu.departure.time = int(NOW - 30 + 300 * n)
    return entity


def _said(entity, *, lines=L):
    fields, warnings = njt._identity(entity.trip_update, entity.id, T, S, lines, 0, set())
    return fields, [w.kind for w in warnings]


def test_a_refuted_join_takes_nothing_from_the_schedule():
    """Trip 77 is 0043 to Port Jervis in the schedule held, and the train under it is 7856
    on route 10, ending at New York Penn Station. All three of the row's words are the
    train's, the route included: 9 is the other train's."""
    fields, kinds = _said(_one())
    assert (fields["train_num"], fields["headsign"], fields["route_id"], fields["mark"]) == (
        "7856",
        "New York Penn Station",
        "10",
        {"identity": "refuted"},
    )
    assert kinds == ["number-mismatch"]
    # With no realtime route there is no line to name: the schedule's is not its own.
    fields, _kinds = _said(_one(route_id=""))
    assert fields["route_id"] is None


def test_the_warning_carries_the_pair_and_the_old_words():
    entity = _one()
    _fields, warnings = njt._identity(entity.trip_update, entity.id, T, S, L, 0, set())
    assert warnings == [
        njt.IdentityWarning(
            "number-mismatch",
            "entity.id '7856' does not match trip_short_name '0043' for trip '77'",
            "7856",
            "0043",
            "77",
        )
    ]


@pytest.mark.parametrize(
    "entity_id,trip_id,expected,mark",
    [
        # The number agrees: a clean join, the schedule's words.
        ("0043", "77", ("0043", "Port Jervis"), {}),
        # No entity.id, or a schedule with no number: nothing to contradict the join with.
        ("", "77", ("0043", "Port Jervis"), {}),
        ("7856", "79", ("7856", "Dover"), {}),
        # A clean join whose schedule gives no destination is named by its own last stop.
        ("0045", "78", ("0045", "New York Penn Station"), {}),
        # A trip the schedule does not have, and one with no trip_id at all (ADDED).
        ("1724", "1155", ("1724", "New York Penn Station"), {"identity": "unjoined"}),
        ("9001", "", ("9001", "New York Penn Station"), {"identity": "unjoined"}),
        # Neither identity: still a row, with no number to show.
        ("", "", (None, "New York Penn Station"), {"identity": "unjoined"}),
    ],
)
def test_who_is_refuted_and_who_is_not(entity_id, trip_id, expected, mark):
    fields, _kinds = _said(_one(entity_id, trip_id))
    assert (fields["train_num"], fields["headsign"]) == expected
    assert fields["mark"] == mark


@pytest.mark.parametrize(
    "calls,destination",
    [
        ((("1", 1), ("2", 2), ("3", 3)), "New York Penn Station"),
        # A partial cancellation ends where it now ends, not where it is headsigned.
        ((("1", 1), ("2", 2), ("3", 3, SKIPPED)), "Newark Penn Station"),
        # NO_DATA is no prediction, not no call: the train still goes there.
        ((("1", 1), ("2", 2), ("3", 3, NO_DATA)), "New York Penn Station"),
        # Feed order is not travel order: the capture's skipped stops come last in it.
        ((("1", 1), ("3", 3), ("2", 2, SKIPPED)), "New York Penn Station"),
        ((("3", 3), ("1", 1), ("2", 2)), "New York Penn Station"),
        # One stop without a sequence and the order is the feed's.
        ((("3", 3), ("1", 1), ("2", None)), "Newark Penn Station"),
        # The last stop is one this schedule does not know, so no stop is named, and the
        # last one it HAPPENS to know is not where the train is going.
        ((("1", 1), ("2", 2), ("99", 3)), "Northeast Corridor 7856"),
        # Nothing left to go to.
        ((("1", 1, SKIPPED), ("2", 2, SKIPPED)), "Northeast Corridor 7856"),
        ((), "Northeast Corridor 7856"),
    ],
)
def test_the_destination_is_the_stop_the_update_itself_ends_at(calls, destination):
    fields, _kinds = _said(_one(calls=calls))
    assert fields["headsign"] == destination


@pytest.mark.parametrize(
    "entity_id,route_id,lines,expected",
    [
        ("1724", "10", L, "Northeast Corridor 1724"),
        ("", "10", L, "Northeast Corridor"),
        ("1724", "10", {}, "Train 1724"),
        ("1724", "", L, "Train 1724"),
        ("", "", L, None),
    ],
)
def test_a_train_with_no_stop_to_name_is_never_named_by_a_bare_route_id(
    entity_id, route_id, lines, expected
):
    """(c): the line's name and the train number, as much of it as there is. "10 1724" is
    what riders read on 2026-11-22 for trains like these."""
    fields, _kinds = _said(_one(entity_id, "", (("99", 1),), route_id=route_id), lines=lines)
    assert fields["headsign"] == expected


def test_line_names_are_the_routes_endpoints_own():
    assert njt_static.build_njt_line_names(
        {
            "10": {"long_name": "Northeast Corridor", "short_name": "NEC"},
            "14": {"long_name": None, "short_name": "PASC"},
            "99": {"long_name": None, "short_name": None},
        }
    ) == {"10": "Northeast Corridor", "14": "PASC"}


# ---------------------------------------------------------------------------
# The counts, by kind
# ---------------------------------------------------------------------------


def _report(joined: int, *, mismatches=0, nameless=0, repeated=0) -> njt.IdentityReport:
    kinds = (
        [njt.NUMBER_MISMATCH] * mismatches
        + [njt.NO_IDENTITY] * nameless
        + [njt.REPEATED_IDENTITY] * repeated
    )
    return njt.IdentityReport(joined, [njt.IdentityWarning(kind, f"a {kind}") for kind in kinds])


def _feed_of(*entities) -> bytes:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(NOW)
    for entity in entities:
        feed.entity.add().CopyFrom(entity)
    return feed.SerializeToString()


def test_each_kind_is_its_own_count():
    """One mismatch, two with no identity, and one repeat of a clean train, in one feed."""
    raw = _feed_of(_one(), _one("", ""), _one("", ""), _one("0043", "78"), _one("0045", "78"))
    _t, _a, _ts, identity = njt.decode_njt_trip_updates(raw, S, T, NOW, L)
    assert identity.counts() == {
        "joined": 3,
        "number_mismatches": 2,
        "no_identity": 2,
        "repeated_identity": 1,
    }


def test_a_canceled_trip_is_in_no_count():
    """It is served nowhere, so it neither joins nor mismatches: five canceled trains under
    ids that now name others are not five rows a rider can read."""
    canceled = []
    for n in range(5):
        entity = _one(f"90{n}")
        entity.trip_update.trip.schedule_relationship = pb.TripDescriptor.CANCELED
        canceled.append(entity)
    _t, _a, _ts, identity = njt.decode_njt_trip_updates(_feed_of(*canceled), S, T, NOW, L)
    assert identity.counts() == {
        "joined": 0,
        "number_mismatches": 0,
        "no_identity": 0,
        "repeated_identity": 0,
    }


def test_the_log_line_counts_each_kind_and_says_nothing_when_there_is_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="feeds"):
        njt.log_cross_check(_report(40))
        assert caplog.records == []
        njt.log_cross_check(_report(40, mismatches=37, nameless=2, repeated=1))
    assert [record.getMessage() for record in caplog.records] == [
        "NJ Transit trip identity: 37 number mismatch(es) of 40 joined trip(s), "
        "2 with no identity, 1 repeated; first: a number-mismatch"
    ]


# ---------------------------------------------------------------------------
# When the count means the schedule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mismatches,joined,behind",
    [
        (0, 0, False),
        (4, 4, False),  # under the floor, however large a share
        (5, 5, True),
        (5, 20, True),  # exactly a quarter
        (5, 21, False),  # just under one
        (26, 104, True),
        (25, 104, False),
        (37, 37, True),  # the incident's one reconciled generation
        (40, 41, True),
        (0, 51, False),  # run 470
    ],
)
def test_the_threshold(mismatches, joined, behind):
    assert njt.schedule_behind(mismatches, joined) is behind


def test_the_numbers_are_the_briefs():
    assert (
        njt.SCHEDULE_BEHIND_MIN_MISMATCHES,
        njt.SCHEDULE_BEHIND_MIN_SHARE,
        njt.SCHEDULE_BEHIND_HOLD_S,
    ) == (5, 0.25, 600.0)


def test_a_run_is_started_lengthened_and_ended_only_by_decodes():
    behind, in_step = _report(40, mismatches=40), _report(40)
    run = njt.behind_run(None, behind, 1000.0)
    assert run == (1000.0, 1000.0)
    assert njt.behind_run(run, behind, 1020.0) == (1000.0, 1020.0)
    assert njt.behind_run(run, in_step, 1020.0) is None
    assert njt.behind_run(None, in_step, 1020.0) is None
    assert [
        njt.behind_past_hold(r)
        for r in (None, njt.BehindRun(1000.0, 1599.0), njt.BehindRun(1000.0, 1600.0))
    ] == [False, False, True]


# ---------------------------------------------------------------------------
# Served: the poller, the endpoints, /api/status and /healthz
# ---------------------------------------------------------------------------


@pytest.fixture
def world(monkeypatch):
    """Q9's world, NJ Transit ready to poll on one frozen clock, holding the OLDER
    publication. Assigned rather than patched, because Q9 patched these names (PR #130)."""
    clock = inspect.unwrap(q9.world)(monkeypatch)
    state = app_module.app.state
    state.njt_stops, state.njt_trips, state.njt_lines = STOPS, OLDER, LINES
    clock[0] = NOW
    return clock


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://drift") as http:
        return await http.get(path)


async def _poll(monkeypatch, report=None, *, raw=RAW, at=None, clock=None):
    """One real poll. With a report, its decode is replaced and only the report matters."""
    if clock is not None:
        clock[0] = at
    if report is None:

        async def post(url, form):
            return raw

        monkeypatch.setattr(njt_auth, "njt_post", post)
        monkeypatch.setattr(app_module, "fetch_njt_trains", njt.fetch_njt_trains)
    else:

        async def fetch(stops, trips, lines):
            if isinstance(report, Exception):
                raise report
            return [], {}, clock[0], report

        monkeypatch.setattr(app_module, "fetch_njt_trains", fetch)
    await pollers._refresh_njt(app_module.app, None)


async def test_a_rider_is_served_the_newer_publications_words(world, monkeypatch):
    """The real fetch, decode, poll and endpoints over the older publication: every train
    and every one of the stations' boards reads what the newer publication gives it, and a
    clean row's JSON has no identity key."""
    await _poll(monkeypatch)
    trains = (await _get("/api/njt-trains")).json()["trains"]
    rows = list(trains)
    for stop_id in STOPS:
        rows += (await _get(f"/api/njt-arrivals/{stop_id}")).json()["arrivals"]
    assert (len(trains), len(rows)) == (131, 1072)
    for row in rows:
        truth = NEWER[row["trip_id"]]
        assert (row["train_num"], row["headsign"], row["route_id"]) == (
            truth["short_name"],
            truth["headsign"],
            truth["route_id"],
        )
    marks = [row.get("identity", "clean") for row in rows]
    assert [marks.count(m) for m in ("clean", "refuted", "unjoined")] == [166, 848, 58]
    assert sum("identity" in row for row in rows) == 906, "and no clean row carries the key"
    assert (await _get("/api/status")).json()["njt_feeds"] == {
        "total": 1,
        "ok": 1,
        "failed": [],
        "joined": 126,
        "number_mismatches": 106,
        "no_identity": 0,
        "repeated_identity": 0,
    }


def test_a_clean_row_is_served_byte_for_byte_and_a_marked_one_says_so():
    """The wire, through the response models: no key for a clean join, the mark last for
    any other, and both row models alike."""
    arrival = {
        "train_num": "0043",
        "route_id": "9",
        "headsign": "Port Jervis",
        "arrival": 1.0,
        "departure": 2.0,
        "delay": None,
        "trip_id": "77",
        "observed_at": 3.0,
        "provenance": "reported",
    }
    clean = (
        '{"train_num":"0043","route_id":"9","headsign":"Port Jervis","arrival":1.0,'
        '"departure":2.0,"delay":null,"trip_id":"77","observed_at":3.0,"provenance":"reported"'
    )
    assert models.NjtArrival(**arrival).model_dump_json() == clean + "}"
    marked = models.NjtArrival(**arrival, identity="refuted").model_dump_json()
    assert marked == clean + ',"identity":"refuted"}'
    trains, _a, _ts, _i = _decode(OLDER)
    kept = models.NjtTrain(**trains[0]).model_dump()
    assert "identity" not in kept and kept["train_num"] == "4633"
    assert models.NjtTrain(**trains[-1]).model_dump()["identity"] == "unjoined"


async def test_the_code_is_raised_ten_minutes_into_an_unbroken_run(world, monkeypatch):
    """Behind from the first poll, every 20 s: off through NOW+580, on at NOW+600 and after,
    and off again at the first decode in step. 200 throughout: it never gates."""
    behind = _report(40, mismatches=40)
    on = []
    for at in range(0, 681, 20):
        await _poll(monkeypatch, behind, at=NOW + at, clock=world)
        response = await _get("/healthz")
        assert (response.status_code, response.json()["status"]) == (200, "pass")
        if CODE in response.json()["degraded"]:
            on.append(at)
    assert on == [600, 620, 640, 660, 680]
    await _poll(monkeypatch, _report(40, mismatches=4), at=NOW + 700, clock=world)
    assert CODE not in (await _get("/healthz")).json()["degraded"]
    # And the next run starts from nothing.
    await _poll(monkeypatch, behind, at=NOW + 720, clock=world)
    assert CODE not in (await _get("/healthz")).json()["degraded"]


async def test_failed_polls_neither_age_a_run_nor_end_it(world, monkeypatch):
    """One bad decode and then an outage: the code stays off for the hour, since nothing
    has decoded twice. The first decode after it, still behind, closes the hold."""
    down = njt_auth.NjtUpstreamError("NJ Transit returned HTTP 503: (empty body)")
    await _poll(monkeypatch, _report(40, mismatches=40), at=NOW, clock=world)
    for at in (20, 600, 620, 3600):
        await _poll(monkeypatch, down, at=NOW + at, clock=world)
        assert CODE not in (await _get("/healthz")).json()["degraded"]
    await _poll(monkeypatch, _report(40, mismatches=40), at=NOW + 3620, clock=world)
    assert CODE in (await _get("/healthz")).json()["degraded"]
    # And an outage after it is raised does not clear it: nothing has said otherwise.
    await _poll(monkeypatch, down, at=NOW + 3640, clock=world)
    assert CODE in (await _get("/healthz")).json()["degraded"]


async def test_a_night_too_quiet_to_judge_clears_it(world, monkeypatch):
    """The floor is five mismatches, so a late feed of four trains is in step by the rule
    whatever they say, and the code clears until there are trains enough to judge by."""
    for at in (0, 600):
        await _poll(monkeypatch, _report(40, mismatches=40), at=NOW + at, clock=world)
    assert CODE in (await _get("/healthz")).json()["degraded"]
    await _poll(monkeypatch, _report(4, mismatches=4), at=NOW + 620, clock=world)
    assert CODE not in (await _get("/healthz")).json()["degraded"]


def test_the_code_is_degraded_last_and_never_gates():
    assert models.HEALTH_DEGRADED_CODES[-1] == CODE
    assert CODE not in models.HEALTH_GATING_CODES
