"""Golden tests: a real captured NJ Transit realtime feed decodes to stable output.

WHAT THESE ADD OVER test_njt_rt.py, which is the question worth answering before
adding any golden at all. That module builds every trap shape synthetically and
pins each rule of the decoder law against it, which is the right way to test a
rule: a live capture cannot be made to contain a cancellation on demand, so
waiting for one would leave the phantom rules untested. What synthetic protobufs
CANNOT do is tell you the real feed still looks the way the probe said it did.
These goldens are that half, and only that half:

  * the trap shapes are PRESENT in real bytes, at real rates
  * the entity.id / trip_short_name join the decoder is built on still holds
  * the decode of those bytes has not silently changed

CAPTURED WITH THE STATIC FIXTURE THEY JOIN, and decoded at the feed's OWN header
timestamp rather than at wall-clock now. Every window in the decoder (the
just-passed grace, the future-first-stop ceiling, the dwell test) is relative to
`now`, so a golden decoded at capture time and re-decoded at test time would
differ for a reason that has nothing to do with the code. The header timestamp is
the one instant that travels with the bytes; gen_njt_rt_fixture.py records it.

    python backend/scripts/gen_njt_rt_fixture.py   # needs NJT_USERNAME/NJT_PASSWORD

THESE FAIL IN CI UNTIL THE CAPTURE IS COMMITTED, and that is the point rather than
an oversight. conftest.golden_fixture_guard skips locally (where the developer may
have no credentials) and FAILS under CI=true, because 13a and 13b both merged green
while ten goldens were dormant: a skip is invisible in a passing summary line. NJ
Transit's is the one feed in this repo that cannot be captured without an account,
so this is a standing handoff carried in the PR body, exactly as 15a's static
fixture was.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from conftest import golden_fixture_guard
from feeds import njt

FIXTURES = Path(__file__).parent / "fixtures"
TU = FIXTURES / "njt_tu.pb"
EXPECTED = FIXTURES / "njt_tu_expected.json"
STATIC_DIR = FIXTURES / "njt_gtfs"

golden = golden_fixture_guard(
    TU,
    "backend/scripts/gen_njt_rt_fixture.py (needs NJT_USERNAME and NJT_PASSWORD)",
)


def _expected() -> dict:
    return json.loads(EXPECTED.read_text())


@golden
def test_golden_the_capture_carries_the_trap_shapes_the_law_is_written_about():
    """A capture with no cancellations and no skips is legal, useless, and the
    likeliest way this fixture rots: it would assert that a feed containing none of
    the failure shapes decodes without dropping anything.

    The generator refuses to WRITE such a capture. This is the same claim asserted
    where a reader will meet it, so a fixture hand-copied into place past the
    generator still fails here.
    """
    shapes = _expected()["capture_shapes"]
    assert shapes["canceled_trips"] >= 1, (
        "no trip-level CANCELED trip in the capture, so decoder law 1 is pinned only "
        "by synthetic bytes; recapture at rush hour"
    )
    assert shapes["skipped_with_times"] >= 1, "decoder law 2's with-times variant (238 at peak)"
    assert shapes["skipped_bare"] >= 1, "decoder law 2's bare variant (35 at peak)"
    # Not a rush-hour capture is a real risk and a quiet one: the probe's own words
    # are that "the overnight numbers are optimistic by roughly 2x".
    assert shapes["trips"] >= 200, (
        f"only {shapes['trips']} trips in the capture; this looks off-peak, and the "
        "shapes these goldens are about only appear under load"
    )


@golden
def test_golden_entity_id_still_equals_trip_short_name():
    """THE JOIN THE DECODER IS BUILT ON. The probe measured 745 of 745 agreement,
    which is why feeds.njt._identity treats this as a cross-check rather than as a
    matcher: PATH synthesizes identity because its bridge churns ids, and inventing
    a matcher for a feed that does not need one would add a failure mode.

    If this ever goes red the right response is a human deciding which side moved,
    not a relaxed threshold.
    """
    assert _expected()["capture_shapes"]["cross_check_agreement"] == 1.0


def _canceled_trip_ids(expected: dict) -> set[str]:
    """Trip ids the CAPTURE marks canceled, recorded by the generator off the wire
    rather than recomputed from the decode. Counting them with the code that is
    supposed to drop them would make a broken decoder look like a feed with no
    cancellations in it."""
    return set(expected["capture_shapes"].get("canceled_trip_ids") or [])


@golden
def test_golden_the_phantom_is_absent_from_the_real_decode():
    """THE CLAIM A RIDER WOULD FEEL, made against real bytes.

    The capture contains canceled trips carrying full times on stops they mark
    SKIPPED. No arrival anywhere in the decoded index may belong to one of them.

    THE NON-VACUITY IS ASSERTED FIRST, and that ordering is the point: "no canceled
    trip is on a board" is trivially true of a capture with no canceled trips and
    of a decode that produced no boards. Both are checked before the claim, so this
    cannot pass by having nothing to say.
    """
    expected = _expected()
    canceled = _canceled_trip_ids(expected)
    assert canceled, (
        "the generator recorded no canceled trip ids, so the claim below has nothing to "
        "exclude and would pass vacuously; recapture at rush hour"
    )
    arrivals = expected["arrivals"]
    assert arrivals, "positive control: a rush capture must produce arrivals somewhere"
    assert expected["trains"], "positive control: a rush capture must place trains"

    for stop_id, rows in arrivals.items():
        for row in rows:
            assert row["trip_id"] not in canceled, (
                f"a canceled trip reached stop {stop_id}'s board in the real decode"
            )
    placed = {t["trip_id"] for t in expected["trains"]}
    assert not (placed & canceled), "and no canceled trip was placed on the map either"


@golden
def test_golden_the_committed_pair_actually_joins():
    """THE MEASURED JOIN FLOOR, which is what the capture-script fix bought.

    The static and realtime fixtures used to be cut by unrelated rules: the static
    kept two lexicographically-first trips per route, the realtime kept whatever
    was moving. Those sets intersect only by luck, and on the first real capture
    attempt they did not intersect at all. gen_njt_rt_fixture.py now re-trims the
    static around the trips the capture contains, so the pair joins BY
    CONSTRUCTION, and a property that holds by construction is exactly the kind
    worth asserting: it can only break through a code change, and then it breaks
    loudly here.

    Without this the goldens below would still pass on a non-joining pair, just
    measuring synthesized display names instead of the static join.
    """
    expected = _expected()
    trains = expected["trains"]
    assert trains, "positive control"
    joined = [t for t in trains if t["joined"]] if "joined" in trains[0] else None
    if joined is None:
        # The decoder does not carry the flag onto the train dict; measure the same
        # thing from the trip index the fixture ships.
        import csv

        trips_txt = (STATIC_DIR / "trips.txt").read_text(encoding="utf-8-sig")
        known = {row["trip_id"] for row in csv.DictReader(io.StringIO(trips_txt))}
        joined = [t for t in trains if t["trip_id"] in known]
    rate = len(joined) / len(trains)
    assert rate >= 0.95, (
        f"only {len(joined)}/{len(trains)} placed trains join the committed static "
        f"({rate:.4f}). The pair is supposed to be coherent by construction; a low rate "
        "means the re-trim in gen_njt_rt_fixture.py stopped widening the trim around the "
        "capture, and every golden here is now measuring synthesized names."
    )


@golden
def test_golden_the_decode_is_stable():
    """Re-decoding the committed bytes at the committed instant reproduces the
    committed output exactly. The whole-output comparison, which is what makes an
    unintended decode change visible rather than only a rule regression."""
    import io
    import zipfile

    import njt_static

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted(STATIC_DIR.iterdir()):
            if path.suffix == ".txt":
                zf.writestr(path.name, path.read_text(encoding="utf-8"))
    parsed = njt_static._parse_zip(buffer)

    expected = _expected()
    trains, arrivals, feed_ts, warnings = njt.decode_njt_trip_updates(
        TU.read_bytes(),
        parsed["stops"],
        njt_static.build_njt_trip_index(parsed["trips"]),
        expected["now"],
    )
    assert warnings == expected["warnings"]
    assert feed_ts == expected["feed_timestamp"]
    assert trains == expected["trains"]
    assert arrivals == expected["arrivals"]
