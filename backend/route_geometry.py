"""The geometry decisions every loader that draws route lines has to make.

WHAT IS HERE AND WHY IT IS SHARED. Three things, and each one decides something a
rider sees:

  * ONE DISTANCE MEASURE, in an isotropic degree basis, used by the dedup below and
    by the goldens that ask whether a station stands on its own line. Two
    implementations of "how far apart is this" would eventually mean a tolerance
    enforced in one place and asserted in another.
  * SIMPLIFICATION (Douglas-Peucker), which decides how much of a published line is
    committed and served. NJ Transit publishes a point every 10 m.
  * TWO DEDUP RULES, which decide which of a route's shape variants a rider
    actually sees. Getting that wrong is not a rendering nicety: too strict and a
    branch disappears from the map (a line that stops short of a real terminus,
    which reads as "service ends here"), too loose and every route carries its own
    reverse-direction twin, doubling the geometry the frontend glides against.

WHY TWO DEDUP RULES RATHER THAN ONE. keep_added_geometry is the original
exact-point rule, and the railroads still use it. keep_distinct_variants is the
distance rule 15c added after the real NJ Transit feed showed the first one
discarding a branch that reaches a different terminus (see there). The railroads
are NOT switched in this phase: the delta that change would make is measured and
recorded in the 15c PR body, and it is a decision for the phase that takes it, not
a side effect of this one.

Until 15c the exact rule lived inline in four loaders at once, each with its own
`_MIN_NEW_GEOMETRY = 0.05` and its own copy of the loop; NJ Transit would have been
a fifth, so it moved here and its callers import it.

WHAT IS DELIBERATELY NOT HERE, AND IT IS THREE MORE COPIES. path_static,
ferry_static and static_data each still carry their own `_MIN_NEW_GEOMETRY = 0.05`
and their own copy of the loop in keep_added_geometry. They are left alone in 15c
on purpose, because each differs in what it feeds the loop or what it emits, and
converting them is a change to three loaders that this phase has no reason to
touch:

  * path_static and ferry_static pick the MODAL shape per (route_id, direction_id)
    by trip count FIRST and run the dedup over those few survivors, since their
    variants are short-turn and track-work patterns rather than branches.
  * static_data's subway builder appends EVERY route even when the dedup keeps
    nothing, because a subway route with no drawable geometry still has trains to
    place; the two callers here drop such a route instead.

Recording them here rather than leaving the next reader to rediscover them: the
duplication is known, it is four copies and not two, and the shared inner rule is
now importable whenever one of those three is next opened for its own reasons.

Pure functions over already-parsed tables: no zip read, no network, no clock, so a
warmup can build route lines from what a load already parsed and a test can call
them with three hand-written points.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable

# Longitude is scaled by cos(latitude) so a degree east and a degree north are
# comparable distances, which is what makes a single scalar tolerance meaningful.
# One fixed factor at the city's latitude is plenty: the whole network spans about
# a degree, and frontend/helpers.js builds the same basis at its own _COS_LAT for
# the projection the rider actually sees. The two must agree, because a tolerance
# stated here and enforced there in a different basis would be two numbers wearing
# one name.
_COS_LAT = math.cos(math.radians(40.7))


def point_to_segment_distance(point: list[float], start: list[float], end: list[float]) -> float:
    """Distance from a point to a SEGMENT (not the infinite line it lies on), in the
    isotropic basis above.

    THE CLAMP IS THE WHOLE DIFFERENCE, and dropping it is a real defect rather than
    a rounding one: a station or a shape point beyond the end of a polyline would
    project onto the line's continuation and read as arbitrarily close to geometry
    that stops well short of it. Bay Head is on the far side of Long Branch, and an
    unclamped test would call it covered by the Long Branch line.
    """
    px, py = point[0], point[1] * _COS_LAT
    ax, ay = start[0], start[1] * _COS_LAT
    bx, by = end[0], end[1] * _COS_LAT
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_to_polylines(point: list[float], polylines: list[list]) -> float:
    """The distance from a point to the nearest part of any of `polylines`, or inf
    when there is nothing to measure against.

    inf FOR AN EMPTY SET rather than 0.0, because the callers ask "is this point
    already covered": with nothing kept yet, nothing is covered, and a 0.0 here
    would silently report the opposite.
    """
    best = math.inf
    for polyline in polylines:
        for start, end in zip(polyline, polyline[1:]):
            best = min(best, point_to_segment_distance(point, start, end))
    return best


# A shape variant is kept only if it adds MORE than this fraction of new geometry
# against the variants already kept for the same route. 0.05, the value the
# railroad builder and static_data's subway builder have both carried since they
# were written.
#
# WHY A FRACTION OF NEW POINTS AND NOT A DISTANCE. The variants of one route are
# overwhelmingly the same track sampled twice: express and local, the
# reverse-direction shape, a weekend working that skips two stops. Those share
# almost every point, so they clear no sensible threshold and collapse. A genuine
# branch (NJ Transit's North Jersey Coast splitting for Long Branch and Bay Head,
# Montclair-Boonton for Montclair State and Hackettstown, Metro-North's New Canaan
# and Danbury and Waterbury legs) contributes a run of points nothing else covers,
# which is exactly what this measures.
#
# THE POINT-SET TEST IS ORDER-INDEPENDENT, and that is load-bearing rather than
# incidental: a reversed shape is the same set of points in the opposite sequence,
# so it reads as 0% new and drops out without anyone having to detect reversal.
MIN_NEW_GEOMETRY = 0.05


# How far apart two pieces of geometry must be before they are DIFFERENT geometry,
# in the isotropic basis above. Tied to the frontend's RAILROAD_ROUTE_ACCEPT_DIST
# (frontend/helpers.js, 0.0025, about 280 m here) and deliberately the same number:
# that is the distance beyond which the app stops associating a train with a line
# at all, so two polylines closer than it everywhere are, to everything downstream,
# the same line drawn twice. Anything further apart is geometry a rider could tell
# apart on the map.
#
# It is also the digitization tolerance this needs. Two shape_ids tracing one
# corridor are rarely point-identical (different survey passes, different sampling,
# and after simplification different retained points), so an exact-coincidence test
# reads them as fully distinct while a rider sees one line.
COVER_DIST = 0.0025

# Decimal places every consumer of a shape point rounds to, about 1 m here. The
# subway, bus and railroad shape parses have always rounded to 5; this names the
# number so the FIXTURE ARM and the LOADER cannot drift apart on it.
#
# THEY DID DRIFT, AND IT WAS MEASURED. The trim simplified the publication's own
# full-precision text while the loader simplified its 5-decimal parse of the same
# rows. NJ Transit publishes 6 decimals (its stops are 40.750568), and a rounding
# shift of half a metre is enough to move a point across the keep/drop threshold:
# 82 of 300 synthetic 6-decimal publications came out NOT a fixed point of the
# loader's own simplification, which is exactly what the fixed-point golden exists
# to catch. Both sides now round through this constant before deciding anything.
COORD_PRECISION = 5

# Douglas-Peucker tolerance for NJ Transit geometry, in the same basis: no retained
# point moves the drawn line more than this from the published one.
#
# WHY THIS FEED NEEDS IT AND THE OTHERS DO NOT. NJ Transit publishes about a point
# every 10 m: 29 shapes came to 195,545 rows and 6.9 MB, against 216 KB for the
# whole PATH shapes.txt. That is a fixture nobody wants in git and a payload
# /api/njt-routes would send to every page load, for detail no map can show.
#
# WHY 0.0002 (about 22 m) AND NOT TIGHTER. Two reasons, one principled and one
# measured.
#
# Principled, and the claim is bounded rather than absolute. 0.0002 is an order of
# magnitude below COVER_DIST, and below the frontend's projection tolerance, which
# is the SAME 0.0025 (both trace to RAILROAD_ROUTE_ACCEPT_DIST). Simplification
# moves a point at most epsilon from the line drawn through it, which is 8% of that
# threshold, so it cannot on its own flip a point from covered to uncovered, nor
# strand a station off its route. A point already within 8% of the limit CAN be
# carried across it, which is why the station golden reports the WORST pair in the
# fixture rather than only whether every pair passed.
#
# AND ONE THING THAT ARGUMENT DOES NOT COVER, said plainly because it is easy to
# read too much into it: the dedup's fraction arm is a ratio over RETAINED POINTS,
# and simplification changes which points those are. Two variants can therefore
# yield a different fraction before and after simplification even where no point
# moved near a threshold, since the denominator itself changed. What is bounded is
# per-point coverage and station projection, not the ratio. The endpoint arm is
# unaffected either way, because endpoints are always retained.
#
# Measured, on 29 synthetic shapes built to the real feed's statistics (the model
# reproduces its row count to within 0.05%: 195,460 rows against the real 195,545):
# 0.0002 holds the fixture at roughly 1,900 rows whatever the digitization noise up
# to about 4 m, while 0.0001 collapses to 21,000 rows the moment that noise reaches
# 4 m. The tighter value buys detail no map draws and pays for it with a fixture
# whose size depends on how carefully somebody digitized a centreline. The full
# sweep is in the 15c PR body.
NJT_SIMPLIFY_EPS = 0.0002


def simplify_indices(points: list[list], epsilon: float) -> list[int]:
    """The indices Douglas-Peucker keeps, ascending. Endpoints are always among them.

    ITERATIVE, WITH AN EXPLICIT STACK, and that is a correctness requirement rather
    than a style preference: the recursive form recurses once per retained point in
    the worst case, and this runs over shapes of 14,000 points, which would trip
    CPython's recursion limit and take the whole load down with it.

    INDICES RATHER THAN POINTS, because two callers need different things from the
    same decision. The loader wants the surviving coordinates; the fixture trim
    wants the surviving ROWS, with the publication's own values and its own
    shape_pt_sequence numbers, and rebuilding those from floats would round-trip the
    text through a float and write back something the feed never said.
    """
    if len(points) <= 2:
        return list(range(len(points)))
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        farthest, farthest_at = -1.0, -1
        anchor_a, anchor_b = points[start], points[end]
        for index in range(start + 1, end):
            distance = point_to_segment_distance(points[index], anchor_a, anchor_b)
            if distance > farthest:
                farthest, farthest_at = distance, index
        # `>` not `>=`: a point exactly at the tolerance is within it, and the
        # guarantee this function makes is "no dropped point is FARTHER than
        # epsilon from the line that replaces it".
        if farthest > epsilon:
            keep[farthest_at] = True
            stack.append((start, farthest_at))
            stack.append((farthest_at, end))
    return [index for index, kept in enumerate(keep) if kept]


def simplify_polyline(points: list[list], epsilon: float) -> list[list]:
    """A SUBSET of `points`, in order, whose line stays within `epsilon` of theirs.

    A subset rather than a resampling: every point in the output is a point the feed
    published, so nothing here invents a coordinate NJ Transit never gave. That is
    what lets the fixture arm write the surviving rows verbatim and lets the fixture
    and production hold the same points rather than two roundings of them.

    IDEMPOTENT AT A FIXED EPSILON, which the fixture arm depends on: simplifying an
    already-simplified shape returns it unchanged, so the committed geometry is
    exactly what the loader would produce from it, and a golden can say so.
    """
    return [points[index] for index in simplify_indices(points, epsilon)]


def shape_ids_by_route(trips: dict[str, dict]) -> dict[str, set[str]]:
    """route_id -> the distinct shape_ids its trips use.

    VIA trips.txt RATHER THAN A shape_id REGEX, because outside the subway a
    shape_id encodes nothing: the railroad and NJ Transit both publish opaque ids
    whose only link to a route is the trip that references them. A trip missing
    either field contributes nothing rather than raising, since a feed is entitled
    to publish a trip with no shape and the alternative is a load that fails whole
    on one incomplete row.
    """
    grouped: dict[str, set[str]] = defaultdict(set)
    for trip in trips.values():
        route_id, shape_id = trip.get("route_id"), trip.get("shape_id")
        if route_id and shape_id:
            grouped[route_id].add(shape_id)
    return grouped


def keep_added_geometry(variants: list[list], *, min_new: float = MIN_NEW_GEOMETRY) -> list[list]:
    """The variants worth drawing, longest first, each adding new geometry.

    Longest first is what makes the result stable and the arithmetic meaningful:
    the trunk is the longest variant, so it is kept first and every branch is then
    measured against a route that already exists. Seeded the other way round, a
    short spur would be kept first and the trunk measured against it, which changes
    which variants survive.

    `> min_new`, never `>=`, so a variant that adds exactly nothing new (the
    reverse-direction twin, at 0.0) can never be kept by an equality that reads as
    a rounding accident.
    """
    ordered = sorted(variants, key=len, reverse=True)
    kept: list[list] = []
    covered: set[tuple] = set()
    for polyline in ordered:
        point_set = {tuple(point) for point in polyline}
        # max(..., 1) guards the division, not the emptiness: a zero-point variant
        # is already excluded by the caller's >= 2 filter, and a guard that can
        # never fire is a guard nobody can test.
        if len(point_set - covered) / max(len(point_set), 1) > min_new:
            kept.append(polyline)
            covered |= point_set
    return kept


def polyline_length(points: list[list]) -> float:
    """Total length of a polyline in the isotropic basis.

    ORDERING NEEDS THIS AND POINT COUNT WILL NOT DO, which is a 15c Part 1b
    correction rather than a preference. keep_added_geometry orders by point count
    and justifies it as "the trunk is the longest variant", which was true while
    every variant was raw points at one spacing: more points meant more line. 15c
    put simplification upstream and broke that, because point count now measures
    CURVATURE as much as length. A long straight trunk simplifies to two points,
    while a short switchback beside it keeps forty, so the short one seeded the
    comparison and the trunk then read as mostly new geometry: both were kept and
    the map drew the switchback on top of the line containing it. Ordering by the
    thing the justification actually names fixes it.
    """
    return sum(
        math.hypot(a[0] - b[0], (a[1] - b[1]) * _COS_LAT) for a, b in zip(points, points[1:])
    )


class _CoverIndex:
    """A yes/no answer to "is this point within `cover_dist` of geometry already
    kept", in constant time per query.

    WHY AN INDEX AND NOT THE PLAIN DISTANCE. The dedup asks that question once per
    point of every candidate, and answering it by scanning every segment of every
    kept polyline is quadratic: measured, four variants of 500 points cost 830 ms
    and of 2,000 points cost 13 SECONDS. build_njt_route_shapes runs inside the
    warmup coroutine, on the event loop, so those seconds are seconds the app serves
    nothing. Simplification keeps the realistic case small (about 66 points a shape
    on geometry modelled from the real feed), but "small on the data we modelled" is
    not a bound, and a curvier publication would find the cliff in production rather
    than here.

    IT IS EXACT, NOT AN APPROXIMATION, and the argument is short enough to check.
    Cells are `cover_dist` on a side. Every segment is first split into pieces no
    longer than one cell, and each piece is registered in every cell its bounding
    box touches (at most four). A query looks only at the 3x3 block of cells around
    the point. If some piece really is within cover_dist of the point, then a point
    of that piece lies within one cell-width in each axis, so it sits in that 3x3
    block, and the piece is registered there because the piece's bounding box
    contains it. So nothing within range can be missed, and the answer equals the
    scan's. A test asserts that equality on random geometry rather than trusting the
    paragraph.
    """

    def __init__(self, polylines: list[list], cover_dist: float) -> None:
        self._cell = cover_dist
        self._cover = cover_dist
        self._cells: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
        for polyline in polylines:
            for start, end in zip(polyline, polyline[1:]):
                self._add(start, end)

    def _add(self, start: list[float], end: list[float]) -> None:
        ax, ay = start[0], start[1] * _COS_LAT
        bx, by = end[0], end[1] * _COS_LAT
        length = math.hypot(bx - ax, by - ay)
        pieces = max(1, math.ceil(length / self._cell))
        for piece in range(pieces):
            t0, t1 = piece / pieces, (piece + 1) / pieces
            px0, py0 = ax + (bx - ax) * t0, ay + (by - ay) * t0
            px1, py1 = ax + (bx - ax) * t1, ay + (by - ay) * t1
            segment = (px0, py0, px1, py1)
            for cx in range(self._index(min(px0, px1)), self._index(max(px0, px1)) + 1):
                for cy in range(self._index(min(py0, py1)), self._index(max(py0, py1)) + 1):
                    self._cells.setdefault((cx, cy), []).append(segment)

    def _index(self, value: float) -> int:
        return math.floor(value / self._cell)

    def covers(self, point: list[float]) -> bool:
        px, py = point[0], point[1] * _COS_LAT
        cx, cy = self._index(px), self._index(py)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ax, ay, bx, by in self._cells.get((cx + dx, cy + dy), ()):
                    if _iso_point_to_segment(px, py, ax, ay, bx, by) <= self._cover:
                        return True
        return False


def _iso_point_to_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """point_to_segment_distance on coordinates already in the isotropic basis, so
    the index does not rescale longitude once per segment per query."""
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def keep_distinct_variants(
    variants: list[list],
    *,
    min_new: float = MIN_NEW_GEOMETRY,
    cover_dist: float = COVER_DIST,
) -> list[list]:
    """The variants worth drawing, by DISTANCE rather than by exact point identity.

    THE RULE keep_added_geometry GETS WRONG, measured on the real NJ Transit feed
    and the reason this exists. Route 2 publishes a shape to New York Penn (9,926
    points) and a shape to Hoboken (9,719). The Hoboken leg is about 5 km of a 97 km
    line, so under a fraction-only test it contributed at or under the 5% threshold
    and was discarded as a duplicate: the map drew a Montclair-Boonton line that
    never reached Hoboken, while Hoboken itself sat 0.028 away from the only line
    kept. Route 7 published the identical pattern. A branch that ends somewhere else
    is not a duplicate however short it is, which is exactly the failure this
    module's own docstring names first.

    SO THERE ARE TWO WAYS TO BE KEPT, and the second is the fix:

      * enough of the variant is new: more than `min_new` of its points sit farther
        than `cover_dist` from every polyline already kept, or
      * either ENDPOINT is farther than `cover_dist` from every polyline already
        kept, at any length. A terminus nothing else reaches is a terminus, and one
        that no line goes to reads to a rider as service that does not run.

    LONGEST FIRST, BY LENGTH RATHER THAN BY POINT COUNT, so the trunk seeds the
    comparison and every branch is measured against the line it branches from. That
    differs from keep_added_geometry deliberately: see polyline_length for the case
    where counting points draws a duplicate.

    THE REVERSE TWIN STILL DROPS, which is what makes the endpoint arm safe rather
    than a licence to keep everything: a reversed shape's endpoints ARE the kept
    shape's endpoints, at distance 0, and every point between them lies on it. The
    order-independence the old point-set test got for free is inherited here,
    because distance to a polyline does not care which way either line runs.

    DISTANCE, NOT COINCIDENCE, also fixes the quieter half: two shape_ids tracing
    one corridor from different survey passes share few exact points, and after
    simplification they share almost none, so an exact test would have called them
    distinct and drawn the corridor twice.
    """
    # BY LENGTH, NOT BY POINT COUNT (see polyline_length). The trunk has to seed the
    # comparison or every branch is measured against something that is not the line
    # it branches from, and after simplification point count no longer says which
    # variant is the trunk.
    ordered = sorted(variants, key=polyline_length, reverse=True)
    kept: list[list] = []
    for polyline in ordered:
        if not kept:
            kept.append(polyline)
            continue
        # One pass, both tests: the endpoint question is a special case of the same
        # per-point measurement, so it costs nothing extra to ask. Both are THRESHOLD
        # questions rather than distance questions, which is what lets the index
        # answer them without measuring anything exactly.
        index = _CoverIndex(kept, cover_dist)
        covered = [index.covers(point) for point in polyline]
        new_fraction = sum(1 for is_covered in covered if not is_covered) / len(covered)
        reaches_somewhere_new = not covered[0] or not covered[-1]
        if new_fraction > min_new or reaches_somewhere_new:
            kept.append(polyline)
    return kept


def route_polylines(
    trips: dict[str, dict],
    shapes: dict[str, list],
    *,
    select: Callable[[list[list]], list[list]] = keep_added_geometry,
) -> dict[str, list[list]]:
    """route_id -> its kept polylines, for every route that has drawable geometry.

    A route whose shapes are all missing, blank or degenerate is ABSENT from the
    result rather than present with an empty list. Both callers want that (each
    says so at its own emit site), and it keeps the empty case one decision made
    once by the caller instead of a None-or-empty ambiguity handed downstream.

    ITERATION IS SORTED AT BOTH LEVELS, and it is not cosmetic. Set iteration of
    shape_id strings is salted by PYTHONHASHSEED, and among equal-length variants
    the dedup keeps whichever it sees first, so an unsorted set could yield a
    different polyline order AND a different kept set from one process to the next.
    Two runs of the same publication must draw the same map.

    shapes.get(s) rather than shapes[s]: a trip may reference a shape_id that
    shapes.txt does not carry (NJ Transit's parse reads only the referenced ids, so
    a publication with a dangling reference lands here), and a missing shape is a
    variant that does not exist, never a KeyError mid-load.

    `select` IS THE DEDUP, INJECTED, and the default is the exact-point rule the
    railroads have always used. NJ Transit passes keep_distinct_variants instead,
    because its feed exposed a case the exact rule gets wrong (see there). The
    railroads keep the old rule in this phase deliberately rather than by
    oversight: switching them is a change to a working map that deserves its own
    measurement, and the 15c PR body carries the delta it would make.
    """
    polylines: dict[str, list[list]] = {}
    for route_id, shape_ids in sorted(shape_ids_by_route(trips).items()):
        # >= 2 points: a one-point "polyline" draws nothing and would still consume
        # a slot in the dedup, where its single point could suppress a real variant.
        variants = [pts for s in sorted(shape_ids) if len(pts := shapes.get(s) or []) >= 2]
        kept = select(variants)
        if kept:
            polylines[route_id] = kept
    return polylines
