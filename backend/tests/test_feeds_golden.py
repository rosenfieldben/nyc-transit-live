"""Golden test: a real captured subway feed decodes to stable output.

The riskiest logic in the app is subway trip decoding and the start-time
heuristics in feeds._decode_trains / _trip_start_ts — synthetic protobufs
(test_feeds.py) exercise the branches, but only a real feed exercises the
true shape of the data. The fixtures here were captured once from the live
keyless numbered-lines feed (the payload carries no PII), with `now` frozen
to the feed's header timestamp so decoding is fully deterministic.

To regenerate after an INTENTIONAL decode change, from backend/ with the
static GTFS present:

    python - <<'PY'
    import json, httpx
    from pathlib import Path
    from google.transit import gtfs_realtime_pb2 as pb
    import feeds, static_data
    FIX = Path("tests/fixtures")
    raw = httpx.get(feeds.SUBWAY_FEED_URLS["1-7+S"], timeout=30,
                    follow_redirects=True).content
    feed = pb.FeedMessage(); feed.ParseFromString(raw)
    now = float(feed.header.timestamp)
    allstops = static_data._parse_stops()
    ref = {s.stop_id for e in feed.entity if e.HasField("trip_update")
           for s in e.trip_update.stop_time_update if s.stop_id}
    stops = {k: allstops[k] for k in sorted(ref) if k in allstops}
    trains = feeds._decode_trains(raw, stops, "1-7+S", now)
    FIX.joinpath("subway_1_7_s.pb").write_bytes(raw)
    FIX.joinpath("subway_1_7_s_stops.json").write_text(json.dumps(stops, sort_keys=True, indent=0))
    FIX.joinpath("subway_1_7_s_expected.json").write_text(
        json.dumps({"now": now, "feed_key": "1-7+S", "trains": trains}, indent=0))
    PY
"""

import json
from pathlib import Path

import feeds

FIXTURES = Path(__file__).parent / "fixtures"


def _load():
    raw = (FIXTURES / "subway_1_7_s.pb").read_bytes()
    stops = json.loads((FIXTURES / "subway_1_7_s_stops.json").read_text())
    expected = json.loads((FIXTURES / "subway_1_7_s_expected.json").read_text())
    return raw, stops, expected


def test_real_feed_decodes_to_golden_output():
    raw, stops, expected = _load()
    trains = feeds._decode_trains(raw, stops, expected["feed_key"], expected["now"])
    assert trains == expected["trains"]


def test_golden_output_is_nontrivial():
    # Guard the guard: an empty fixture would make the equality test vacuous.
    _, _, expected = _load()
    assert len(expected["trains"]) > 20


def test_every_golden_train_is_well_formed():
    # Real-data invariants the synthetic tests can't assert at scale.
    _, stops, expected = _load()
    for train in expected["trains"]:
        assert train["stop_id"] in stops
        assert train["route_id"]  # numbered-lines feed always carries a route
        assert train["direction"] in ("Northbound", "Southbound", None)
        assert 40.4 < train["latitude"] < 41.1
        assert -74.3 < train["longitude"] < -73.6
