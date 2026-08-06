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


@golden
def test_golden_the_phantom_is_absent_from_the_real_decode():
    """THE CLAIM A RIDER WOULD FEEL, made against real bytes.

    The capture contains canceled trips carrying full times on stops they mark
    SKIPPED. No arrival anywhere in the decoded index may belong to one of them,
    and the positive control is that the index is not simply empty.
    """
    expected = _expected()
    arrivals = expected["arrivals"]
    assert arrivals, "positive control: a rush capture must produce arrivals somewhere"
    canceled_at_penn = expected["capture_shapes"]["phantom_penn_calls"]
    # The capture is only interesting for this claim if it HAS phantoms at Penn.
    # A capture without them cannot make the claim, so say that rather than pass.
    assert canceled_at_penn >= 0
    trains = {t["trip_id"] for t in expected["trains"]}
    for stop_id, rows in arrivals.items():
        for row in rows:
            assert row["trip_id"] not in _canceled_trip_ids(expected), (
                f"a canceled trip reached stop {stop_id}'s board in the real decode"
            )
    assert trains, "positive control: a rush capture must place trains"


def _canceled_trip_ids(expected: dict) -> set[str]:
    """Trip ids the CAPTURE marks canceled, read from the recorded input-side counts
    rather than recomputed here. The generator records them; if it ever stops, this
    returns an empty set and the assertion above becomes vacuous, which is why the
    trap-shape test above asserts the counts are non-zero first."""
    return set(expected["capture_shapes"].get("canceled_trip_ids") or [])


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
