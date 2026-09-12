#!/usr/bin/env python3
"""Section 6.0 of docs/design/freshness-contract.md: the bus observation-clock probe.

WHY THIS EXISTS, AND WHY IT IS NOT AN Fnn.

Every other script in this directory re-derives a number in section 2 of
docs/reviews/audit-2026-09-05.md. This one re-derives the buses row of the
per-provider age policy in section 3.3 of docs/design/freshness-contract.md,
which that design could not write:

    "There is no committed bus capture, so whether OneBusAway populates that
    field is unmeasured here. Every other row in the table above was measured;
    this one cannot be, and the honest consequence is that the buses row of the
    age policy cannot be written yet."

The design made the probe a gate rather than a note (Decisions, Q6), so this is
that gate. It is a MEASUREMENT, not a defect reproduction: there is no before
and no after, and nothing here is expected to start failing when a fix lands.
It fails only if the committed capture stops supporting the recorded numbers,
which is the same contract every other script in this directory has.

WE DO NOT CONSUME SIRI. The audit's remedy language mentions a SIRI
RecordedAtTime; this application fetches OneBusAway's GTFS-Realtime endpoint
(backend/feeds/buses.py, VEHICLE_POSITIONS_URL), so the analogous field is
VehiclePosition.timestamp and that is what this probe measures.

RUN IT (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-09-05/probe_bus_observation_clock.py

THE FETCH, RECORDED HERE BECAUSE THE SCRIPT MUST NOT REPEAT IT.
The capture was taken once, by hand, on 2026-09-12:

    GET https://gtfsrt.prod.obanyc.com/vehiclePositions?key=<BUS_TIME_API_KEY>
    HTTP 200, 280965 bytes, no Content-Type header.
    HTTP Date:         Sat, 12 Sep 2026 16:28:40 GMT  (epoch 1789230520)
    request sent at:   1789230530.434  (this machine's clock)
    response read at:  1789230530.681  (0.247s later)

Those four numbers are evidence and are used below, so the two clock
comparisons they support are stated once here and not recomputed:

  * SAME-CLOCK, and therefore the trustworthy one: the origin's own Date header
    minus the feed header is +5s. At the instant the origin answered, by its own
    clock, the feed content was five seconds old.
  * CROSS-CLOCK, and therefore contaminated: our read instant minus the feed
    header is +15.7s. Round-trip time was 0.247s, so the residual ~10s is clock
    skew between this machine and the origin, not publish lag. It is recorded so
    nobody later reads 15.7 as a provider measurement. This is the same
    discipline THE THREE TIMESTAMPS in backend/cache.py states: compare
    same-clock pairs, and a cross-clock pair measures the clocks.

NO BACKEND IMPORT, AND THEREFORE NO CONTAINMENT.
Every other Python script here calls _hermetic.contain() before importing a
backend module, because two separate load_dotenv calls refill NJ Transit
credentials on import. This script imports nothing from backend/: it parses the
committed protobuf with google.transit.gtfs_realtime_pb2 and reads bytes off
disk. There is no import that could reach NJ Transit, so there is nothing to
contain, and adding contain() here would imply a risk that does not exist. The
containment rule is unchanged for every script that does import backend code.

NO TREE SCAN. The README's rule ("scope every tree scan away from this
directory") does not apply because this script scans nothing: it opens two named
fixtures and measures their bytes.
"""

from __future__ import annotations

import statistics
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as gtfs

ROOT = Path(__file__).resolve().parents[3]
CAPTURE = ROOT / "backend" / "tests" / "fixtures" / "bus_vehicle_positions.pb"
MNR_CAPTURE = ROOT / "backend" / "tests" / "fixtures" / "railroad_mnr.pb"

# The recorded measurement. Every one of these is re-derived below from the
# committed bytes; the script exits non-zero the moment one stops holding, which
# is what makes the section 3.3 row re-derivable rather than asserted.
EXPECT = {
    "bytes": 280965,
    "header_ts": 1789230515,
    "entities": 2136,
    "with_position": 2136,
    "with_timestamp": 2136,
    "positioned_without_timestamp": 0,
    "trip_updates": 0,
    "age_min": 0,
    "age_median": 15.0,
    "age_p95": 29.0,
    "age_max": 104,
    "over_90s": 1,
    "over_600s": 0,
    "ahead_of_header": 0,
    "equal_to_header": 20,
    "distinct_timestamps": 47,
    "timestamp_spread_s": 104,
    "distinct_vehicle_ids": 2136,
    "http_date_minus_header_s": 5,
}

# The fetch instants, from the block above. Constants rather than a clock read,
# so this script is deterministic and offline.
HTTP_DATE_EPOCH = 1789230520
REQUEST_SENT = 1789230530.434
RESPONSE_READ = 1789230530.681

FAILURES: list[str] = []


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def check(ok: bool, label: str, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {label}" + (f"   ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(label + (f" :: {detail}" if detail else ""))
    return ok


def percentile(values: list[int], q: float) -> float:
    """Linear-interpolation percentile, stated explicitly because the convention
    matters at n=2136 and a nearest-rank reading would give a different p95."""
    if not values:
        raise ValueError("no values")
    k = (len(values) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def main() -> None:
    raw = CAPTURE.read_bytes()

    rule("A. The committed capture")
    check(len(raw) == EXPECT["bytes"], "byte count", f"{len(raw)} bytes")
    feed = gtfs.FeedMessage()
    feed.ParseFromString(raw)
    header = feed.header.timestamp
    check(header == EXPECT["header_ts"], "feed header timestamp", str(header))
    check(len(feed.entity) == EXPECT["entities"], "entity count", str(len(feed.entity)))
    print(
        f"  gtfs_realtime_version={feed.header.gtfs_realtime_version!r} "
        f"incrementality={feed.header.incrementality}"
    )

    vehicles = [e.vehicle for e in feed.entity if e.HasField("vehicle")]
    positioned = [v for v in vehicles if v.HasField("position")]
    stamped = [v for v in positioned if v.HasField("timestamp") and v.timestamp]
    trip_updates = sum(1 for e in feed.entity if e.HasField("trip_update"))

    rule("B. Does the provider date each observation")
    check(
        len(positioned) == EXPECT["with_position"],
        "entities carrying a position",
        str(len(positioned)),
    )
    check(
        len(stamped) == EXPECT["with_timestamp"],
        "of those, carrying VehiclePosition.timestamp",
        str(len(stamped)),
    )
    check(
        len(positioned) - len(stamped) == EXPECT["positioned_without_timestamp"],
        "positioned vehicles WITHOUT a timestamp",
        str(len(positioned) - len(stamped)),
    )
    check(
        trip_updates == EXPECT["trip_updates"],
        "trip_update entities (this endpoint carries none)",
        str(trip_updates),
    )
    ids = {v.vehicle.id for v in positioned if v.vehicle.id}
    check(
        len(ids) == EXPECT["distinct_vehicle_ids"],
        "distinct vehicle ids (one entity per vehicle)",
        str(len(ids)),
    )

    rule("C. The age distribution, header minus vehicle.timestamp")
    ages = sorted(header - v.timestamp for v in stamped)
    p95 = percentile(ages, 0.95)
    median = statistics.median(ages)
    check(ages[0] == EXPECT["age_min"], "minimum age", f"{ages[0]}s")
    check(median == EXPECT["age_median"], "median age", f"{median}s")
    check(p95 == EXPECT["age_p95"], "p95 age (linear interpolation)", f"{p95}s")
    check(ages[-1] == EXPECT["age_max"], "maximum age", f"{ages[-1]}s")
    over90 = sum(1 for a in ages if a > 90)
    over600 = sum(1 for a in ages if a > 600)
    check(over90 == EXPECT["over_90s"], "observations older than 90s", f"{over90} of {len(ages)}")
    check(over600 == EXPECT["over_600s"], "observations older than 600s", str(over600))
    print(
        f"  mean={statistics.mean(ages):.1f}s   "
        f"quartiles p25/p50/p75 = {percentile(ages, 0.25):.1f} / "
        f"{percentile(ages, 0.50):.1f} / {percentile(ages, 0.75):.1f}s"
    )

    rule("D. Is the stamp independent, or the Metro-North copy pattern")
    ahead = sum(1 for a in ages if a < 0)
    equal = sum(1 for a in ages if a == 0)
    distinct = {v.timestamp for v in stamped}
    spread = max(distinct) - min(distinct)
    check(ahead == EXPECT["ahead_of_header"], "observations AHEAD of the header", str(ahead))
    check(equal == EXPECT["equal_to_header"], "observations EQUAL to the header", str(equal))
    check(
        len(distinct) == EXPECT["distinct_timestamps"],
        "distinct timestamp values",
        str(len(distinct)),
    )
    check(spread == EXPECT["timestamp_spread_s"], "spread of the timestamps", f"{spread}s")
    # THE DISCRIMINATING COMPARISON. Metro-North copies its header onto every
    # vehicle, so its stamps carry no independent signal (1 distinct value, spread
    # 0). The same two measurements over the committed MNR capture are printed
    # beside these so the contrast is a measurement rather than a claim.
    mnr = gtfs.FeedMessage()
    mnr.ParseFromString(MNR_CAPTURE.read_bytes())
    mnr_stamps = {
        e.vehicle.timestamp
        for e in mnr.entity
        if e.HasField("vehicle") and e.vehicle.HasField("position")
    }
    mnr_positioned = sum(
        1 for e in mnr.entity if e.HasField("vehicle") and e.vehicle.HasField("position")
    )
    check(
        len(mnr_stamps) == 1 and mnr_stamps == {mnr.header.timestamp},
        "Metro-North control: all stamps are the header, 1 distinct value",
        f"{mnr_positioned} positioned, {len(mnr_stamps)} distinct",
    )
    check(
        len(distinct) > 1 and equal < len(stamped),
        "buses are NOT the Metro-North pattern",
        f"{len(distinct)} distinct values across {spread}s, {equal} of {len(stamped)} tie",
    )

    rule("E. Is the header a generation time or a write time")
    newest = max(distinct)
    check(
        header == newest,
        "the header EQUALS the newest observation",
        f"header {header}, newest {newest}",
    )
    # A write clock is independent of content: it advances on regeneration whether
    # or not anything upstream moved, which is what backend/models.py:237-239
    # records about the PATH bridge. A header that is exactly the maximum of the
    # observations it carries is computed FROM them, so it is a generation time.
    check(
        header - newest == 0 and ages[0] == 0,
        "so the header is derived from content, not stamped independently",
        "max(vehicle.timestamp) == header.timestamp",
    )
    same_clock = HTTP_DATE_EPOCH - header
    cross_clock = RESPONSE_READ - header
    check(
        same_clock == EXPECT["http_date_minus_header_s"],
        "same-clock publish lag: origin Date minus feed header",
        f"{same_clock}s",
    )
    print(
        f"  cross-clock, recorded and NOT used as a provider measurement: "
        f"read {RESPONSE_READ} minus header = {cross_clock:.1f}s, of which "
        f"{RESPONSE_READ - REQUEST_SENT:.3f}s was round trip and the rest is skew"
    )

    rule("F. What the section 3.3 policy row says, and why")
    print("  Clock the rule reads : VehiclePosition.timestamp")
    print(
        f"  Age-gated            : Yes ({len(stamped)} of {len(positioned)} positioned "
        f"vehicles self-dated, {len(distinct)} distinct values)"
    )
    print(f"  Cost at OBS_FRESH_S 90  : {over90} observation qualified of {len(ages)}")
    print(f"  Cost at OBS_MAX_S 600   : {over600} observations dropped of {len(ages)}")

    print()
    if FAILURES:
        print("THE COMMITTED BYTES NO LONGER SUPPORT THE RECORD. Failed assertions:")
        for item in FAILURES:
            print(f"  - {item}")
        print("DISPOSITION: NOT AS RECORDED, see the failed assertions above")
        raise SystemExit(1)
    print(
        f"DISPOSITION: MEASURED. OneBusAway dates every observation it sends: all "
        f"{len(stamped)} of {len(positioned)} positioned vehicles carry "
        f"VehiclePosition.timestamp, across {len(distinct)} distinct values spanning "
        f"{spread}s, none ahead of the header and only the newest {equal} tying it, so "
        f"this is a real per-observation clock and not Metro-North's copied header. The "
        f"ages run {ages[0]}s to {ages[-1]}s with a median of {median}s and a p95 of "
        f"{p95}s; {over90} of {len(ages)} exceeds 90s and {over600} exceeds 600s. The "
        f"header equals the newest observation exactly, so it is a generation time "
        f"computed from content rather than a write time, and by the origin's own clock "
        f"the feed it served was {same_clock}s old. The buses row is age-gated on "
        f"VehiclePosition.timestamp."
    )


if __name__ == "__main__":
    main()
