"""One added-geometry dedup, shared by every loader that draws route lines.

WHY THIS IS A MODULE AND NOT A LOCAL HELPER. The rule below decides which of a
route's shape variants a rider actually sees, and getting it wrong is not a
rendering nicety: too strict and a branch disappears from the map (a line that
stops short of a real terminus, which reads as "service ends here"), too loose and
every route carries its own reverse-direction twin, doubling the geometry the
frontend glides against. Until 15c it lived inline in four loaders at once, each
with its own `_MIN_NEW_GEOMETRY = 0.05` and its own copy of the loop; NJ Transit
would have been a fifth, so the rule moved here and its callers import it.

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

from collections import defaultdict

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


def route_polylines(
    trips: dict[str, dict],
    shapes: dict[str, list],
    *,
    min_new: float = MIN_NEW_GEOMETRY,
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
    """
    polylines: dict[str, list[list]] = {}
    for route_id, shape_ids in sorted(shape_ids_by_route(trips).items()):
        # >= 2 points: a one-point "polyline" draws nothing and would still consume
        # a slot in the dedup, where its single point could suppress a real variant.
        variants = [pts for s in sorted(shape_ids) if len(pts := shapes.get(s) or []) >= 2]
        kept = keep_added_geometry(variants, min_new=min_new)
        if kept:
            polylines[route_id] = kept
    return polylines
