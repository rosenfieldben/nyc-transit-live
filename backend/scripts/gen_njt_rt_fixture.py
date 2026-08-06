#!/usr/bin/env python3
"""Generate the committed NJ Transit REALTIME fixtures (backend/tests/fixtures/njt_tu.pb
and njt_alerts.pb), plus the decoded golden they are checked against.

RUN THIS LOCALLY, WITH CREDENTIALS, AND PREFERABLY AT RUSH HOUR:

    export NJT_USERNAME=...   # or put both in the project-root .env
    export NJT_PASSWORD=...
    python backend/scripts/gen_njt_rt_fixture.py

Nothing in CI runs this and nothing in CI touches the live API. Until the
fixtures are committed, the golden tests that read them are gated by
conftest.golden_fixture_guard, which skips them locally and FAILS them under
CI=true, on purpose: 13a and 13b both merged green while ten goldens were
dormant, because a skip is invisible in a passing summary line.

WHY THE RUSH HOUR MATTERS MORE HERE THAN FOR ANY OTHER GENERATOR. The probe's own
words are that "the overnight numbers are optimistic by roughly 2x", and the
shapes this fixture exists to preserve are the ones that only appear under load:
8% of peak Penn stop_time_updates were phantoms of a trip-level CANCELED trip,
and both SKIPPED variants (238 with times, 35 without) were counted at peak. An
overnight capture is a valid feed with almost nothing in it, and committing one
would leave the golden asserting that a nearly empty feed decodes to nearly
nothing. The checks below therefore REPORT the trap-shape counts and refuse to
write a capture that carries none of them.

WHAT IS DELIBERATELY NOT CAPTURED: getVehiclePositions. It is not fetched, not
parsed, not modelled, and not committed; the numbers behind that decision are at
the poller registry in backend/pollers.py. If you came here to add it, read that
first.

THE ADDED TRAP CANNOT BE CAPTURED AT ALL. schedule_relationship ADDED was never
observed in either probe, so no capture will ever contain one. Decoder law 3 is
therefore pinned only by the synthetic tests in backend/tests/test_njt_rt.py and
by the contract tier's trap matrix, and that is a permanent division of labour
rather than a gap waiting to be filled.

IT REUSES THE PRODUCTION TOKEN DOOR (njt_auth.njt_post) rather than posting for
itself, which makes this script a live smoke test of that module as a side
effect, and costs ONE token for both feeds because the door's cache is shared.

The script verifies the live feeds still match the facts probed 2026-08-05, then
prints what it found for eyeballing. It exits nonzero on any drift, so a stale or
empty regeneration cannot slip in quietly.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path

# The same two-line preamble the other generators use, so a script run directly
# can import the app modules that live in backend/.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import njt_auth  # noqa: E402
import njt_static  # noqa: E402
from feeds import njt as njt_feed  # noqa: E402

OUT_DIR = REPO_ROOT / "backend" / "tests" / "fixtures"

# --- facts probed live 2026-08-05 (overnight 02:37 EDT and rush 18:15 EDT) ---
#
# Each is a FLOOR or a RANGE rather than an exact number, because a realtime feed
# legitimately differs poll to poll. What must not differ is the SHAPE, and these
# are calibrated so an ordinary rush capture passes while an overnight one (or a
# feed that stopped publishing trip descriptors) fails.

# Trips in flight at peak. The probe counted 745; a capture with fewer than this
# is either off-peak or a feed in trouble, and either way is not what the goldens
# are supposed to be asserting against.
MIN_PEAK_TRIPS = 200

# The entity.id / trip_short_name agreement the decoder cross-checks. 745 of 745
# at the probe, so this is written as "all of them" with the tolerance stated: a
# single mismatch is worth a human looking, because it is the assumption
# feeds.njt._identity is built on.
EXPECTED_CROSS_CHECK_AGREEMENT = 1.0

# Header lag, in seconds, measured as (our receive time - header timestamp).
# 9s to 23s at peak. Bounded generously on both sides: a NEGATIVE lag means a
# clock disagreement worth knowing about, and a lag far past the observed range
# means the capture is of a feed that has stopped regenerating, which would make
# every freshness golden derived from it wrong.
HEADER_LAG_RANGE_S = (-5.0, 120.0)

# THE TRAP SHAPES. A capture with none of these is legal and useless: the goldens
# it feeds would assert that a feed with no cancellations decodes without
# dropping anything. At least one of each must be present, which the probe's peak
# counts (8% of Penn stop_time_updates phantom, 238 SKIPPED-with-times, 35 bare)
# make a low bar at rush and an impossible one overnight, which is the intent.
MIN_CANCELED_TRIPS = 1
MIN_SKIPPED_WITH_TIMES = 1
MIN_SKIPPED_BARE = 1

# Penn Station New York, the stop every phantom claim is measured at.
PENN = "109"

# Alerts. The probe counted 263 at peak, 162 of them stop-scoped. Floors only:
# a quiet day is legitimate, an EMPTY alerts feed is not what the golden wants.
MIN_ALERTS = 1


def _download() -> tuple[bytes, bytes, float]:
    """Mint once, POST both realtime feeds, return (trip_updates, alerts, received_at).

    ONE TOKEN FOR BOTH, which is not an optimization but the same single-flight
    cache the app relies on: njt_auth.njt_post takes its token from a
    process-wide cache and re-mints at most once per attempt. A regeneration
    therefore costs one token against a rate limit NJ Transit does not publish,
    exactly as a production poll cycle does.
    """
    if not njt_auth.is_configured():
        raise SystemExit(
            f"{njt_auth.USERNAME_VAR} and {njt_auth.PASSWORD_VAR} must be set (in the "
            "environment or the project-root .env) to download the NJ Transit feeds."
        )

    async def both() -> tuple[bytes, bytes]:
        # Sequential rather than gathered, on purpose. Concurrency here would
        # exercise the single-flight lock, which is a fine thing to test and a
        # bad thing to depend on in a script whose failure mode is "spent two
        # tokens and did not notice".
        tu = await njt_auth.njt_post(njt_feed.NJT_TU_URL)
        alerts = await njt_auth.njt_post(njt_feed.NJT_ALERTS_URL)
        return tu, alerts

    print(f"Minting a token and POSTing {njt_feed.NJT_TU_URL} ...")
    tu, alerts = asyncio.run(both())
    received_at = time.time()
    print(f"  trip updates: {len(tu)} bytes")
    print(f"  alerts:       {len(alerts)} bytes")
    return tu, alerts, received_at


def _static_tables() -> tuple[dict, dict]:
    """The committed 15a static fixture, as the two indexes the decoder joins.

    FROM THE COMMITTED FIXTURE, NEVER A SECOND LIVE DOWNLOAD. The golden's whole
    value is that it is reproducible: decoding tomorrow's capture against
    tomorrow's static would make a red golden ambiguous between a decoder change
    and a schedule change. This also means a realtime capture must be regenerated
    together with the static one when trip ids roll over, which the join-rate
    check below will say loudly if you forget.
    """
    fixture = OUT_DIR / "njt_gtfs"
    if not (fixture / "trips.txt").exists():
        raise SystemExit(
            f"the 15a static fixture is missing ({fixture}); run "
            "backend/scripts/gen_njt_fixture.py first, because the realtime golden "
            "joins against it"
        )
    # Through the PRODUCTION parser, the same way test_njt_static's goldens read
    # this directory: the fixture is committed as loose .txt members, so it is
    # zipped in memory rather than parsed by a second reader that could disagree
    # with njt_static about a quoted field or a BOM.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted(fixture.iterdir()):
            if path.suffix == ".txt":
                zf.writestr(path.name, path.read_text(encoding="utf-8"))
    parsed = njt_static._parse_zip(buffer)
    return parsed["stops"], njt_static.build_njt_trip_index(parsed["trips"])


def _shapes(raw: bytes) -> dict:
    """Count the shapes the decoder law is written about, straight off the wire.

    Deliberately NOT via the decoder: this is the input side of the golden, and
    counting phantoms with the code that is supposed to drop them would make a
    broken decoder look like a feed with no cancellations in it.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    trip_sr = Counter()
    canceled_trips = 0
    skipped_with_times = 0
    skipped_bare = 0
    phantom_penn_calls = 0
    penn_calls = 0
    id_matches = 0
    id_compared = 0
    trips = 0

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        trips += 1
        tu = entity.trip_update
        relationship = pb.TripDescriptor.ScheduleRelationship.Name(tu.trip.schedule_relationship)
        trip_sr[relationship] += 1
        canceled = relationship == "CANCELED"
        canceled_trips += canceled
        for stu in tu.stop_time_update:
            if stu.stop_id == PENN:
                penn_calls += 1
            if stu.schedule_relationship != pb.TripUpdate.StopTimeUpdate.SKIPPED:
                continue
            has_times = stu.HasField("arrival") or stu.HasField("departure")
            skipped_with_times += has_times
            skipped_bare += not has_times
            # THE PHANTOM, counted at Penn: a call the feed marks skipped, on a
            # trip it has canceled, still carrying a time a board would print.
            if canceled and has_times and stu.stop_id == PENN:
                phantom_penn_calls += 1
        # The cross-check the decoder makes a warning of. Counted here against
        # the trip id's own short name via the static index, since entity.id is
        # supposed to equal both.
        if entity.id:
            id_compared += 1
            id_matches += entity.id == (_TRIPS.get(tu.trip.trip_id) or {}).get("short_name")

    return {
        "trips": trips,
        "trip_relationships": dict(trip_sr),
        "canceled_trips": canceled_trips,
        "skipped_with_times": skipped_with_times,
        "skipped_bare": skipped_bare,
        "penn_calls": penn_calls,
        "phantom_penn_calls": phantom_penn_calls,
        "cross_check_agreement": (id_matches / id_compared) if id_compared else 0.0,
        "header_timestamp": float(feed.header.timestamp),
    }


_TRIPS: dict = {}


def main() -> int:
    global _TRIPS
    stops, _TRIPS = _static_tables()
    print(f"static fixture: {len(stops)} stops, {len(_TRIPS)} trips")

    tu_raw, alerts_raw, received_at = _download()
    shapes = _shapes(tu_raw)
    lag = received_at - shapes["header_timestamp"] if shapes["header_timestamp"] else None

    print("\nWHAT THE LIVE FEED CARRIES RIGHT NOW:")
    print(f"  trips in flight        {shapes['trips']}")
    print(f"  trip relationships     {shapes['trip_relationships']}")
    print(f"  CANCELED trips         {shapes['canceled_trips']}")
    print(f"  SKIPPED with times     {shapes['skipped_with_times']}")
    print(f"  SKIPPED bare           {shapes['skipped_bare']}")
    print(f"  calls at Penn ({PENN})     {shapes['penn_calls']}")
    print(f"  PHANTOM calls at Penn  {shapes['phantom_penn_calls']}")
    print(f"  entity.id agreement    {shapes['cross_check_agreement']:.4f}")
    print(f"  header lag             {lag:.1f}s" if lag is not None else "  header lag  (none)")

    problems: list[str] = []
    if shapes["trips"] < MIN_PEAK_TRIPS:
        problems.append(
            f"only {shapes['trips']} trips in flight (want >= {MIN_PEAK_TRIPS}). This looks "
            "like an off-peak capture; the traps the goldens are about only appear under load."
        )
    if shapes["cross_check_agreement"] < EXPECTED_CROSS_CHECK_AGREEMENT:
        problems.append(
            f"entity.id agrees with trip_short_name on only "
            f"{shapes['cross_check_agreement']:.2%} of trips (probe: 745/745). This is the "
            "assumption feeds.njt._identity is built on; a real drift here is worth a "
            "human decision, not a regenerated fixture."
        )
    if lag is None:
        problems.append("the feed carries no header timestamp, so no freshness claim can be made")
    elif not (HEADER_LAG_RANGE_S[0] <= lag <= HEADER_LAG_RANGE_S[1]):
        problems.append(
            f"header lag {lag:.1f}s is outside {HEADER_LAG_RANGE_S} (probe: 9s to 23s at peak). "
            "Negative means a clock disagreement; far high means the feed stopped regenerating."
        )
    if shapes["canceled_trips"] < MIN_CANCELED_TRIPS:
        problems.append(
            "no trip-level CANCELED trip in this capture, so the golden could not tell a "
            "decoder that drops phantoms from one that never meets any"
        )
    if shapes["skipped_with_times"] < MIN_SKIPPED_WITH_TIMES:
        problems.append("no SKIPPED-with-times call in this capture (decoder law 2, 238 at peak)")
    if shapes["skipped_bare"] < MIN_SKIPPED_BARE:
        problems.append("no bare SKIPPED call in this capture (decoder law 2, 35 at peak)")

    alerts_feed = pb.FeedMessage()
    alerts_feed.ParseFromString(alerts_raw)
    alert_count = sum(1 for e in alerts_feed.entity if e.HasField("alert"))
    stop_scoped = sum(
        1
        for e in alerts_feed.entity
        if e.HasField("alert") and any(ie.stop_id for ie in e.alert.informed_entity)
    )
    print(f"\n  alerts                 {alert_count} ({stop_scoped} stop-scoped)")
    if alert_count < MIN_ALERTS:
        problems.append("the alerts feed is empty, so the alerts golden would assert nothing")

    if problems:
        print("\nFEED DRIFT OR AN UNUSABLE CAPTURE, fixtures NOT written:")
        for problem in problems:
            print(f"  !! {problem}")
        return 1

    # THE DECODED GOLDEN, frozen at the feed's OWN header timestamp rather than at
    # wall-clock now. Every window in the decoder (the just-passed grace, the
    # future-first-stop ceiling, the dwell test) is relative to `now`, so a golden
    # decoded at capture time and re-decoded at test time would differ for a
    # reason that has nothing to do with the code. The header timestamp is the one
    # instant that travels with the bytes.
    now = shapes["header_timestamp"]
    trains, arrivals, feed_ts, warnings = njt_feed.decode_njt_trip_updates(
        tu_raw, stops, _TRIPS, now
    )
    print(
        f"\ndecoded at the feed's own header timestamp: {len(trains)} trains placed, "
        f"{sum(len(v) for v in arrivals.values())} arrivals across {len(arrivals)} stops, "
        f"{len(warnings)} cross-check warnings"
    )
    # THE JOIN RATE IS THE ONE THAT CATCHES A STALE STATIC FIXTURE. Trip ids roll
    # over with each schedule publication, so a realtime capture taken months
    # after the static one joins nothing and produces a golden full of trains
    # with synthesized names, which would look like a decoder bug forever after.
    joined = sum(1 for t in trains if t["trip_id"] in _TRIPS)
    print(f"  {joined} of {len(trains)} placed trains join the committed static fixture")
    if trains and joined == 0:
        print(
            "\n  !! NOTHING joins the static fixture. Regenerate it first "
            "(backend/scripts/gen_njt_fixture.py); trip ids roll over with each "
            "schedule publication."
        )
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "njt_tu.pb").write_bytes(tu_raw)
    (OUT_DIR / "njt_alerts.pb").write_bytes(alerts_raw)
    (OUT_DIR / "njt_tu_expected.json").write_text(
        json.dumps(
            {
                "now": now,
                "feed_timestamp": feed_ts,
                "trains": trains,
                "arrivals": arrivals,
                "warnings": warnings,
                # The input-side counts, committed WITH the golden so a future
                # reader can tell a decode change from a capture change without
                # re-running this script.
                "capture_shapes": shapes,
            },
            indent=0,
            sort_keys=True,
        )
    )
    print(
        "\nWrote njt_tu.pb, njt_alerts.pb and njt_tu_expected.json. Eyeball the counts above "
        "against the decoder law before committing: a capture with no phantoms and no skips "
        "passes every check that follows and proves nothing."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
