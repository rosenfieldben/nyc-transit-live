#!/usr/bin/env python3
"""Capture PATH realtime golden fixtures (backend/tests/fixtures/path_rt_*).

The PATH bridge feed (jamespfennell/path-train-gtfs-realtime) has two
behaviors no synthetic fixture can prove we handle, so the golden tests need
REAL captured pairs:

  - CHURN: when the upstream PANYNJ API refreshes, trip ids churn 100%,
    including trains whose arrival payloads are byte-identical (probed
    2026-07-05; recorded in path_static's module docstring). The decoder must
    treat two consecutive generations with disjoint ids as two independent,
    equally valid snapshots.
  - DUPLICATES: the bridge regenerates every ~15s but re-serves an identical
    entity set when the upstream has not refreshed, with only the header
    timestamp (the bridge's write time) advancing. Identical consecutive
    content is NORMAL for PATH, never a stuck-feed signal.

This script polls the live feed every POLL_INTERVAL_S for up to DEADLINE_S
and saves, into backend/tests/fixtures/ following the railroad fixture
naming:

  - path_rt_gen_a.pb / path_rt_gen_b.pb: a churn pair, two consecutive
    distinct generations whose trip id overlap is near zero.
  - path_rt_dup_a.pb / path_rt_dup_b.pb: a duplicate pair, two polls with
    identical entity content but differing header timestamps.
  - path_stops.json: the 13a parent stops table (load_path_static()["stops"]),
    mirroring railroad_lirr_stops.json, so the decode goldens are network-free.
  - path_rt_gen_a_expected.json: the decoder's expected output for gen_a
    ({now, trains, arrivals} with `now` frozen to gen_a's header timestamp),
    to be verified MANUALLY against the printed counts and sample rows before
    committing, per house rules.

If the deadline passes without producing a churn pair (the upstream refresh
cadence is minutes-scale off-peak), the script says so and exits nonzero
rather than fabricating one; a missing duplicate pair (upstream refreshing
faster than the bridge regenerates, unlikely) exits nonzero the same way.

RUSH MODE (13d):  python backend/scripts/gen_path_rt_fixture.py rush
Captures ONE churn pair into path_rt_rush_a.pb / path_rt_rush_b.pb, and
refuses to run outside a weekday 7-9am or 5-7pm America/New_York window: the
rush pair exists to pin the identity matcher's tolerance against PATH's
COMPRESSED peak headways (roughly 4 minutes), and an off-peak capture would
assert nothing the existing gen pair does not. The mode prints the pair's
minimum same-(route, direction, stop) headway and the matcher's identity
carry rate for the manual eyeball, and refuses to write a pair whose
stations do not resolve against the committed path_stops.json (the goldens
decode against that snapshot).

Run from the repo root:  python backend/scripts/gen_path_rt_fixture.py
Requires egress to the bridge host (PATH_RT_URL overrides the default).
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))  # import the app modules directly

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import feeds  # noqa: E402
import path_static  # noqa: E402

FIXTURES = REPO_ROOT / "backend" / "tests" / "fixtures"

FEED_URL = os.environ.get("PATH_RT_URL", "https://path.transitdata.nyc/gtfsrt")
USER_AGENT = feeds.PATH_USER_AGENT

POLL_INTERVAL_S = 5
DEADLINE_S = 300

# "Near-zero overlap" for the churn pair: the probe saw 100% id churn, so any
# pair above this tiny tolerance is not a clean churn example and is skipped.
MAX_CHURN_OVERLAP = 0.05


def _ssl_context() -> ssl.SSLContext:
    # Verify against certifi's CA bundle when present (matches the other
    # generators); fall back to the platform default elsewhere.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _poll(ctx: ssl.SSLContext) -> bytes:
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:  # noqa: S310
        return resp.read()


def _parse(raw: bytes) -> tuple[float, list[bytes], set[str]]:
    """(header timestamp, per-entity serialized bytes, trip id set).

    Entity bytes are compared for the duplicate pair: two polls are the same
    GENERATION exactly when their entity lists are byte-identical, regardless
    of the header timestamp (the bridge's write time, which always advances).
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    entities = [e.SerializeToString() for e in feed.entity]
    trip_ids = {
        e.trip_update.trip.trip_id
        for e in feed.entity
        if e.HasField("trip_update") and e.trip_update.trip.trip_id
    }
    return float(feed.header.timestamp), entities, trip_ids


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _min_same_slot_headway(payloads: list[bytes], stops: dict) -> float | None:
    """Smallest gap between consecutive predicted arrivals sharing a
    (route_id, direction bucket, stop) across the given payloads: the quantity
    the matcher's PATH_MATCH_TOLERANCE_S must stay well under, since two
    distinct trains one headway apart at the same stop must never fall inside
    one matching window."""
    times: dict[tuple, list[float]] = {}
    for raw in payloads:
        feed = pb.FeedMessage()
        feed.ParseFromString(raw)
        _trains, arrivals, _ts, _unresolved = feeds._decode_path_feed(
            raw, stops, float(feed.header.timestamp)
        )
        for stop_id, buckets in arrivals.items():
            for bucket, rows in buckets.items():
                for row in rows:
                    times.setdefault((row["route_id"], bucket, stop_id), []).append(row["arrival"])
    gaps = []
    for arrs in times.values():
        arrs.sort()
        gaps.extend(b - a for a, b in zip(arrs, arrs[1:]) if b > a)
    return min(gaps) if gaps else None


def _rush_window_open() -> bool:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/New_York"))
    return now.weekday() < 5 and (7 <= now.hour < 9 or 17 <= now.hour < 19)


def rush_main() -> int:
    """Capture the weekday rush-hour churn pair (path_rt_rush_a/b.pb)."""
    if not _rush_window_open():
        print(
            "REFUSED: rush mode must run during a WEEKDAY 7-9am or 5-7pm "
            "America/New_York window; the pair exists to pin the matcher "
            "tolerance against compressed peak headways, and any other "
            "capture would not be a rush-hour fixture."
        )
        return 1
    stops_path = FIXTURES / "path_stops.json"
    if not stops_path.exists():
        print("REFUSED: commit path_stops.json first (default mode writes it); the rush")
        print("goldens decode against that snapshot, so the pair must resolve against it.")
        return 1
    stops = json.loads(stops_path.read_text())

    ctx = _ssl_context()
    print(f"Polling {FEED_URL} every {POLL_INTERVAL_S}s for up to {DEADLINE_S}s (rush mode) ...")
    started = time.time()
    churn: tuple[bytes, bytes] | None = None
    prev_raw: bytes | None = None
    prev_ids: set[str] = set()
    while time.time() - started < DEADLINE_S and churn is None:
        raw = _poll(ctx)
        _ts, _entities, ids = _parse(raw)
        if prev_raw is not None and ids and prev_ids:
            ratio = _overlap(prev_ids, ids)
            print(f"  [{round(time.time() - started, 1)}s] {len(ids)} trips, overlap {ratio:.0%}")
            if ratio <= MAX_CHURN_OVERLAP:
                churn = (prev_raw, raw)
        prev_raw, prev_ids = raw, ids
        time.sleep(POLL_INTERVAL_S)
    if churn is None:
        print(f"\nFAILED: no churn pair within {DEADLINE_S}s; re-run, do NOT fabricate one.")
        return 1

    for raw in churn:
        feed = pb.FeedMessage()
        feed.ParseFromString(raw)
        _trains, _arr, _ts, unresolved = feeds._decode_path_feed(
            raw, stops, float(feed.header.timestamp)
        )
        if unresolved:
            print(
                f"FAILED: capture references {unresolved} entities whose station ids are "
                "missing from the committed path_stops.json; regenerate the default "
                "fixtures first (static/bridge drift)."
            )
            return 1

    (FIXTURES / "path_rt_rush_a.pb").write_bytes(churn[0])
    (FIXTURES / "path_rt_rush_b.pb").write_bytes(churn[1])
    print("\nwrote path_rt_rush_a.pb / path_rt_rush_b.pb")

    print("\n" + "=" * 72)
    print("MANUAL VERIFICATION (eyeball before committing, per house rules)")
    print("=" * 72)
    headway = _min_same_slot_headway(list(churn), stops)
    print(f"min same-(route, direction, stop) headway across the pair: {headway}s")
    print(f"matcher tolerance: {feeds.PATH_MATCH_TOLERANCE_S}s (must sit WELL under the headway)")

    # Identity carry across the pair, through the real matcher, so the number
    # eyeballed here is the same one the golden pins.
    static = asyncio.run(path_static.load_path_static())
    order = path_static.build_path_station_order(
        static.get("trips") or {},
        static.get("stop_times") or {},
        static.get("child_to_parent") or {},
    )
    decoded = []
    for raw in churn:
        feed = pb.FeedMessage()
        feed.ParseFromString(raw)
        trains, _arr, _ts, _unres = feeds._decode_path_feed(
            raw, stops, float(feed.header.timestamp)
        )
        decoded.append(trains)
    state = feeds.new_path_identity_state("rushcheck")
    served_a, state = feeds.match_path_identities(state, decoded[0], order)
    served_b, state = feeds.match_path_identities(state, decoded[1], order)
    ids_a = {t["id"] for t in served_a}
    ids_b = {t["id"] for t in served_b}
    carried = len(ids_a & ids_b)
    denominator = max(1, min(len(ids_a), len(ids_b)))
    print(
        f"identity carry a -> b: {carried}/{denominator} "
        f"({carried / denominator:.1%}); advances with anchors: "
        f"{sum(1 for t in served_b if t['prev_lat'] is not None)}"
    )
    return 0


def main() -> int:
    ctx = _ssl_context()
    FIXTURES.mkdir(parents=True, exist_ok=True)

    print(f"Polling {FEED_URL} every {POLL_INTERVAL_S}s for up to {DEADLINE_S}s ...")
    started = time.time()
    churn: tuple[bytes, bytes] | None = None
    dup: tuple[bytes, bytes] | None = None
    churn_at = dup_at = None  # seconds since start, for the capture log
    prev_raw: bytes | None = None
    prev_ts, prev_entities, prev_ids = 0.0, [], set()
    polls = 0

    while time.time() - started < DEADLINE_S and (churn is None or dup is None):
        raw = _poll(ctx)
        polls += 1
        ts, entities, ids = _parse(raw)
        if prev_raw is not None:
            if entities == prev_entities:
                # Same generation re-served; a duplicate pair needs the header
                # timestamps to differ so the test can prove only the write
                # time moved.
                if dup is None and ts != prev_ts:
                    dup = (prev_raw, raw)
                    dup_at = round(time.time() - started, 1)
                    print(f"  [{dup_at}s] duplicate pair captured (headers {prev_ts} -> {ts})")
            else:
                ratio = _overlap(prev_ids, ids)
                print(
                    f"  [{round(time.time() - started, 1)}s] new generation: "
                    f"{len(ids)} trips, id overlap with previous {ratio:.0%}"
                )
                if churn is None and ratio <= MAX_CHURN_OVERLAP and ids and prev_ids:
                    churn = (prev_raw, raw)
                    churn_at = round(time.time() - started, 1)
                    print(f"  [{churn_at}s] churn pair captured")
        prev_raw, prev_ts, prev_entities, prev_ids = raw, ts, entities, ids
        time.sleep(POLL_INTERVAL_S)

    missing = [name for name, pair in (("churn", churn), ("duplicate", dup)) if pair is None]
    if missing:
        print(
            f"\nFAILED after {polls} polls: no {' or '.join(missing)} pair observed within "
            f"{DEADLINE_S}s. The upstream may be refreshing off-peak (churn needs an upstream "
            "refresh) or unusually fast (duplicates need a re-served generation). "
            "Re-run; do NOT fabricate a pair."
        )
        return 1

    (FIXTURES / "path_rt_gen_a.pb").write_bytes(churn[0])
    (FIXTURES / "path_rt_gen_b.pb").write_bytes(churn[1])
    (FIXTURES / "path_rt_dup_a.pb").write_bytes(dup[0])
    (FIXTURES / "path_rt_dup_b.pb").write_bytes(dup[1])
    print(
        f"\nwrote path_rt_gen_a/b.pb (churn, at {churn_at}s) and path_rt_dup_a/b.pb (at {dup_at}s)"
    )

    # Stops snapshot: the 13a parent stops table, so the goldens decode without
    # network or the static zip (mirrors railroad_lirr_stops.json).
    static = asyncio.run(path_static.load_path_static())
    if not static.get("stops"):
        print("FAILED: load_path_static returned no parent stops; cannot freeze path_stops.json")
        return 1
    stops = static["stops"]
    (FIXTURES / "path_stops.json").write_text(json.dumps(stops, indent=0, sort_keys=True))
    print(f"wrote path_stops.json ({len(stops)} parent stations)")

    # Expected decoder output for gen_a, `now` frozen to its header timestamp
    # (the same freeze the railroad goldens use). Verify the printout by hand
    # before committing.
    gen_a = churn[0]
    feed = pb.FeedMessage()
    feed.ParseFromString(gen_a)
    now = float(feed.header.timestamp)
    trains, arrivals, feed_ts, unresolved = feeds._decode_path_feed(gen_a, stops, now)
    if unresolved:
        # The stops snapshot was captured in the same session as the feed, so
        # every bridge station id must resolve; a mismatch means the static
        # table and the bridge disagree RIGHT NOW and the fixture would fail
        # its own golden (which asserts unresolved == 0). Do not commit it.
        print(
            f"FAILED: gen_a references {unresolved} entities whose station ids are "
            "missing from the static stops table; fix the static/bridge drift first."
        )
        return 1
    expected = {"now": now, "trains": trains, "arrivals": arrivals}
    (FIXTURES / "path_rt_gen_a_expected.json").write_text(
        json.dumps(expected, indent=0, sort_keys=True)
    )
    print(f"wrote path_rt_gen_a_expected.json (feed_timestamp {feed_ts})")

    print("\n" + "=" * 72)
    print("MANUAL VERIFICATION (eyeball before committing, per house rules)")
    print("=" * 72)
    print(
        f"entities in gen_a: {len(feed.entity)} | decoded trains: {len(trains)} | "
        f"unresolved station ids: {unresolved}"
    )
    by_dir: dict[str, int] = {}
    for t in trains:
        by_dir[t["direction"] or "(none)"] = by_dir.get(t["direction"] or "(none)", 0) + 1
    print(f"trains by direction: {by_dir}")
    print(f"stations with arrivals: {len(arrivals)}")
    for t in trains[:5]:
        print(
            f"  sample train: route {t['route_id']} {t['direction']} -> "
            f"{t['stop_name']} at {t['next_time']}"
        )
    for sid in list(arrivals)[:3]:
        print(f"  sample arrivals at {sid}: {arrivals[sid]}")
    print(
        "\nCapture log: churn pair at "
        f"{churn_at}s, duplicate pair at {dup_at}s, {polls} polls total."
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "rush":
        sys.exit(rush_main())
    if len(sys.argv) > 1:
        print(f"unknown mode {sys.argv[1]!r}; usage: gen_path_rt_fixture.py [rush]")
        sys.exit(2)
    sys.exit(main())
