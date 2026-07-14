"""Shared pure core for the routes-per-station index (H5).

Every static loader (subway, railroad, PATH, ferry) exposes which route ids
serve each stop, derived from the standard GTFS join
stop_times.trip_id -> trips.route_id. The fold itself is identical across
systems; what differs is the shape of each module's parsed tables and whether
child platform ids fold up to a parent station. So each static module keeps its
own thin, pure per-system derivation function (typed to its own parser output)
and delegates the common loop here, rather than four copies of it drifting apart.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping


def fold_stop_routes(
    trip_routes: Mapping[str, str | None],
    stop_times: Mapping[str, Iterable[str]],
    child_to_parent: Mapping[str, str] | None = None,
) -> dict[str, list[str]]:
    """stop_id -> sorted, de-duplicated [route_id] serving that stop.

    trip_routes: trip_id -> route_id. A blank/None route id contributes nothing
    (a trip with no route cannot say which route serves a stop).
    stop_times: trip_id -> the stop ids that trip visits. Order is irrelevant
    here (membership is all that matters), so an ordered list or a set both work.
    child_to_parent: optional child_stop_id -> parent_stop_id. When given, each
    stop id is folded up to its parent before indexing, so the index is keyed by
    the parent-station ids the markers and service alerts use (subway 101N ->
    101, PATH platforms -> station); an id with no mapping is kept as-is (already
    a parent, or a flat system like ferry/railroad).

    Pure: no zip read, no network, so a caller can build it from already-parsed
    app.state tables without re-parsing, exactly like build_*_route_shapes.
    """
    by_stop: dict[str, set[str]] = defaultdict(set)
    for trip_id, stop_ids in stop_times.items():
        route_id = trip_routes.get(trip_id)
        if not route_id:
            continue
        for stop_id in stop_ids:
            if child_to_parent:
                stop_id = child_to_parent.get(stop_id, stop_id)
            by_stop[stop_id].add(route_id)
    return {stop_id: sorted(routes) for stop_id, routes in by_stop.items()}
