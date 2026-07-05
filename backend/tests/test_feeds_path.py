"""Golden + unit tests for the PATH bridge-feed decode: placement + arrivals.

Two layers, matching test_feeds_railroad.py:
  - Synthetic feeds exercise every decode rule (single next-arrival stop,
    direction bucketing, unresolvable-stop skips, the churn and duplicate
    generation behaviors the bridge exhibits, grace boundaries).
  - Goldens lock the decode against REAL captured payloads (path_rt_*.pb,
    written by backend/scripts/gen_path_rt_fixture.py with the capture rules
    documented there) plus the committed parent-stops snapshot
    (path_stops.json). They skip loudly until the fixtures are captured,
    which needs egress to the bridge host.

The churn and duplicate pairs are the PATH-specific risk: bridge trip ids
churn 100% when the upstream refreshes (recorded in path_static's module
docstring), and identical consecutive generations are normal (the bridge
regenerates faster than the upstream refreshes). The decoder must treat both
as ordinary snapshots: no cross-poll identity, no staleness inference.
"""

import json
from pathlib import Path

import httpx
import pytest
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as pb

import feeds

FIXTURES = Path(__file__).parent / "fixtures"

NOW = 1000.0

# Real PATH parent-station ids and coordinates (the 13a stops table shape).
PATH_STOPS = {
    "26733": {"id": "26733", "name": "Newark", "lat": 40.73454, "lon": -74.16375},
    "26734": {"id": "26734", "name": "World Trade Center", "lat": 40.71271, "lon": -74.01193},
    "26727": {"id": "26727", "name": "Journal Square", "lat": 40.73342, "lon": -74.06289},
}

_SKIPPED = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.SKIPPED
_NO_DATA = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.NO_DATA
_CANCELED = pb.TripDescriptor.ScheduleRelationship.CANCELED


def _path_entity(feed, trip_id, route_id="862", direction_id=None, stops=(), canceled=False):
    """Add a bridge-style trip_update entity. stops = [(stop_id, time | None
    [, schedule_rel]), ...]; the real bridge carries exactly one stop per
    entity, so most tests pass a single-item list."""
    ent = feed.entity.add()
    ent.id = trip_id
    tu = ent.trip_update
    tu.trip.trip_id = trip_id
    tu.trip.route_id = route_id
    if direction_id is not None:
        tu.trip.direction_id = direction_id
    if canceled:
        tu.trip.schedule_relationship = _CANCELED
    for spec in stops:
        sid, t = spec[0], spec[1]
        stu = tu.stop_time_update.add()
        stu.stop_id = sid
        if t is not None:
            stu.arrival.time = int(t)
        if len(spec) > 2 and spec[2] is not None:
            stu.schedule_relationship = spec[2]
    return ent


def _decode(feed, stops=PATH_STOPS, now=NOW, header_ts=1782000000):
    feed.header.gtfs_realtime_version = "2.0"
    if header_ts is not None:
        feed.header.timestamp = header_ts
    return feeds._decode_path_feed(feed.SerializeToString(), stops, now)


# ---------------- placement ----------------


def test_train_placed_at_next_station_with_null_prev():
    feed = pb.FeedMessage()
    _path_entity(feed, "T1", direction_id=1, stops=[("26733", NOW + 180)])
    trains, _, _ = _decode(feed)
    assert len(trains) == 1
    t = trains[0]
    assert (t["latitude"], t["longitude"]) == (40.73454, -74.16375)  # Newark
    assert t["stop_id"] == "26733" and t["stop_name"] == "Newark"
    assert t["next_time"] == NOW + 180
    assert t["route_id"] == "862"
    # No carry-forward in 13b: bridge trip ids do not survive an upstream
    # refresh, so every train's prev anchors are null on every poll.
    assert (t["prev_lat"], t["prev_lon"], t["prev_time"]) == (None, None, None)


def test_direction_labels_from_direction_id():
    feed = pb.FeedMessage()
    _path_entity(feed, "NJ", direction_id=0, stops=[("26733", NOW + 60)])
    _path_entity(feed, "NY", direction_id=1, stops=[("26734", NOW + 60)])
    _path_entity(feed, "NONE", stops=[("26727", NOW + 60)])  # no direction_id
    trains, _, _ = _decode(feed)
    dirs = {t["trip_id"]: t["direction"] for t in trains}
    # Verified against static trips.txt: 0 is the New Jersey-bound terminal,
    # 1 the New York-bound one. A missing direction_id stays null on the
    # train (the "Trains" residual is an arrivals bucket, not a direction).
    assert dirs == {"NJ": "To New Jersey", "NY": "To New York", "NONE": None}


def test_first_future_stop_used_and_later_ones_ignored():
    # The bridge serves one stop today; if it ever adds more, the decode must
    # use the FIRST still-upcoming stop and ignore the rest, for placement
    # AND arrivals (no downstream stops are indexed).
    feed = pb.FeedMessage()
    _path_entity(
        feed,
        "T1",
        direction_id=1,
        stops=[("26733", NOW - 300), ("26734", NOW + 120), ("26727", NOW + 600)],
    )
    trains, arrivals, _ = _decode(feed)
    assert len(trains) == 1
    assert trains[0]["stop_id"] == "26734"  # first still-upcoming, not the past one
    assert trains[0]["next_time"] == NOW + 120
    assert set(arrivals) == {"26734"}  # the later stop is NOT indexed
    assert trains[0]["prev_lat"] is None  # the past stop never becomes a prev anchor


def test_unresolvable_stop_id_skipped_without_failing_the_decode():
    feed = pb.FeedMessage()
    _path_entity(feed, "GHOST", direction_id=1, stops=[("99999", NOW + 60)])  # unknown station
    _path_entity(feed, "OK", direction_id=0, stops=[("26733", NOW + 60)])
    trains, arrivals, _ = _decode(feed)
    assert [t["trip_id"] for t in trains] == ["OK"]  # the good entity still decodes
    assert set(arrivals) == {"26733"}


def test_missing_arrival_time_places_with_null_next_time_and_no_arrival_row():
    # Railroad convention: a stop with no time still anchors placement (the
    # no-times fallback) but cannot be an arrival row (nothing to count down).
    feed = pb.FeedMessage()
    _path_entity(feed, "T1", direction_id=1, stops=[("26733", None)])
    trains, arrivals, _ = _decode(feed)
    assert len(trains) == 1
    assert trains[0]["stop_id"] == "26733" and trains[0]["next_time"] is None
    assert arrivals == {}


def test_trip_with_only_past_stops_dropped():
    feed = pb.FeedMessage()
    _path_entity(feed, "DONE", direction_id=1, stops=[("26733", NOW - 600)])
    trains, arrivals, _ = _decode(feed)
    assert trains == [] and arrivals == {}


def test_just_passed_grace_boundary():
    # Same now - 60 grace as the other systems: now-60 is kept, now-61 dropped.
    feed = pb.FeedMessage()
    _path_entity(feed, "KEPT", direction_id=1, stops=[("26733", NOW - 60)])
    _path_entity(feed, "PAST", direction_id=1, stops=[("26734", NOW - 61)])
    trains, arrivals, _ = _decode(feed)
    assert [t["trip_id"] for t in trains] == ["KEPT"]
    assert set(arrivals) == {"26733"}


def test_canceled_trip_dropped_from_both_outputs():
    feed = pb.FeedMessage()
    _path_entity(feed, "T1", direction_id=1, stops=[("26733", NOW + 60)], canceled=True)
    trains, arrivals, _ = _decode(feed)
    assert trains == [] and arrivals == {}


def test_skipped_and_no_data_stops_ignored():
    feed = pb.FeedMessage()
    _path_entity(
        feed,
        "T1",
        direction_id=0,
        stops=[
            ("26733", NOW + 60, _SKIPPED),
            ("26734", NOW + 90, _NO_DATA),
            ("26727", NOW + 120),
        ],
    )
    trains, arrivals, _ = _decode(feed)
    assert len(trains) == 1 and trains[0]["stop_id"] == "26727"
    assert set(arrivals) == {"26727"}


def test_empty_feed_decodes_to_empty_output():
    feed = pb.FeedMessage()
    trains, arrivals, feed_ts = _decode(feed)
    assert trains == [] and arrivals == {}
    assert feed_ts == 1782000000.0  # the header still reads


def test_header_timestamp_none_when_omitted():
    feed = pb.FeedMessage()
    _, _, feed_ts = _decode(feed, header_ts=None)
    assert feed_ts is None


# ---------------- arrivals bucketing ----------------


def test_arrivals_bucketed_by_direction_with_trains_residual():
    feed = pb.FeedMessage()
    _path_entity(feed, "NJ", direction_id=0, stops=[("26727", NOW + 120)])
    _path_entity(feed, "NY", direction_id=1, stops=[("26727", NOW + 180)])
    _path_entity(feed, "NODIR", stops=[("26727", NOW + 240)])
    _, arrivals, _ = _decode(feed)
    buckets = {k: [a["trip_id"] for a in v] for k, v in arrivals["26727"].items()}
    assert buckets == {
        "To New Jersey": ["NJ"],
        "To New York": ["NY"],
        "Trains": ["NODIR"],
    }


def test_arrival_rows_carry_route_trip_and_absolute_time():
    feed = pb.FeedMessage()
    _path_entity(feed, "1a2b-uuid", route_id="859", direction_id=1, stops=[("26727", NOW + 90)])
    _, arrivals, _ = _decode(feed)
    row = arrivals["26727"]["To New York"][0]
    # trip_id is carried for shape parity with the railroad arrivals only:
    # bridge ids are unstable and display-poor, never keyed on or shown.
    assert row == {"route_id": "859", "trip_id": "1a2b-uuid", "arrival": NOW + 90}


def test_arrivals_sorted_and_capped_per_bucket():
    feed = pb.FeedMessage()
    for i, dt in enumerate([300, 60, 500, 120, 240, 30, 420, 180]):
        _path_entity(feed, f"T{i}", direction_id=1, stops=[("26734", NOW + dt)])
    _, arrivals, _ = _decode(feed)
    ny = arrivals["26734"]["To New York"]
    assert len(ny) == feeds.ARRIVALS_PER_DIRECTION  # capped at 6
    times = [a["arrival"] for a in ny]
    assert times == sorted(times)
    assert times[0] == NOW + 30 and times[-1] == NOW + 300  # kept the six soonest


# ---------------- churn + duplicate generations (synthetic) ----------------


def _generation(trip_ids, header_ts):
    """A bridge generation: same service picture (route 862 to NY at Newark,
    route 859 to NJ at Journal Square) under the given trip ids."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = header_ts
    _path_entity(feed, trip_ids[0], route_id="862", direction_id=1, stops=[("26733", NOW + 120)])
    _path_entity(feed, trip_ids[1], route_id="859", direction_id=0, stops=[("26727", NOW + 240)])
    return feed


def test_churn_pair_decodes_independently_with_equivalent_counts():
    # The upstream refresh churns 100% of trip ids, including trains whose
    # payloads are unchanged. Each generation must decode on its own, and the
    # service picture (trains per route + direction) must come out the same
    # despite the disjoint ids.
    gen_a = _generation(["uuid-a1", "uuid-a2"], header_ts=1782000000)
    gen_b = _generation(["uuid-b1", "uuid-b2"], header_ts=1782000015)
    trains_a, _, _ = feeds._decode_path_feed(gen_a.SerializeToString(), PATH_STOPS, NOW)
    trains_b, _, _ = feeds._decode_path_feed(gen_b.SerializeToString(), PATH_STOPS, NOW)
    ids_a = {t["trip_id"] for t in trains_a}
    ids_b = {t["trip_id"] for t in trains_b}
    assert ids_a and ids_b and not (ids_a & ids_b)  # fully disjoint ids

    def by_route_dir(trains):
        counts: dict[tuple, int] = {}
        for t in trains:
            key = (t["route_id"], t["direction"])
            counts[key] = counts.get(key, 0) + 1
        return counts

    assert by_route_dir(trains_a) == by_route_dir(trains_b)


def test_duplicate_pair_identical_output_and_no_error():
    # The bridge re-serves an identical generation when the upstream has not
    # refreshed, with only the header timestamp (its write time) advancing.
    # Decoding both is not an error, yields identical trains/arrivals, and no
    # staleness state exists to be derived from the sameness (there is no
    # content-comparison anywhere in the decode path).
    dup_a = _generation(["uuid-1", "uuid-2"], header_ts=1782000000)
    dup_b = _generation(["uuid-1", "uuid-2"], header_ts=1782000015)
    trains_a, arrivals_a, ts_a = feeds._decode_path_feed(dup_a.SerializeToString(), PATH_STOPS, NOW)
    trains_b, arrivals_b, ts_b = feeds._decode_path_feed(dup_b.SerializeToString(), PATH_STOPS, NOW)
    assert trains_a == trains_b
    assert arrivals_a == arrivals_b
    assert (ts_a, ts_b) == (1782000000.0, 1782000015.0)  # only the write time moved


# ---------------- fetch_path_trains: live path (fake client) ----------------


class _FakeResp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _FakePathClient:
    def __init__(self, content, error=None):
        self._content = content
        self._error = error
        self.requests: list[tuple[str, dict]] = []

    async def get(self, url, headers=None):
        self.requests.append((url, headers or {}))
        if self._error is not None:
            raise self._error
        return _FakeResp(self._content)


@pytest.mark.anyio
async def test_fetch_sends_descriptive_user_agent_to_the_bridge():
    feed = _generation(["u1", "u2"], header_ts=1782000000)
    client = _FakePathClient(feed.SerializeToString())
    trains, arrivals, feed_ts = await feeds.fetch_path_trains(client, PATH_STOPS)
    assert len(client.requests) == 1
    url, headers = client.requests[0]
    assert url == feeds.PATH_RT_URL
    # The bridge is a community service: the UA must identify this app so the
    # maintainer can see who is polling.
    assert headers.get("User-Agent") == feeds.PATH_USER_AGENT
    assert "nyc-transit-live" in feeds.PATH_USER_AGENT
    assert feed_ts == 1782000000.0
    # Times in the fixture are relative to NOW, far in the past against
    # time.time(): the fetch decodes but drops the past-only trips, proving
    # the live path wires the real clock through.
    assert trains == [] and arrivals == {}


@pytest.mark.anyio
async def test_fetch_propagates_http_error_for_the_caller_to_record():
    client = _FakePathClient(b"", error=httpx.ConnectError("bridge down"))
    with pytest.raises(httpx.HTTPError):
        await feeds.fetch_path_trains(client, PATH_STOPS)


@pytest.mark.anyio
async def test_fetch_propagates_decode_error_for_the_caller_to_record():
    client = _FakePathClient(b"\x0a\xff")  # truncated length-delimited field
    with pytest.raises(DecodeError):
        await feeds.fetch_path_trains(client, PATH_STOPS)


# ---------------- goldens over the captured bridge fixtures ----------------
#
# path_rt_gen_a/b.pb (a churn pair), path_rt_dup_a/b.pb (a duplicate pair),
# path_stops.json, and path_rt_gen_a_expected.json are captured by
# backend/scripts/gen_path_rt_fixture.py (capture rules documented there) and
# verified manually before committing. They skip until captured, which needs
# egress to the bridge host.

golden = pytest.mark.skipif(
    not (FIXTURES / "path_rt_gen_a.pb").exists(),
    reason="PATH realtime fixtures not captured; run backend/scripts/gen_path_rt_fixture.py",
)


def _golden_stops():
    return json.loads((FIXTURES / "path_stops.json").read_text())


@golden
def test_golden_gen_a_decodes_to_expected_output():
    raw = (FIXTURES / "path_rt_gen_a.pb").read_bytes()
    expected = json.loads((FIXTURES / "path_rt_gen_a_expected.json").read_text())
    trains, arrivals, feed_ts = feeds._decode_path_feed(raw, _golden_stops(), expected["now"])
    assert trains == expected["trains"]
    assert arrivals == expected["arrivals"]
    assert feed_ts == expected["now"]  # the capture froze `now` to the header


@golden
def test_golden_output_is_nontrivial():
    # Guard the guard: an empty fixture would make the equality test vacuous.
    expected = json.loads((FIXTURES / "path_rt_gen_a_expected.json").read_text())
    assert len(expected["trains"]) > 5
    assert len(expected["arrivals"]) > 3


@golden
def test_golden_every_train_is_well_formed():
    stops = _golden_stops()
    expected = json.loads((FIXTURES / "path_rt_gen_a_expected.json").read_text())
    coords = {(s["lat"], s["lon"]) for s in stops.values()}
    for t in expected["trains"]:
        assert t["stop_id"] in stops  # placed AT a parent station
        assert (t["latitude"], t["longitude"]) in coords
        assert t["direction"] in ("To New York", "To New Jersey", None)
        # 13b invariant: no cross-poll identity, so prev is null on EVERY train.
        assert (t["prev_lat"], t["prev_lon"], t["prev_time"]) == (None, None, None)


@golden
def test_golden_churn_pair_disjoint_ids_equivalent_picture():
    stops = _golden_stops()
    raws = [(FIXTURES / f"path_rt_gen_{g}.pb").read_bytes() for g in ("a", "b")]
    header_now = []
    decoded = []
    for raw in raws:
        feed = pb.FeedMessage()
        feed.ParseFromString(raw)
        now = float(feed.header.timestamp)
        header_now.append(now)
        trains, _, _ = feeds._decode_path_feed(raw, stops, now)
        decoded.append(trains)
    ids_a = {t["trip_id"] for t in decoded[0]}
    ids_b = {t["trip_id"] for t in decoded[1]}
    # Near-zero overlap is the capture criterion; assert it held.
    assert ids_a and ids_b
    assert len(ids_a & ids_b) / min(len(ids_a), len(ids_b)) <= 0.05
    # The service picture survives the id churn: per-(route, direction) train
    # counts match within 1 (the generations are seconds apart, so a single
    # train legitimately entering/leaving service is tolerated; wholesale
    # divergence is not).
    keys = set()
    counts = []
    for trains in decoded:
        c: dict[tuple, int] = {}
        for t in trains:
            key = (t["route_id"], t["direction"])
            c[key] = c.get(key, 0) + 1
        counts.append(c)
        keys |= set(c)
    for key in keys:
        assert abs(counts[0].get(key, 0) - counts[1].get(key, 0)) <= 1


@golden
def test_golden_duplicate_pair_identical_content_differing_write_time():
    stops = _golden_stops()
    raw_a = (FIXTURES / "path_rt_dup_a.pb").read_bytes()
    raw_b = (FIXTURES / "path_rt_dup_b.pb").read_bytes()
    # Decode both against the SAME frozen now so equality is meaningful.
    feed = pb.FeedMessage()
    feed.ParseFromString(raw_a)
    now = float(feed.header.timestamp)
    trains_a, arrivals_a, ts_a = feeds._decode_path_feed(raw_a, stops, now)
    trains_b, arrivals_b, ts_b = feeds._decode_path_feed(raw_b, stops, now)
    assert trains_a == trains_b and arrivals_a == arrivals_b
    assert ts_a != ts_b  # only the bridge's write time differs
