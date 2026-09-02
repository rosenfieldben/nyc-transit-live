"""Tests for the shared route geometry (backend/route_geometry.py).

TWO RULES LIVE HERE AND BOTH DECIDE WHAT A RIDER SEES, so they are tested at the
function boundary rather than only through a loader:

  - SIMPLIFICATION decides how much of a published line is committed and served. It
    is tested as PROPERTIES (a subset, endpoints kept, nothing moved further than
    the tolerance, idempotent) rather than as expected point lists, because a
    property survives a change of test data and an expected list only records what
    the code did on the day it was written.
  - THE DEDUP decides whether a branch appears at all. Its cases are drawn from the
    real feed: the Hoboken leg the old exact-point rule discarded, the
    reverse-direction twin that must still collapse, and two survey passes over one
    corridor that must read as one line.

The old exact-point rule (keep_added_geometry) is still tested here too, because
the railroads still use it and this phase deliberately did not switch them.
"""

from __future__ import annotations

import random
import sys

import pytest

import route_geometry as rg

# One step of divergence: far enough that the branch is unmistakably different
# geometry (more than twice COVER_DIST, which is about 280 m), and small enough that
# a three-step branch off a long trunk is still the SHORTER variant. That second
# half matters since 15c Part 1b: the dedup seeds on geometric length, so a test
# whose "branch" is longer than its trunk would be testing the opposite case.
_FAR = 0.006


def _line(start, end, count):
    """`count` points evenly spaced from start to end, inclusive."""
    (lat0, lon0), (lat1, lon1) = start, end
    return [
        [lat0 + (lat1 - lat0) * i / (count - 1), lon0 + (lon1 - lon0) * i / (count - 1)]
        for i in range(count)
    ]


def _indices_of_subsequence(output, source):
    """The indices in `source` that `output` came from, or None if it is not a
    subsequence. Identity-free: compares values, since simplification copies rows."""
    at, found = 0, []
    for point in output:
        while at < len(source) and source[at] != point:
            at += 1
        if at == len(source):
            return None
        found.append(at)
        at += 1
    return found


def _random_polyline(seed, count=400):
    """A wandering line with real curvature, which is what makes simplification
    interesting: a straight run would collapse to two points and prove little."""
    rnd = random.Random(seed)
    lat, lon, heading = 40.7, -74.0, rnd.uniform(0, 6.28)
    points = []
    for _ in range(count):
        heading += rnd.gauss(0, 0.15)
        lat += 0.0004 * rnd.uniform(0.5, 1.5)
        lon += 0.0004 * rnd.uniform(-1, 1) + 0.0002 * heading % 0.0004
        points.append([round(lat, 5), round(lon, 5)])
    return points


# ---------------------------------------------------------------------------
# Simplification, as properties
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(8))
def test_simplify_returns_a_subsequence_of_its_input(seed):
    points = _random_polyline(seed)
    simplified = rg.simplify_polyline(points, rg.NJT_SIMPLIFY_EPS)
    assert _indices_of_subsequence(simplified, points) is not None, (
        "simplification must never invent a coordinate the feed did not publish"
    )


@pytest.mark.parametrize("seed", range(8))
def test_simplify_always_keeps_both_endpoints(seed):
    points = _random_polyline(seed)
    simplified = rg.simplify_polyline(points, rg.NJT_SIMPLIFY_EPS)
    assert simplified[0] == points[0]
    assert simplified[-1] == points[-1]
    # THE ENDPOINTS ARE WHERE THE LINE REACHES, and the dedup's endpoint rule reads
    # them: a simplification that trimmed a terminus would delete the very evidence
    # that a branch goes somewhere new.


@pytest.mark.parametrize("seed", range(8))
def test_no_dropped_point_is_further_than_epsilon_from_the_line_that_replaces_it(seed):
    points = _random_polyline(seed)
    eps = rg.NJT_SIMPLIFY_EPS
    simplified = rg.simplify_polyline(points, eps)
    worst = max(rg.distance_to_polylines(point, [simplified]) for point in points)
    assert worst <= eps, f"a point moved {worst} from the drawn line, past the {eps} tolerance"


@pytest.mark.parametrize("seed", range(8))
def test_simplify_is_idempotent_at_one_epsilon(seed):
    """THE PROPERTY THE FIXTURE ARM RESTS ON. The committed geometry is the output
    of this function, and a golden asserts the loader's own simplification leaves it
    alone; that is only a meaningful statement if simplifying twice is simplifying
    once."""
    points = _random_polyline(seed)
    once = rg.simplify_polyline(points, rg.NJT_SIMPLIFY_EPS)
    assert rg.simplify_polyline(once, rg.NJT_SIMPLIFY_EPS) == once


def test_simplify_leaves_a_two_point_line_alone():
    line = [[40.7, -74.0], [40.8, -74.1]]
    assert rg.simplify_polyline(line, rg.NJT_SIMPLIFY_EPS) == line
    assert rg.simplify_polyline([], 0.001) == []


def test_simplify_drops_a_point_on_the_straight_line_between_its_neighbours():
    straight = _line((40.7, -74.0), (40.8, -74.0), 50)
    assert rg.simplify_polyline(straight, rg.NJT_SIMPLIFY_EPS) == [straight[0], straight[-1]]


def test_a_deflection_larger_than_the_tolerance_survives():
    """THE CONTROL for the test above: simplification that kept nothing but
    endpoints would also satisfy it."""
    bent = [[40.700, -74.000], [40.750, -74.010], [40.800, -74.000]]
    assert rg.simplify_polyline(bent, rg.NJT_SIMPLIFY_EPS) == bent


def test_simplify_survives_a_shape_far_larger_than_the_recursion_limit():
    """NJ Transit publishes shapes of 10,000 points and route 2's are 9,926 and
    9,719. The recursive form of this algorithm recurses once per retained point in
    the worst case; this one runs a zig-zag twice as long as that limit, every point
    of which must be retained, without a RecursionError."""
    points = [[40.7 + i * 0.001, -74.0 + (0.01 if i % 2 else 0.0)] for i in range(2_000)]
    assert len(points) > sys.getrecursionlimit(), (
        "the point of this test is a shape deeper than the recursion the naive form "
        "would need, so it has to be longer than the limit itself"
    )
    simplified = rg.simplify_polyline(points, rg.NJT_SIMPLIFY_EPS)
    assert len(simplified) == len(points)


# ---------------------------------------------------------------------------
# The distance dedup: the cases the real feed produced
# ---------------------------------------------------------------------------


def _trunk_and_branch(branch_points=3, trunk_points=100):
    """A long trunk and a branch that leaves it near the end for a distinct
    terminus. The branch is `branch_points` of `trunk_points`, so it is a few
    percent of the line: the shape of NJ Transit route 2's Hoboken leg, which is
    about 5 km of a 97 km line."""
    trunk = _line((40.70, -74.60), (40.75, -74.00), trunk_points)
    # The branch leaves the trunk EARLIER than its own length, so it is genuinely
    # the shorter variant. Equal lengths would make the longest-first ordering a
    # coin toss decided by input order, and the tie-break test below meaningless.
    shared = trunk[: trunk_points - 2 * branch_points]
    branch = shared + [
        [shared[-1][0] - _FAR * (i + 1), shared[-1][1] - _FAR * (i + 1)]
        for i in range(branch_points)
    ]
    return trunk, branch


def test_a_short_branch_to_its_own_terminus_survives():
    """DEFECT A, the reason this rule exists. Measured on the real feed: route 2
    publishes a shape to New York Penn and a shape to Hoboken, the Hoboken leg is
    ~5 km of a ~97 km line, and the fraction rule alone discarded it as a duplicate.
    Hoboken then sat 0.028 from the only line drawn, and the map showed a
    Montclair-Boonton line that never reached it."""
    trunk, branch = _trunk_and_branch(branch_points=3)
    # The premise: this branch is BELOW the fraction threshold, so the fraction arm
    # cannot be what keeps it. Without this the test would pass for the wrong reason.
    new_fraction = sum(
        1 for p in branch if rg.distance_to_polylines(p, [trunk]) > rg.COVER_DIST
    ) / len(branch)
    assert new_fraction < rg.MIN_NEW_GEOMETRY

    kept = rg.keep_distinct_variants([trunk, branch])
    assert len(kept) == 2, "a branch that reaches a terminus nothing else reaches was discarded"
    assert branch[-1] in [polyline[-1] for polyline in kept]

    # AND THE OLD RULE GETS IT WRONG, which is the finding rather than an aside.
    assert len(rg.keep_added_geometry([trunk, branch])) == 1


def test_a_branch_whose_own_terminus_is_its_START_survives_too():
    """THE OTHER HALF OF THE ENDPOINT ARM. Every other dedup case here puts the
    branch's unique terminus at the END, so dropping `not covered[0]` from the rule
    passed the whole suite: half the arm was unexercised. A published shape may run
    either way (NJ Transit publishes both directions of every line), so the start is
    not a hypothetical end."""
    trunk, branch = _trunk_and_branch(branch_points=3)
    reversed_branch = list(reversed(branch))
    assert rg.distance_to_polylines(reversed_branch[-1], [trunk]) <= rg.COVER_DIST
    assert rg.distance_to_polylines(reversed_branch[0], [trunk]) > rg.COVER_DIST
    kept = rg.keep_distinct_variants([trunk, reversed_branch])
    assert len(kept) == 2, "a branch reaching its own terminus at its START was discarded"


def test_the_fraction_arm_uses_a_strict_inequality_like_its_sibling():
    """`> min_new`, never `>=`, which route_geometry documents as deliberate for the
    old rule and which the new one inherited in code but not in tests. Built so the
    new fraction is EXACTLY the threshold: 1 of 20 points outside cover is 0.05, and
    both endpoints are covered so the endpoint arm cannot decide it."""
    # The trunk runs past the candidate at both ends, so it is unambiguously the
    # longer variant and seeds the comparison; the candidate's one detour would
    # otherwise make IT the longer of the two by a hair.
    trunk = _line((40.68, -74.00), (40.92, -74.00), 240)
    near = _line((40.70, -74.00), (40.90, -74.00), 20)
    # One interior point pushed just past cover, the rest (endpoints included) on it.
    near[10] = [near[10][0], near[10][1] - (rg.COVER_DIST * 1.5) / rg._COS_LAT]
    assert rg.polyline_length(trunk) > rg.polyline_length(near)
    outside = sum(1 for p in near if rg.distance_to_polylines(p, [trunk]) > rg.COVER_DIST)
    assert outside / len(near) == rg.MIN_NEW_GEOMETRY, "the premise: exactly at the line"
    assert rg.distance_to_polylines(near[0], [trunk]) <= rg.COVER_DIST
    assert rg.distance_to_polylines(near[-1], [trunk]) <= rg.COVER_DIST
    assert rg.keep_distinct_variants([trunk, near]) == [trunk], "exactly at is not MORE than"


def test_length_is_measured_in_the_isotropic_basis():
    """polyline_length decides which variant seeds the dedup, and a degree of
    longitude is shorter than a degree of latitude here. Without the scaling an
    east-west line reads longer than the north-south line that actually beats it,
    which silently reverses the seeding."""
    north = [[40.70, -74.00], [40.80, -74.00]]
    east = [[40.70, -74.00], [40.70, -73.88]]
    assert rg.polyline_length(north) == pytest.approx(0.10)
    assert rg.polyline_length(east) == pytest.approx(0.12 * rg._COS_LAT)
    # 0.12 degrees of longitude is SHORTER than 0.10 of latitude at this latitude,
    # so the scaling is what puts them in this order.
    assert rg.polyline_length(east) < rg.polyline_length(north)


def test_the_reverse_direction_twin_still_collapses():
    """The endpoint arm is not a licence to keep everything: a reversed shape's
    endpoints ARE the kept shape's endpoints, so it fails both arms."""
    trunk, _branch = _trunk_and_branch()
    assert rg.keep_distinct_variants([trunk, list(reversed(trunk))]) == [trunk]


def test_two_survey_passes_over_one_corridor_read_as_one_line():
    """The quieter half of the change. Two shape_ids tracing the same track share
    few exact points (different sampling, and after simplification almost none), so
    the exact-point rule called them distinct and drew the corridor twice. Offset
    here by less than COVER_DIST, and sampled at different intervals so no point is
    shared at all."""
    trunk = _line((40.70, -74.60), (40.75, -74.00), 100)
    offset = rg.COVER_DIST / 3
    resampled = [
        [lat + offset, lon + offset] for lat, lon in _line((40.70, -74.60), (40.75, -74.00), 61)
    ]
    assert not set(map(tuple, trunk)) & set(map(tuple, resampled)), "no exact point in common"
    assert len(rg.keep_distinct_variants([trunk, resampled])) == 1
    # THE OLD RULE KEEPS BOTH, drawing one corridor twice.
    assert len(rg.keep_added_geometry([trunk, resampled])) == 2


def test_a_variant_far_enough_along_its_length_survives_without_the_endpoint_arm():
    """The fraction arm still works: a branch that runs beside the trunk for a long
    way and rejoins it has both endpoints covered, so only its middle can save it."""
    trunk = _line((40.70, -74.60), (40.75, -74.00), 100)
    detour = trunk[:20] + [[lat + _FAR, lon] for lat, lon in trunk[20:80]] + trunk[80:]
    # THE PREMISE, and it has to be about the variant being JUDGED. detour shares
    # the trunk's first and last points, so asserting they are covered was asserting
    # 0.0 <= COVER_DIST, true whatever the code does. What matters is that the
    # detour is judged on its MIDDLE: both its ends are covered by the trunk, so the
    # endpoint arm cannot keep it and only the fraction arm can.
    index = rg._CoverIndex([trunk], rg.COVER_DIST)
    assert index.covers(detour[0]) and index.covers(detour[-1])
    outside = sum(1 for point in detour if not index.covers(point))
    assert outside / len(detour) > rg.MIN_NEW_GEOMETRY
    assert len(rg.keep_distinct_variants([trunk, detour])) == 2


def test_the_first_variant_is_always_kept():
    trunk = _line((40.70, -74.60), (40.75, -74.00), 10)
    assert rg.keep_distinct_variants([trunk]) == [trunk]
    assert rg.keep_distinct_variants([]) == []


def test_the_longest_variant_seeds_the_comparison_whatever_the_input_order():
    """Order matters to the arithmetic, so it is fixed rather than incidental: the
    trunk is kept first and every branch is measured against a line that exists."""
    trunk, branch = _trunk_and_branch(branch_points=3)
    assert rg.polyline_length(trunk) > rg.polyline_length(branch)
    assert rg.keep_distinct_variants([branch, trunk])[0] is trunk
    assert rg.keep_distinct_variants([trunk, branch])[0] is trunk


def test_ordering_is_by_length_because_point_count_stopped_meaning_length():
    """A 15c Part 1b correction, with the case that forced it.

    keep_added_geometry orders by POINT COUNT and calls it "the trunk is the longest
    variant". That held while every variant was raw points at one spacing. It stops
    holding the moment simplification runs first, because point count then measures
    CURVATURE: a long straight trunk simplifies to two points, while a short
    switchback beside it keeps forty. Seeded on count, the switchback goes first,
    the trunk then reads as almost entirely new, and BOTH are kept: the map draws
    the switchback on top of the line that already contains it.
    """
    trunk = [[40.70, -74.00], [41.30, -74.00]]
    switchback = [
        [40.72 + i * 0.002, -74.00 + (0.002 if i % 2 else -0.002) / rg._COS_LAT] for i in range(41)
    ]
    # The premise, so this cannot pass for the wrong reason: the switchback has far
    # more points, is far shorter, and never leaves the trunk's cover.
    assert len(switchback) > len(trunk)
    assert rg.polyline_length(switchback) < rg.polyline_length(trunk)
    assert max(rg.distance_to_polylines(p, [trunk]) for p in switchback) <= rg.COVER_DIST

    assert rg.keep_distinct_variants([trunk, switchback]) == [trunk]
    assert rg.keep_distinct_variants([switchback, trunk]) == [trunk]


def _keep_by_plain_scan(variants, cover=None, min_new=None):
    """keep_distinct_variants written the obvious way, measuring every distance.

    The reference the indexed implementation is checked against. Kept in the tests
    rather than in production precisely because it is the slow one: four variants of
    2,000 points cost 13 seconds this way and 39 ms through the index."""
    cover = rg.COVER_DIST if cover is None else cover
    min_new = rg.MIN_NEW_GEOMETRY if min_new is None else min_new
    kept = []
    for polyline in sorted(variants, key=rg.polyline_length, reverse=True):
        if not kept:
            kept.append(polyline)
            continue
        distances = [rg.distance_to_polylines(point, kept) for point in polyline]
        fraction = sum(1 for d in distances if d > cover) / len(distances)
        if fraction > min_new or distances[0] > cover or distances[-1] > cover:
            kept.append(polyline)
    return kept


@pytest.mark.parametrize("seed", range(12))
def test_the_cover_index_answers_exactly_what_a_plain_scan_would(seed):
    """THE CLAIM THE OPTIMISATION RESTS ON. The dedup asks "is this point already
    covered" once per point of every candidate, and a plain scan of every segment is
    quadratic: measured at 13 SECONDS for four variants of 2,000 points, on the event
    loop, inside a warmup. The grid index answers in 39 ms, and it is only allowed to
    do that if it answers the SAME. Random variant sets, sizes and counts, compared
    against the scan itself rather than against a remembered result."""
    rnd = random.Random(seed)
    variants = [
        _random_polyline(seed * 10 + i, rnd.randint(3, 80)) for i in range(rnd.randint(1, 5))
    ]
    assert rg.keep_distinct_variants(variants) == _keep_by_plain_scan(variants)


def test_the_cover_index_agrees_at_the_boundary_it_is_built_around():
    """Points placed exactly at, just inside and just outside COVER_DIST, which is
    also the index's cell size: the one place a grid is most likely to disagree with
    a scan is the edge of its own cells."""
    trunk = _line((40.70, -74.00), (40.90, -74.00), 40)
    for offset in (
        rg.COVER_DIST * 0.5,
        rg.COVER_DIST * 0.99,
        rg.COVER_DIST * 1.01,
        rg.COVER_DIST * 2,
    ):
        near = [[lat, lon + offset / rg._COS_LAT] for lat, lon in trunk]
        assert rg.keep_distinct_variants([trunk, near]) == _keep_by_plain_scan([trunk, near]), (
            f"index and scan disagree at an offset of {offset}"
        )


# ---------------------------------------------------------------------------
# The measure the dedup and the station golden share
# ---------------------------------------------------------------------------


def test_distance_is_to_the_segment_not_to_the_infinite_line():
    """Bay Head is beyond Long Branch, and an unclamped projection would call it
    covered by the line that stops at Long Branch."""
    segment = [[40.70, -74.00], [40.71, -74.00]]
    beyond = [40.80, -74.00]
    assert rg.distance_to_polylines(beyond, [segment]) == pytest.approx(0.09, abs=1e-6)


def test_longitude_is_scaled_so_one_tolerance_means_one_distance():
    """A degree of longitude is shorter than a degree of latitude at this latitude,
    and a scalar tolerance is meaningless unless the two are comparable."""
    north = rg.point_to_segment_distance([40.71, -74.00], [40.70, -74.00], [40.70, -74.00])
    east = rg.point_to_segment_distance([40.70, -73.99], [40.70, -74.00], [40.70, -74.00])
    assert east < north
    assert east == pytest.approx(0.01 * rg._COS_LAT)


def test_distance_to_nothing_is_infinite_not_zero():
    """The callers ask "is this point already covered", and with nothing kept the
    answer is no. A 0.0 here would say the opposite and drop every variant."""
    assert rg.distance_to_polylines([40.7, -74.0], []) == float("inf")
