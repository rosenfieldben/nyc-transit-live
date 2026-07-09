"""Golden + unit tests for the PATH bridge-feed decode: placement + arrivals.

Two layers, matching test_feeds_railroad.py:
  - Synthetic feeds exercise every decode rule (single next-arrival stop,
    direction bucketing, unresolvable-stop skips, the churn and duplicate
    generation behaviors the bridge exhibits, grace boundaries).
  - Goldens lock the decode against REAL captured payloads (path_rt_*.pb,
    written by backend/scripts/gen_path_rt_fixture.py with the capture rules
    documented there) plus the committed parent-stops snapshot
    (path_stops.json). They skip loudly until the fixtures are captured,
    which needs egress to the bridge host; in CI a missing fixture fails
    instead.

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
from conftest import golden_fixture_guard

FIXTURES = Path(__file__).parent / "fixtures"

NOW = 1000.0

# Real PATH parent-station ids and coordinates (the 13a stops table shape).
PATH_STOPS = {
    "26733": {"id": "26733", "name": "Newark", "lat": 40.73454, "lon": -74.16375},
    "26734": {"id": "26734", "name": "World Trade Center", "lat": 40.71271, "lon": -74.01193},
    "26727": {"id": "26727", "name": "Exchange Place", "lat": 40.71676, "lon": -74.03238},
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


def _decode_full(feed, stops=PATH_STOPS, now=NOW, header_ts=1782000000):
    """The decoder's full (trains, arrivals, feed_timestamp, unresolved) tuple."""
    feed.header.gtfs_realtime_version = "2.0"
    if header_ts is not None:
        feed.header.timestamp = header_ts
    return feeds._decode_path_feed(feed.SerializeToString(), stops, now)


def _decode(feed, stops=PATH_STOPS, now=NOW, header_ts=1782000000):
    """(trains, arrivals, feed_timestamp): the outputs most tests read. The
    unresolved counter has its own dedicated tests via _decode_full."""
    return _decode_full(feed, stops, now, header_ts)[:3]


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


def test_unresolved_counts_only_unknown_stations():
    # The returned counter names a static-vs-bridge id mismatch, so it must
    # count ONLY entities whose stop ids resolve to no known station, NOT a
    # known station that happened to be SKIPPED/NO_DATA (a normal suspension).
    # Surfacing (transition-only warning + path_feed_health) is the poll
    # loop's job and is tested in test_api.py.
    unknown = pb.FeedMessage()
    _path_entity(unknown, "GHOST", direction_id=1, stops=[("99999", NOW + 60)])
    _, _, _, unresolved = _decode_full(unknown)
    assert unresolved == 1

    skipped = pb.FeedMessage()
    _path_entity(skipped, "SUSP", direction_id=1, stops=[("26733", NOW + 60, _SKIPPED)])
    trains, arrivals, _, unresolved = _decode_full(skipped)
    assert trains == [] and arrivals == {}  # still dropped from both outputs
    assert unresolved == 0  # resolvable-but-skipped is NOT an id mismatch


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
    # Distinct route ids identify the rows: arrival rows deliberately carry no
    # trip id (see test_arrival_rows_carry_route_and_absolute_time below).
    feed = pb.FeedMessage()
    _path_entity(feed, "NJ", route_id="861", direction_id=0, stops=[("26727", NOW + 120)])
    _path_entity(feed, "NY", route_id="862", direction_id=1, stops=[("26727", NOW + 180)])
    _path_entity(feed, "NODIR", route_id="859", stops=[("26727", NOW + 240)])
    _, arrivals, _ = _decode(feed)
    buckets = {k: [a["route_id"] for a in v] for k, v in arrivals["26727"].items()}
    assert buckets == {
        "To New Jersey": ["861"],
        "To New York": ["862"],
        "Trains": ["859"],
    }


def test_arrival_rows_carry_route_and_absolute_time_and_never_the_bridge_hash():
    feed = pb.FeedMessage()
    _path_entity(feed, "1a2b-uuid", route_id="859", direction_id=1, stops=[("26727", NOW + 90)])
    _, arrivals, _ = _decode(feed)
    row = arrivals["26727"]["To New York"][0]
    # {route_id, arrival} ONLY: the bridge hash is unstable and display-poor,
    # and since the 13d cleanup it appears in no served payload at all (the
    # trains side carries the matcher's synthetic id instead). The exact-dict
    # equality is the pin: a reintroduced hash fails here.
    assert row == {"route_id": "859", "arrival": NOW + 90}


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
    route 859 to NJ at Exchange Place) under the given trip ids."""
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
    trains_a, _, _, _ = feeds._decode_path_feed(gen_a.SerializeToString(), PATH_STOPS, NOW)
    trains_b, _, _, _ = feeds._decode_path_feed(gen_b.SerializeToString(), PATH_STOPS, NOW)
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
    trains_a, arrivals_a, ts_a, _ = feeds._decode_path_feed(
        dup_a.SerializeToString(), PATH_STOPS, NOW
    )
    trains_b, arrivals_b, ts_b, _ = feeds._decode_path_feed(
        dup_b.SerializeToString(), PATH_STOPS, NOW
    )
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
    trains, arrivals, feed_ts, unresolved = await feeds.fetch_path_trains(client, PATH_STOPS)
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
    # the live path wires the real clock through. Both stations resolve, so
    # the unresolved count rides through as zero.
    assert trains == [] and arrivals == {}
    assert unresolved == 0


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
# egress to the bridge host. In CI a missing fixture fails instead; see
# conftest.golden_fixture_guard.

golden = golden_fixture_guard(
    FIXTURES / "path_rt_gen_a.pb", "backend/scripts/gen_path_rt_fixture.py"
)


def _golden_stops():
    return json.loads((FIXTURES / "path_stops.json").read_text())


@golden
def test_golden_gen_a_decodes_to_expected_output():
    raw = (FIXTURES / "path_rt_gen_a.pb").read_bytes()
    expected = json.loads((FIXTURES / "path_rt_gen_a_expected.json").read_text())
    trains, arrivals, feed_ts, unresolved = feeds._decode_path_feed(
        raw, _golden_stops(), expected["now"]
    )
    assert trains == expected["trains"]
    assert arrivals == expected["arrivals"]
    assert feed_ts == expected["now"]  # the capture froze `now` to the header
    # The stops snapshot is captured in the same session as the feed, so every
    # bridge station must resolve; the capture script refuses to write a
    # fixture where they disagree.
    assert unresolved == 0


@golden
def test_golden_output_is_nontrivial():
    # Guard the guard: an empty fixture would make the equality test vacuous.
    # The floor is only "non-empty", NOT a service-volume threshold: the capture
    # script gates on a churn + duplicate pair (an upstream refresh), which
    # occurs independent of how many trains are running, so a legitimate
    # off-peak capture can be small. A higher floor would false-fail a valid,
    # manually-verified overnight fixture.
    expected = json.loads((FIXTURES / "path_rt_gen_a_expected.json").read_text())
    assert len(expected["trains"]) >= 1
    assert len(expected["arrivals"]) >= 1


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
        trains, _, _, _ = feeds._decode_path_feed(raw, stops, now)
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
    trains_a, arrivals_a, ts_a, _ = feeds._decode_path_feed(raw_a, stops, now)
    trains_b, arrivals_b, ts_b, _ = feeds._decode_path_feed(raw_b, stops, now)
    assert trains_a == trains_b and arrivals_a == arrivals_b
    assert ts_a != ts_b  # only the bridge's write time differs


# ---------------- match_path_identities (13d synthetic identity) ----------------
#
# The matcher is pure and clock-free, so these tests drive whole generations
# through it directly. Trains below are decode-shaped dicts (trip_id present,
# prev_* null), exactly what _decode_path_feed emits and the matcher consumes.

# Coordinates arbitrary but distinct per station, so anchor assertions can
# tell stations apart. Order: WTC > EXP > GRV (the 862 New Jersey-bound run).
_STATIONS = {
    "26734": (40.71271, -74.01193),  # World Trade Center
    "26727": (40.71676, -74.03238),  # Exchange Place
    "26728": (40.71966, -74.04245),  # Grove Street
}
ORDER = {("862", "0"): ["26734", "26727", "26728"]}
TOL = feeds.PATH_MATCH_TOLERANCE_S


def _mtrain(stop, at, route="862", direction="To New Jersey", trip="raw"):
    lat, lon = _STATIONS[stop]
    return {
        "trip_id": trip,  # the unstable bridge hash; the matcher must never rely on it
        "route_id": route,
        "latitude": lat,
        "longitude": lon,
        "stop_id": stop,
        "stop_name": stop,
        "direction": direction,
        "prev_lat": None,
        "prev_lon": None,
        "prev_time": None,
        "next_time": at,
    }


def _match(state, trains, order=ORDER):
    return feeds.match_path_identities(state, trains, order)


def _state():
    return feeds.new_path_identity_state("t")


def test_matcher_serves_stable_id_and_never_the_bridge_hash():
    served, state = _match(_state(), [_mtrain("26727", 1000.0, trip="hash-a")])
    assert [set(t) for t in served] == [
        {
            "id",
            "route_id",
            "latitude",
            "longitude",
            "stop_id",
            "stop_name",
            "direction",
            "prev_lat",
            "prev_lon",
            "prev_time",
            "next_time",
        }
    ]
    assert served[0]["id"] == "t-1"  # epoch-prefixed mint, not derived from the hash
    assert "hash-a" not in str(served)


def test_matcher_same_stop_carry_within_tolerance():
    served1, state = _match(_state(), [_mtrain("26727", 1000.0, trip="gen1")])
    # Upstream refresh: new hash, prediction drifted 8s (the probe's worst case).
    served2, state = _match(state, [_mtrain("26727", 1008.0, trip="gen2")])
    assert served2[0]["id"] == served1[0]["id"]
    assert served2[0]["next_time"] == 1008.0  # fresher prediction served
    assert served2[0]["prev_lat"] is None  # no advance happened: anchors stay null
    assert state["seq"] == 1  # nothing new was minted


def test_matcher_same_stop_beyond_tolerance_resets():
    served1, state = _match(_state(), [_mtrain("26727", 1000.0)])
    served2, state = _match(state, [_mtrain("26727", 1000.0 + TOL + 1)])
    assert served2[0]["id"] != served1[0]["id"]


def test_matcher_ambiguous_candidates_reset_instead_of_guessing():
    # Two prior trains inside one window at the same slot: guessing could glide
    # a marker along the wrong journey, so both claims must reset.
    served1, state = _match(
        _state(),
        [_mtrain("26727", 1000.0, trip="x"), _mtrain("26727", 1030.0, trip="y")],
    )
    served2, state = _match(state, [_mtrain("26727", 1015.0, trip="z")])
    assert served2[0]["id"] not in {t["id"] for t in served1}


def test_matcher_one_candidate_claimed_by_two_new_trains_resets_both():
    served1, state = _match(_state(), [_mtrain("26727", 1000.0)])
    served2, state = _match(
        state,
        [_mtrain("26727", 990.0, trip="x"), _mtrain("26727", 1010.0, trip="y")],
    )
    prior = served1[0]["id"]
    assert all(t["id"] != prior for t in served2)


def test_matcher_duplicate_generation_is_a_strict_noop():
    # The bridge re-serves identical content between upstream refreshes (only
    # its write time advances). Every identity must carry with ZERO anchor
    # changes and zero mints, or a stalled upstream would slowly churn the map.
    gen = [_mtrain("26727", 1000.0, trip="a"), _mtrain("26728", 1200.0, trip="b")]
    served1, state = _match(_state(), gen)
    seq_before = state["seq"]
    served2, state = _match(state, [dict(t) for t in gen])
    assert [t["id"] for t in served2] == [t["id"] for t in served1]
    assert [(t["prev_lat"], t["prev_lon"], t["prev_time"]) for t in served2] == [
        (t["prev_lat"], t["prev_lon"], t["prev_time"]) for t in served1
    ]
    assert state["seq"] == seq_before


def test_matcher_untimed_placements_compare_equal_only_to_each_other():
    # The no-times placement fallback has nothing to window on: identical
    # untimed re-serves carry (delta zero), a timed vs untimed pair never does.
    served1, state = _match(_state(), [_mtrain("26727", None)])
    served2, state = _match(state, [_mtrain("26727", None)])
    assert served2[0]["id"] == served1[0]["id"]
    served3, state = _match(state, [_mtrain("26727", 1000.0)])
    assert served3[0]["id"] != served2[0]["id"]


def test_matcher_clean_advance_carries_identity_and_sets_anchors():
    # The train finished EXP and now shows at GRV: identity carries via the
    # station order, and the prev anchor becomes EXP (position + its predicted
    # arrival there), exactly what trainLatLng needs to glide EXP -> GRV.
    served1, state = _match(_state(), [_mtrain("26727", 1000.0, trip="gen1")])
    served2, state = _match(state, [_mtrain("26728", 1180.0, trip="gen2")])
    assert served2[0]["id"] == served1[0]["id"]
    exp_lat, exp_lon = _STATIONS["26727"]
    assert (served2[0]["prev_lat"], served2[0]["prev_lon"]) == (exp_lat, exp_lon)
    assert served2[0]["prev_time"] == 1000.0
    assert served2[0]["next_time"] == 1180.0


def test_matcher_advance_needs_the_immediate_predecessor():
    # WTC is two stations behind GRV: a skip is not an advance the order can
    # vouch for, so identity resets rather than fabricating a two-hop glide.
    served1, state = _match(_state(), [_mtrain("26734", 1000.0)])
    served2, state = _match(state, [_mtrain("26728", 1400.0)])
    assert served2[0]["id"] != served1[0]["id"]
    assert served2[0]["prev_lat"] is None


def test_matcher_advance_requires_direction_and_order():
    # No direction: the order cannot be looked up, so no advance (fresh id).
    served1, state = _match(_state(), [_mtrain("26727", 1000.0, direction=None)])
    served2, state = _match(state, [_mtrain("26728", 1180.0, direction=None)])
    assert served2[0]["id"] != served1[0]["id"]
    # Direction present but no station order loaded (pre-13d cache): same reset.
    served1, state = _match(_state(), [_mtrain("26727", 1000.0)], order={})
    served2, state = _match(state, [_mtrain("26728", 1180.0)], order={})
    assert served2[0]["id"] != served1[0]["id"]


def test_matcher_lockstep_advances_do_not_swap_identities():
    # The review-flagged case: two trains advancing simultaneously, one WTC ->
    # EXP and one EXP -> GRV. Each new train sees exactly one vanished train
    # at ITS OWN predecessor, so both carry and the anchors prove no swap.
    gen1 = [_mtrain("26727", 1000.0, trip="lead"), _mtrain("26734", 1010.0, trip="chase")]
    served1, state = _match(_state(), gen1)
    lead_id = served1[0]["id"]
    chase_id = served1[1]["id"]
    gen2 = [_mtrain("26728", 1300.0, trip="n1"), _mtrain("26727", 1310.0, trip="n2")]
    served2, state = _match(state, gen2)
    by_stop = {t["stop_id"]: t for t in served2}
    assert by_stop["26728"]["id"] == lead_id
    assert by_stop["26727"]["id"] == chase_id
    assert (by_stop["26728"]["prev_lat"], by_stop["26728"]["prev_lon"]) == _STATIONS["26727"]
    assert (by_stop["26727"]["prev_lat"], by_stop["26727"]["prev_lon"]) == _STATIONS["26734"]


def test_matcher_two_vanished_candidates_at_the_predecessor_reset():
    # Two prior trains at EXP (a real double-berth or a data wobble) both
    # vanish while one train appears at GRV: the advance cannot know which one
    # moved, so it must reset rather than guess (the only heuristic branch
    # stays unique-or-nothing).
    gen1 = [_mtrain("26727", 1000.0, trip="x"), _mtrain("26727", 2000.0, trip="y")]
    served1, state = _match(_state(), gen1)
    served2, state = _match(state, [_mtrain("26728", 1300.0)])
    assert served2[0]["id"] not in {t["id"] for t in served1}
    assert served2[0]["prev_lat"] is None


def test_matcher_advance_only_from_the_immediately_previous_generation():
    # A train absent for a full generation is a terminal arrival or a data
    # gap, not an advance in progress (mid-system trains reappear at their
    # next stop in the very next generation), so it must not anchor one.
    served1, state = _match(_state(), [_mtrain("26727", 1000.0)])
    served_gap, state = _match(state, [])  # the train vanishes for a generation
    served2, state = _match(state, [_mtrain("26728", 1300.0)])
    assert served2[0]["id"] != served1[0]["id"]
    assert served2[0]["prev_lat"] is None


def test_matcher_same_stop_rematch_survives_a_short_absence_then_expires():
    # A single-poll bridge blip must not reset identity: the absent identity
    # stays matchable for PATH_IDENTITY_EXPIRY_GENERATIONS - 1 generations.
    served1, state = _match(_state(), [_mtrain("26727", 1000.0)])
    sid = served1[0]["id"]
    served2, state = _match(state, [])  # absent once
    served3, state = _match(state, [_mtrain("26727", 1000.0)])
    assert served3[0]["id"] == sid

    # Absent for the full expiry run (PATH_IDENTITY_EXPIRY_GENERATIONS
    # consecutive matched generations): the identity is gone, and a fresh id
    # mints even at an identical prediction.
    for _ in range(feeds.PATH_IDENTITY_EXPIRY_GENERATIONS):
        _served, state = _match(state, [])
    served_after, state = _match(state, [_mtrain("26727", 1000.0)])
    assert served_after[0]["id"] != sid


def test_matcher_anchors_persist_across_same_stop_polls_after_an_advance():
    # Mid-glide the bridge re-serves the same generation: the train stays at
    # GRV with its EXP anchor intact, so the frontend keeps interpolating the
    # same segment instead of snapping back to the station.
    _s1, state = _match(_state(), [_mtrain("26727", 1000.0)])
    served2, state = _match(state, [_mtrain("26728", 1300.0)])
    served3, state = _match(state, [_mtrain("26728", 1300.0)])
    assert served3[0]["id"] == served2[0]["id"]
    assert (served3[0]["prev_lat"], served3[0]["prev_lon"]) == _STATIONS["26727"]
    assert served3[0]["prev_time"] == 1000.0


def test_matcher_pins_the_steal_boundary_inside_the_tolerance_window():
    # DOCUMENTED EXPOSURE, deliberately pinned: if a DISTINCT train arrives at
    # the same (route, direction, stop) within PATH_MATCH_TOLERANCE_S of a
    # departed train's stored prediction, branch 1 hands it the departed
    # train's identity and the truly-advanced train mints fresh. This is the
    # trade the tolerance rationale bounds: real same-slot headways are 406s+
    # off-peak and 240s at rush (the golden headway floors), so a follower
    # inside the 60s window cannot occur at today's service levels. If the
    # tolerance is ever raised, THIS is the boundary that widens; the goldens
    # fail first only when the schedule compresses, so both guards matter.
    served1, state = _match(_state(), [_mtrain("26727", 1000.0, trip="t1")])
    t1 = served1[0]["id"]
    # T1 advanced EXP -> GRV while a follower appears at EXP inside the window.
    served2, state = _match(
        state,
        [_mtrain("26728", 1300.0, trip="adv"), _mtrain("26727", 1000.0 + TOL - 1, trip="new")],
    )
    by_stop = {t["stop_id"]: t for t in served2}
    assert by_stop["26727"]["id"] == t1  # the follower stole the identity
    assert by_stop["26728"]["id"] != t1  # the truly-advanced train minted fresh

    # The same shape one REAL peak headway (240s) behind instead: no steal.
    # Branch 1 rejects the follower, branch 2 carries the advanced train with
    # its EXP anchor, and the follower mints fresh. This is where actual PATH
    # service lives, which is why the exposure above is acceptable.
    served1, state = _match(_state(), [_mtrain("26727", 1000.0)])
    t1 = served1[0]["id"]
    served2, state = _match(state, [_mtrain("26728", 1300.0), _mtrain("26727", 1240.0)])
    by_stop = {t["stop_id"]: t for t in served2}
    assert by_stop["26728"]["id"] == t1
    assert (by_stop["26728"]["prev_lat"], by_stop["26728"]["prev_lon"]) == _STATIONS["26727"]
    assert by_stop["26727"]["id"] != t1


def test_matcher_pins_the_terminate_plus_appear_false_advance():
    # DOCUMENTED RESIDUAL of branch 2, deliberately pinned: these inputs are
    # BY CONSTRUCTION indistinguishable from the clean advance two tests up,
    # but here the physical story is different: the EXP train terminated
    # (short-turned) while an unrelated train appeared at GRV in the same
    # generation. The matcher cannot tell "moved" from "died while another was
    # born at the successor", so the appearing train inherits the terminated
    # identity and a fabricated EXP -> GRV glide anchor. Plausible around
    # short-turn territory (861/1024 end at Journal Square while through
    # trains continue); the cost is one cosmetic wrong glide segment that the
    # next advance replaces. Pinned so a future fix (e.g. terminal-awareness)
    # changes this test knowingly instead of silently.
    served1, state = _match(_state(), [_mtrain("26727", 1000.0, trip="dies")])
    ended = served1[0]["id"]
    served2, state = _match(state, [_mtrain("26728", 1500.0, trip="born")])
    assert served2[0]["id"] == ended  # the misattribution, accepted by design
    assert (served2[0]["prev_lat"], served2[0]["prev_lon"]) == _STATIONS["26727"]
    assert served2[0]["prev_time"] == 1000.0


def test_matcher_tolerance_pins_the_probe_margins():
    # Probe, 2026-07-07, 40 polls / 10 upstream generations / 238 transitions:
    # same-stop nearest-arrival matching hit 98.7% unique, 0.0% ambiguous;
    # worst prediction drift 8s; minimum same-(route, direction, stop) headway
    # 2256s off-peak, roughly 240s at peak. The tolerance must dominate the
    # drift by a wide margin while staying a small fraction of the tightest
    # headway, or two REAL trains could fall inside one window and a future
    # schedule compression must fail here loudly rather than silently merge.
    assert feeds.PATH_MATCH_TOLERANCE_S == 60
    assert feeds.PATH_MATCH_TOLERANCE_S >= 8 * 5  # 7.5x the worst observed drift
    assert feeds.PATH_MATCH_TOLERANCE_S * 3 <= 240  # still 4x under peak headway
    assert feeds.PATH_MATCH_TOLERANCE_S * 30 <= 2256  # 37x under the off-peak floor


# ---------------- matcher goldens over the captured fixtures ----------------
#
# These run the matcher over the REAL committed pairs. Station order is
# deliberately {} here: it only enriches advances with anchors (pinned by the
# synthetic tests above); what the real captures must pin is identity carry
# and the tolerance margin against genuine bridge behavior.


def _golden_decode(name):
    raw = (FIXTURES / name).read_bytes()
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    now = float(feed.header.timestamp)
    trains, arrivals, _ts, _unresolved = feeds._decode_path_feed(raw, _golden_stops(), now)
    return trains, arrivals


def _golden_match_pair(name_a, name_b):
    trains_a, _ = _golden_decode(name_a)
    trains_b, _ = _golden_decode(name_b)
    state = feeds.new_path_identity_state("g")
    served_a, state = feeds.match_path_identities(state, trains_a, {})
    seq_after_a = state["seq"]
    served_b, state = feeds.match_path_identities(state, trains_b, {})
    return served_a, served_b, state["seq"] - seq_after_a


def _golden_min_same_slot_headway(names):
    """Smallest gap between distinct predicted arrivals sharing a
    (route, direction bucket, stop) WITHIN one payload: the real quantity the
    matching window must stay under (pooling across payloads would instead
    measure the same train's prediction drift)."""
    gaps = []
    for name in names:
        _trains, arrivals = _golden_decode(name)
        times: dict[tuple, list[float]] = {}
        for stop_id, buckets in arrivals.items():
            for bucket, rows in buckets.items():
                for row in rows:
                    times.setdefault((row["route_id"], bucket, stop_id), []).append(row["arrival"])
        for arrs in times.values():
            arrs.sort()
            gaps.extend(b - a for a, b in zip(arrs, arrs[1:]) if b > a)
    return min(gaps) if gaps else None


@golden
def test_golden_churn_pair_carries_nearly_every_identity():
    # The pair whose trip ids churned 100%: the matcher must reconstruct the
    # picture from stable fields alone. Measured on the committed capture:
    # 52 of 53 identities carried (0.981); the remainder are trains that
    # advanced or appeared between generations, which mint fresh ids here
    # because the goldens run without a station order.
    served_a, served_b, minted_b = _golden_match_pair("path_rt_gen_a.pb", "path_rt_gen_b.pb")
    ids_a = {t["id"] for t in served_a}
    ids_b = {t["id"] for t in served_b}
    carry = len(ids_a & ids_b) / min(len(ids_a), len(ids_b))
    assert carry >= 0.95
    assert minted_b <= 3  # only the advance/appear tail mints


@golden
def test_golden_duplicate_pair_carries_every_identity_with_zero_changes():
    # A re-served generation must be a strict no-op: total carry, zero mints,
    # zero anchor changes, or a stalled upstream would slowly churn the map.
    served_a, served_b, minted_b = _golden_match_pair("path_rt_dup_a.pb", "path_rt_dup_b.pb")
    assert {t["id"] for t in served_a} == {t["id"] for t in served_b}
    assert minted_b == 0
    anchors = lambda served: {  # noqa: E731
        t["id"]: (t["prev_lat"], t["prev_lon"], t["prev_time"]) for t in served
    }
    assert anchors(served_a) == anchors(served_b)


@golden
def test_golden_tolerance_sits_well_under_the_real_headway_floor():
    # The committed captures' tightest same-(route, direction, stop) headway
    # is 406s (off-peak), nearly 7x the 60s window: two REAL consecutive
    # trains can never fall inside one match window at this service level.
    # The rush golden below re-pins this against compressed peak headways.
    headway = _golden_min_same_slot_headway(["path_rt_gen_a.pb", "path_rt_gen_b.pb"])
    assert headway is not None
    assert headway > 2 * feeds.PATH_MATCH_TOLERANCE_S


# ---------------- rush-hour goldens (13d) ----------------
#
# Captured by `gen_path_rt_fixture.py rush` DURING a weekday 7-9am or 5-7pm
# America/New_York window, because peak service is where headways compress
# toward the matcher's tolerance and where a future schedule change must fail
# loudly. Gated on its own guard: the pair does not exist until someone runs
# the capture inside such a window with egress to the bridge host.

golden_rush = golden_fixture_guard(
    FIXTURES / "path_rt_rush_a.pb", "backend/scripts/gen_path_rt_fixture.py rush"
)


@golden_rush
def test_golden_rush_pair_carries_identities_at_peak_service():
    served_a, served_b, _minted = _golden_match_pair("path_rt_rush_a.pb", "path_rt_rush_b.pb")
    ids_a = {t["id"] for t in served_a}
    ids_b = {t["id"] for t in served_b}
    assert min(len(ids_a), len(ids_b)) >= 1
    carry = len(ids_a & ids_b) / min(len(ids_a), len(ids_b))
    assert carry >= 0.9


@golden_rush
def test_golden_rush_headways_still_dominate_the_tolerance():
    # THE tolerance rationale, pinned against real peak service: PATH's rush
    # headways (roughly 240s) must stay comfortably above the 60s window. If
    # PATH ever compresses service toward sub-2-minute headways this fails
    # loudly, and PATH_MATCH_TOLERANCE_S must be revisited before trusting
    # same-stop matches again.
    headway = _golden_min_same_slot_headway(["path_rt_rush_a.pb", "path_rt_rush_b.pb"])
    assert headway is not None
    assert headway > 2 * feeds.PATH_MATCH_TOLERANCE_S
