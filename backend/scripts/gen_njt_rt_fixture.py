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

ADDED IS REAL AFTER ALL, AND IT CARRIES AN EMPTY trip_id. Both 2026-08-05 probes
saw none, and this file used to say no capture ever would; a later live capture
carried 36 of them out of 164 trip_updates, every one with trip_id "". So decoder
law 3 is no longer synthetic-only, and the empty id is the load-bearing part: a
decoder keying on trip_id would collapse all 36 into one train. The identity chain
in feeds.njt._identity falls through to entity.id for exactly this, and the
goldens below assert the captured extras survive DISTINCTLY.

IT REUSES THE PRODUCTION TOKEN DOOR (njt_auth.njt_post) rather than posting for
itself, which makes this script a live smoke test of that module as a side
effect, and costs ONE token for all three downloads because the door's cache is
shared.

IT ALSO RE-TRIMS THE STATIC FIXTURE, which is why it downloads the archive at all.
The committed static and realtime fixtures have to JOIN, and they used to be
chosen by unrelated rules: the static kept two lexicographically-first trips per
route, the realtime kept whatever happened to be moving. Those sets intersect only
by luck, and at 17:20 on a live rush capture they did not intersect at all, which
blocked the capture entirely. Now the static fixture is re-cut from the same
archive around the trips the capture actually contains, so the pair joins by
construction and the goldens can assert a MEASURED join floor.

EVERY REFUSAL BELOW STATES WHAT IT MEASURED. The version that blocked the first
capture asserted "trip ids roll over with each schedule publication" as the cause
of a zero join, having measured no such thing, and it was wrong: the archive
re-downloaded byte-identical to the probe's. Rollover is real and is named as one
candidate among several, with the numbers a reader needs to tell them apart.

The script verifies the live feeds still match the facts probed 2026-08-05, then
prints what it found for eyeballing. It exits nonzero on any drift, so a stale or
empty regeneration cannot slip in quietly.
"""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
import sys
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

# The same two-line preamble the other generators use, so a script run directly
# can import the app modules that live in backend/.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import njt_auth  # noqa: E402
import njt_static  # noqa: E402
from feeds import njt as njt_feed  # noqa: E402

# The trim, shared with gen_njt_fixture.py so the re-trim below writes exactly what
# a first trim would. Loaded by path because scripts/ is not an importable package.
_TRIM_SPEC = importlib.util.spec_from_file_location(
    "njt_fixture_trim", Path(__file__).resolve().parent / "njt_fixture_trim.py"
)
trim = importlib.util.module_from_spec(_TRIM_SPEC)
_TRIM_SPEC.loader.exec_module(trim)

OUT_DIR = REPO_ROOT / "backend" / "tests" / "fixtures"
# The static fixture this script RE-TRIMS on a successful capture, so the
# committed pair joins. Same directory gen_njt_fixture.py writes.
STATIC_OUT_DIR = OUT_DIR / "njt_gtfs"

# --- facts probed live 2026-08-05 (overnight 02:37 EDT and rush 18:15 EDT) ---
#
# Each is a FLOOR or a RANGE rather than an exact number, because a realtime feed
# legitimately differs poll to poll. What must not differ is the SHAPE, and these
# are calibrated so an ordinary rush capture passes while an overnight one (or a
# feed that stopped publishing trip descriptors) fails.

# A SANITY FLOOR ON TRIP COUNT, not a peak detector, and the difference is a
# correction. This was 200, calibrated against the probe's "745 trips" without
# establishing what that number counted. A live rush capture at 17:20 carried 165
# trip_updates, so the floor was rejecting exactly the captures it was written to
# accept. What the goldens actually need is the TRAP SHAPES, and those have their
# own floors below; this one only has to reject the overnight state the probe
# recorded as a 13-byte empty feed.
MIN_TRIPS = 40

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
# ADDED joined the list once a capture proved it exists (36 of them, every one with
# an empty trip_id). It is the only trap shape whose bytes the decoder's identity
# chain is built on, so a capture without one retires that evidence; the goldens
# assert the same floor, since a fixture can arrive past this script.
MIN_ADDED_TRIPS = 1

# Penn Station New York, the stop every phantom claim is measured at.
PENN = "109"

# Alerts. The probe counted 263 at peak, 162 of them stop-scoped. Floors only:
# a quiet day is legitimate, an EMPTY alerts feed is not what the golden wants.
MIN_ALERTS = 1

# The share of live trip_updates that must join the FULL LIVE STATIC, measured
# against the archive downloaded in the same run.
#
# AGAINST THE FULL STATIC, NEVER THE TRIM, and that distinction is the whole
# defect this script was blocked on. The check used to join live trips against the
# COMMITTED fixture, which is 25 trips (two lexicographically-first per route). At
# 17:20 none of the 165 trips in flight were among those 25, so the rate measured
# 0.0000 and the script refused, blaming a schedule rollover it had never
# measured. The archive that evening re-downloaded byte-identical to the probe's,
# so no rollover had happened; the trim was simply the wrong denominator.
#
# THE DENOMINATOR IS TRIPS THAT CLAIM TO BE SCHEDULED, which is SCHEDULED and
# CANCELED: a canceled train was scheduled and still carries its trip_id. ADDED
# trips are excluded because NJ Transit publishes them with an EMPTY trip_id, so
# they are definitionally unjoinable and no floor over them means anything.
#
# THAT DISTINCTION BLOCKED A CAPTURE. With ADDED in the denominator a live feed of
# 164 trip_updates measured 128/164 = 0.7805 and was refused, while the joinable
# set was 128/128 = 1.0000: a perfect join reported as a failure because 36 trips
# that can never join were being counted against it. 164 - 128 = 36 exactly, and
# every one of the 36 was ADDED with an empty id.
#
# 0.95 rather than 1.0 because a publication boundary crossed between the two
# downloads would show as a handful of misses. A REAL rollover still fails this
# loudly, and says so with the number it measured.
MIN_LIVE_JOIN_RATE = 0.95


def _download() -> tuple[bytes, bytes, bytes, float]:
    """Mint once, POST both realtime feeds AND the static archive.

    Returns (trip_updates, alerts, static_zip, received_at).

    ONE TOKEN FOR ALL THREE, which is not an optimization but the same
    single-flight cache the app relies on: njt_auth.njt_post takes its token from
    a process-wide cache and re-mints at most once per attempt. A regeneration
    therefore costs one token against a rate limit NJ Transit does not publish,
    exactly as a production poll cycle does.

    THE STATIC ARCHIVE IS FETCHED HERE, AND THAT IS THE FIX. The join check below
    has to ask "do these live trips exist in the schedule this feed is running
    on", and the only thing that can answer it is the FULL publication. Asking the
    committed 25-trip trim instead produced 0.0000 at rush hour and a refusal that
    blamed a rollover which had not happened. Downloading it in the same run also
    means the pair written at the end is coherent by construction: same
    publication, same minute.
    """
    if not njt_auth.is_configured():
        raise SystemExit(
            f"{njt_auth.USERNAME_VAR} and {njt_auth.PASSWORD_VAR} must be set (in the "
            "environment or the project-root .env) to download the NJ Transit feeds."
        )

    async def everything() -> tuple[bytes, bytes, bytes]:
        # Sequential rather than gathered, on purpose. Concurrency here would
        # exercise the single-flight lock, which is a fine thing to test and a
        # bad thing to depend on in a script whose failure mode is "spent two
        # tokens and did not notice".
        #
        # REALTIME FIRST, STATIC SECOND. The header lag this capture is judged on
        # is measured from the trip updates, so the multi-megabyte archive
        # download must not sit between the feed and the clock reading.
        tu = await njt_auth.njt_post(njt_feed.NJT_TU_URL)
        alerts = await njt_auth.njt_post(njt_feed.NJT_ALERTS_URL)
        return tu, alerts, await njt_auth.njt_post(njt_static.NJT_STATIC_URL)

    print(f"Minting a token and POSTing {njt_feed.NJT_TU_URL} ...")
    tu, alerts, archive = asyncio.run(everything())
    received_at = time.time()
    print(f"  trip updates: {len(tu)} bytes")
    print(f"  alerts:       {len(alerts)} bytes")
    print(f"  static zip:   {len(archive)} bytes")
    return tu, alerts, archive, received_at


def _zip_members(members: dict[str, tuple[list[str], list[dict]]]) -> io.BytesIO:
    """An in-memory GTFS zip of (fieldnames, rows) members.

    Used to run the PRODUCTION parser over the trim that was just written, so the
    committed golden is the decode of the committed tables rather than of the full
    publication they were cut from. Writing the files and re-reading them would
    work too; building the zip keeps the whole check in one function and cannot be
    fooled by a stale file left behind from an earlier run.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, (fieldnames, rows) in members.items():
            out = io.StringIO()
            writer = csv.DictWriter(out, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in fieldnames})
            zf.writestr(name, out.getvalue())
    return buffer


def _live_static(archive: bytes) -> dict:
    """The FULL live publication, parsed by the production parser.

    NOT THE COMMITTED FIXTURE. That was the defect: the fixture is a 25-trip trim,
    so joining live realtime against it measured the trim rather than the
    schedule, and reported 0.0000 at exactly the hour the capture is supposed to
    be taken.

    Through njt_static._parse_zip so this reads the archive the same way the app
    does, which also makes the download a live smoke test of that parser.
    """
    return njt_static._parse_zip(io.BytesIO(archive))


def _raw_static_rows(archive: bytes) -> dict[str, tuple[list[str], list[dict]]]:
    """Every member the fixture carries, as (fieldnames, rows), straight from the zip.

    Separate from _live_static because the two want different things: the parsed
    tables are for JOINING (typed, indexed, production shapes), and these raw rows
    are for WRITING (every original column preserved in its original order, so the
    committed fixture is a faithful slice of the publication rather than a
    re-serialization of whatever the parser chose to keep).
    """
    members: dict[str, tuple[list[str], list[dict]]] = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for name in trim.FIXTURE_MEMBERS:
            with zf.open(name) as fh:
                reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
                members[name] = (list(reader.fieldnames or []), list(reader))
    return members


def _trip_ids_by_claim(raw: bytes) -> tuple[list[str], list[str]]:
    """(trip ids that CLAIM to be scheduled, trip ids of ADDED trips).

    THE SPLIT IS THE JOIN GATE'S WHOLE CORRECTNESS. A trip is joinable only if it
    says it is in the schedule, which SCHEDULED and CANCELED both do: a canceled
    train was scheduled and still carries its trip_id. ADDED says the opposite, and
    NJ Transit publishes ADDED trips with an EMPTY trip_id, so they are
    DEFINITIONALLY unjoinable and belong in no denominator.

    Measured on the capture that exposed this: 164 trip_updates, 128 joined, 36
    ADDED with empty ids, and 164 - 128 = 36 exactly. Over the joinable set the
    rate was 128/128 = 1.0000, a perfect join reported as a 0.78 failure purely
    because the denominator counted trips that can never be in it.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    scheduled, added = [], []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        trip = entity.trip_update.trip
        target = added if trip.schedule_relationship == pb.TripDescriptor.ADDED else scheduled
        target.append(trip.trip_id)
    return scheduled, added


def _shapes(raw: bytes) -> dict:
    """Count the shapes the decoder law is written about, straight off the wire.

    Deliberately NOT via the decoder: this is the input side of the golden, and
    counting phantoms with the code that is supposed to drop them would make a
    broken decoder look like a feed with no cancellations in it.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    trip_sr = Counter()
    canceled_trip_ids: list[str] = []
    added_trip_ids: list[str] = []
    added_entity_ids: list[str] = []
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
        if relationship == "ADDED":
            added_trip_ids.append(tu.trip.trip_id)
            added_entity_ids.append(entity.id)
        canceled = relationship == "CANCELED"
        canceled_trips += canceled
        if canceled and tu.trip.trip_id:
            # Recorded BY ID, not only counted, so the golden can assert that no
            # arrival in the decoded index belongs to one of them. A count alone
            # would let that assertion be written as a tautology.
            canceled_trip_ids.append(tu.trip.trip_id)
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
        # The cross-check the decoder makes a warning of, counted the SAME WAY the
        # decoder counts it: only over trips that actually JOINED the static.
        #
        # Counting every entity instead made this gate misfire on the one condition
        # it is most likely to meet. A trip absent from the committed static
        # fixture yields no short_name, so it read as a mismatch, and a single such
        # trip (an ADDED one, a train added since the capture, or a static fixture
        # one publication out of date, which this script's own docstring says to
        # expect) dropped the rate below 100% and aborted with "a real drift here
        # is worth a human decision, not a regenerated fixture". That blamed
        # feeds.njt._identity's invariant for a stale fixture, and it fired BEFORE
        # the join-rate check written for exactly that case could be reached.
        if entity.id and _TRIPS.get(tu.trip.trip_id):
            id_compared += 1
            id_matches += entity.id == _TRIPS[tu.trip.trip_id].get("short_name")

    return {
        "trips": trips,
        "trip_relationships": dict(trip_sr),
        "canceled_trips": canceled_trips,
        "canceled_trip_ids": sorted(canceled_trip_ids),
        # THE ADDED FACTS, recorded off the wire so the goldens can assert the
        # captured extras survive as distinct trains rather than collapsing on a
        # shared empty trip_id.
        "added_trips": len(added_trip_ids),
        "added_with_empty_trip_id": sum(1 for t in added_trip_ids if not t),
        "added_entity_ids": sorted(e for e in added_entity_ids if e),
        "added_distinct_entity_ids": len({e for e in added_entity_ids if e}),
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
    tu_raw, alerts_raw, archive, received_at = _download()

    # THE FULL LIVE PUBLICATION, which is what the join below is measured against.
    parsed = _live_static(archive)
    stops = parsed["stops"]
    _TRIPS = njt_static.build_njt_trip_index(parsed["trips"])
    print(f"\nlive static: {len(stops)} stops, {len(_TRIPS)} trips in this publication")

    shapes = _shapes(tu_raw)
    lag = received_at - shapes["header_timestamp"] if shapes["header_timestamp"] else None

    print("\nWHAT THE LIVE FEED CARRIES RIGHT NOW:")
    print(f"  trips in flight        {shapes['trips']}")
    print(f"  trip relationships     {shapes['trip_relationships']}")
    print(f"  CANCELED trips         {shapes['canceled_trips']}")
    print(f"  SKIPPED with times     {shapes['skipped_with_times']}")
    print(f"  SKIPPED bare           {shapes['skipped_bare']}")
    print(
        f"  ADDED trips            {shapes['added_trips']} "
        f"({shapes['added_distinct_entity_ids']} distinct entity.id)"
    )
    print(f"  calls at Penn ({PENN})     {shapes['penn_calls']}")
    print(f"  PHANTOM calls at Penn  {shapes['phantom_penn_calls']}")
    print(f"  entity.id agreement    {shapes['cross_check_agreement']:.4f}")
    print(f"  header lag             {lag:.1f}s" if lag is not None else "  header lag  (none)")

    problems: list[str] = []
    if shapes["trips"] < MIN_TRIPS:
        problems.append(
            f"MEASURED {shapes['trips']} trip_updates in this capture, below the sanity floor "
            f"of {MIN_TRIPS}. That is the overnight shape; the trap-shape floors below are "
            "what actually decide whether a capture is usable."
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
    if shapes["added_trips"] < MIN_ADDED_TRIPS:
        problems.append(
            "no ADDED trip in this capture, so nothing here pins decoder law 3 or the "
            "empty-trip_id keying to real bytes; extras run on disrupted evenings"
        )

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

    # THE LIVE JOIN, MEASURED FIRST, because a low rate explains several of the
    # problems above rather than being one more of them: entity.id agreement is
    # computed over joined trips, so a schedule this capture does not match makes
    # that number meaningless too.
    #
    # WHAT THIS REPORTS IS A MEASUREMENT, NOT A DIAGNOSIS. The previous version
    # printed "trip ids roll over with each schedule publication" as the cause of a
    # zero join, which it had not measured and which was false the evening it
    # fired: the archive re-downloaded byte-identical to the probe's, and the zero
    # came from joining against the 25-trip COMMITTED TRIM instead of the
    # publication. Rollover is a real phenomenon and is named below as one
    # possibility among several, with the numbers a reader needs to tell them
    # apart, rather than asserted as fact.
    claim_scheduled, added_ids = _trip_ids_by_claim(tu_raw)
    live_trip_ids = claim_scheduled + added_ids
    joined_trips = sum(1 for t in claim_scheduled if t in _TRIPS)
    join_rate = joined_trips / len(claim_scheduled) if claim_scheduled else 0.0
    print(
        f"\n  live join: {joined_trips}/{len(claim_scheduled)} SCHEDULED-or-CANCELED "
        f"trip_updates ({join_rate:.4f}) match a trip in this publication's "
        f"{len(_TRIPS)}-trip trips.txt"
    )
    # "all with empty trip_id: yes" against zero ADDED trips is vacuously true and
    # reads as a confirmed observation, so a capture with none says so instead.
    if added_ids:
        answer = "yes" if all(not t for t in added_ids) else "no"
    else:
        answer = "n/a, none in this capture"
    print(f"  ADDED, definitionally unjoinable: {len(added_ids)}, all with empty trip_id: {answer}")
    # EITHER OF THESE WOULD BE A NEW NJ TRANSIT FACT, so each gets its own line
    # rather than being folded into the rate. An ADDED trip carrying a real
    # trip_id would mean extras are being published against the schedule after
    # all; an empty one on a SCHEDULED trip would mean a train claiming to be in
    # the schedule with no way to say which.
    named_added = sorted({t for t in added_ids if t})[:5]
    if named_added:
        print(
            f"  NOTE: {len(named_added)} ADDED trip(s) carry a NONEMPTY trip_id "
            f"({named_added}). Every ADDED trip in the capture that exposed this had an "
            "empty one; a named extra is a new fact and may mean they can now be joined."
        )
    # A THIRD NEW FACT, and the one with a rider-visible cost. Once trip_id is empty
    # the entity.id is the only identity an extra has, so two extras sharing one
    # would be two trains the decoder can only tell apart by feed position. It
    # separates and warns rather than collapsing them, but a capture that contains
    # the shape should say so out loud rather than leave it to a golden assertion.
    if shapes["added_trips"] != shapes["added_distinct_entity_ids"]:
        print(
            f"  NOTE: {shapes['added_trips']} ADDED trips carry only "
            f"{shapes['added_distinct_entity_ids']} distinct entity.id values, so some "
            "extras share the only identity they have. GTFS-RT requires FeedEntity.id to "
            "be unique within a message; this capture would be the first counterexample."
        )
    unnamed_scheduled = sum(1 for t in claim_scheduled if not t)
    if unnamed_scheduled:
        print(
            f"  NOTE: {unnamed_scheduled} SCHEDULED-or-CANCELED trip(s) carry an EMPTY "
            "trip_id, which is a new fact: they claim to be in the schedule and give no "
            "way to say which trip. They are counted as unjoined below."
        )
    if claim_scheduled and join_rate < MIN_LIVE_JOIN_RATE:
        missing = sorted({t for t in claim_scheduled if t not in _TRIPS})[:5]
        print(
            f"\n  !! MEASURED join rate {join_rate:.4f} against the FULL live publication "
            f"({joined_trips} of {len(claim_scheduled)} SCHEDULED-or-CANCELED trip_updates "
            f"matched a trip_id in the {len(_TRIPS)}-trip trips.txt downloaded in this same "
            f"run), below the {MIN_LIVE_JOIN_RATE:.2f} floor. The {len(added_ids)} ADDED "
            "trips are excluded from this denominator by definition.\n"
            f"     unmatched trip_ids, first few: {missing}\n"
            "     This is the realtime feed and the static archive disagreeing about what is\n"
            "     scheduled. Possible causes, none of them measured here: a publication\n"
            "     boundary crossed between the two downloads; a schedule rollover in\n"
            "     progress; or the realtime feed running against a publication the archive\n"
            "     endpoint has not caught up to. Re-run in a few minutes before concluding\n"
            "     anything: the two downloads are seconds apart and a boundary between them\n"
            "     is the cheapest explanation to rule out."
        )
        return 1

    if problems:
        print("\nFEED DRIFT OR AN UNUSABLE CAPTURE, fixtures NOT written:")
        for problem in problems:
            print(f"  !! {problem}")
        return 1

    # THE GOLDEN IS FROZEN AT THE FEED'S OWN HEADER TIMESTAMP rather than at
    # wall-clock now. Every window in the decoder (the just-passed grace, the
    # future-first-stop ceiling, the dwell test) is relative to `now`, so a golden
    # decoded at capture time and re-decoded at test time would differ for a
    # reason that has nothing to do with the code. The header timestamp is the one
    # instant that travels with the bytes. The decode itself happens after the
    # re-trim below, against the tables actually written.
    now = shapes["header_timestamp"]

    # --- THE RE-TRIM: make the committed pair join BY CONSTRUCTION ------------
    #
    # The static fixture is rewritten here, from the SAME archive this run
    # downloaded, trimmed to the must-include set UNION every trip the realtime
    # capture contains. That is what turns "does the pair join" from a property of
    # the capture hour into a property of the writing step.
    #
    # WITHOUT IT the two fixtures are chosen by unrelated rules: the static keeps
    # two lexicographically-first trips per route, the realtime keeps whatever was
    # moving. Those sets intersect only by luck, and at 17:20 they did not
    # intersect at all.
    raw_members = _raw_static_rows(archive)
    static_trips = raw_members["trips.txt"][1]
    static_stops = raw_members["stops.txt"][1]
    calls: dict[str, list[dict]] = defaultdict(list)
    for row in raw_members["stop_times.txt"][1]:
        calls[trim.get(row, "trip_id")].append(row)
    for trip_calls in calls.values():
        trip_calls.sort(key=lambda r: int(trim.get(r, "stop_sequence") or 0))
    trips_by_route: dict[str, list[dict]] = defaultdict(list)
    for row in static_trips:
        trips_by_route[trim.get(row, "route_id")].append(row)

    pj_trips = trim.port_jervis_trips(static_trips, calls)
    pj_headsigned = {
        trim.get(t, "trip_id")
        for t in static_trips
        if "port jervis" in trim.get(t, "trip_headsign").lower()
    }
    west_of_hudson = trim.west_of_hudson_stops(pj_trips, calls)
    route_of_trip = {trim.get(t, "trip_id"): trim.get(t, "route_id") for t in static_trips}
    pj_keep, pj_uncovered = trim.select_port_jervis_trips(
        pj_trips, pj_headsigned, calls, west_of_hudson, route_of_trip
    )
    if pj_uncovered:
        print(
            f"  NOTE: {len(pj_uncovered)} west-of-Hudson station(s) are served by no Port "
            f"Jervis trip in this publication: {sorted(pj_uncovered)}. The mandated-stop "
            "top-up still puts their rows in the fixture."
        )
    pasc_trio = sorted(
        trim.route_exclusive_stops(route_of_trip, calls, "13"),
        key=lambda sid: (int(sid) if sid.isdigit() else 0, sid),
    )[: trim.PASC_TRIO]

    kept_trip_ids = trim.select_trim(
        trips=static_trips,
        calls=calls,
        trips_by_route=trips_by_route,
        pj_keep=pj_keep,
        mandated_stops=list(trim.IDENTITY_STOPS) + sorted(west_of_hudson) + pasc_trio,
        extra_trip_ids=set(live_trip_ids),
    )
    kept_trips, kept_stops, kept_stop_times = trim.apply_trim(
        trips=static_trips, stops=static_stops, calls=calls, kept_trip_ids=kept_trip_ids
    )

    # The pair must now join by construction. Asserted rather than assumed,
    # because "by construction" is a claim about code that can stop being true.
    kept_ids = {trim.get(t, "trip_id") for t in kept_trips}
    unjoined = sorted({t for t in live_trip_ids if t in _TRIPS and t not in kept_ids})
    if unjoined:
        print(
            f"\n  !! the re-trim kept {len(kept_ids)} trips but {len(unjoined)} trip_ids in the "
            f"capture that DO exist in this publication are not among them: {unjoined[:5]}. "
            "select_trim's extra_trip_ids is not doing its job; the pair would not join."
        )
        return 1

    kept_stop_ids = {trim.get(s_, "stop_id") for s_ in kept_stops}
    missing_woh = sorted(west_of_hudson - kept_stop_ids)
    if missing_woh:
        print(
            f"\n  !! the re-trim drops west-of-Hudson stations {missing_woh}. The coverage "
            "selection and the mandated-stop top-up are both supposed to prevent this; the "
            "static fixture would violate its own mandate."
        )
        return 1

    print(
        f"\nre-trimmed the static fixture around this capture: {len(kept_trips)} of "
        f"{len(static_trips)} trips, {len(kept_stops)} of {len(static_stops)} stops, "
        f"{len(kept_stop_times)} stop_times rows, all {len(west_of_hudson)} west-of-Hudson "
        "stations kept"
    )
    trim.write_fixture(
        STATIC_OUT_DIR,
        {
            "agency.txt": raw_members["agency.txt"],
            "routes.txt": raw_members["routes.txt"],
            "calendar_dates.txt": raw_members["calendar_dates.txt"],
            "stops.txt": (raw_members["stops.txt"][0], kept_stops),
            "trips.txt": (raw_members["trips.txt"][0], kept_trips),
            "stop_times.txt": (raw_members["stop_times.txt"][0], kept_stop_times),
        },
    )

    # THE GOLDEN IS DECODED AGAINST THE TRIM THAT WAS JUST WRITTEN, not against the
    # full publication, because the trim is what the tests will read. Re-decoding
    # here is what makes the committed expected-output correct for the committed
    # pair rather than for a table the repository does not contain.
    trimmed_parsed = njt_static._parse_zip(
        _zip_members(
            {
                name: (raw_members[name][0], rows)
                for name, rows in (
                    ("agency.txt", raw_members["agency.txt"][1]),
                    ("routes.txt", raw_members["routes.txt"][1]),
                    ("calendar_dates.txt", raw_members["calendar_dates.txt"][1]),
                    ("stops.txt", kept_stops),
                    ("trips.txt", kept_trips),
                    ("stop_times.txt", kept_stop_times),
                )
            }
        )
    )
    trimmed_trips = njt_static.build_njt_trip_index(trimmed_parsed["trips"])
    trains, arrivals, feed_ts, warnings = njt_feed.decode_njt_trip_updates(
        tu_raw, trimmed_parsed["stops"], trimmed_trips, now
    )
    measured_join = (
        sum(1 for t in live_trip_ids if t in trimmed_trips) / len(live_trip_ids)
        if live_trip_ids
        else 0.0
    )
    print(
        f"  against the written trim: {len(trains)} trains placed, join rate "
        f"{measured_join:.4f} (the goldens assert a floor under this)"
    )

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
