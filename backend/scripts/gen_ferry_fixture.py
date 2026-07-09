#!/usr/bin/env python3
"""Generate the committed NYC Ferry GTFS test fixture (backend/tests/fixtures/ferry_gtfs/).

WHY a trimmed committed fixture (the gen_path_fixture pattern): the loader's
golden tests assert real-feed facts (50 stops, the 9 routes and their colors
and names, modal shape selection over the real variant spreads, the trip ->
route map 14b needs), so the fixture must be a captured slice of the real feed,
not handcrafted rows. The feed is small (~44 KB) but trips.txt is 723 rows, so
this trims it while provably preserving the properties under test.

TRIM RULE (the golden tests depend on it):
  - stops.txt and routes.txt are committed IN FULL: the stop count / wheelchair
    passthrough and the route/color/name table are asserted against the
    untrimmed truth.
  - trips.txt keeps, for each (route_id, direction_id): the 3
    lexicographically-first trips of the MODAL shape_id (most trips; ties to
    the smallest shape_id, the loader's exact rule) plus the 1
    lexicographically-first trip of EVERY other shape_id. Modal selection on
    the trimmed table therefore picks the same shape as on the full table
    (3 > 1) while every variant shape stays present to exercise the selection,
    and every route keeps at least one trip so the trip -> route golden covers
    all nine routes.
  - shapes.txt keeps only the shape_ids referenced by the kept trips, with
    their full point sets.

The script verifies the live feed still matches the facts probed 2026-07-07
(stop count, route set, colors, the named long_names, the multi-variant
spread) and that the trimmed trips reproduce the full table's modal picks; it
exits nonzero on any drift so a stale regeneration cannot slip in quietly.
Eyeball the printed tables against the golden test expectations before
committing, per house rules.

The utility URL 302-redirects to the resource zip; urllib.request follows
redirects by default (unlike httpx, which the loader configures explicitly).
Requires egress to nycferry.connexionz.net (a HANDOFF item: Ben runs this and
commits the goldens; CI stays red on the gated tests until then, by design).

Run:  python backend/scripts/gen_ferry_fixture.py
"""

from __future__ import annotations

import csv
import io
import ssl
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

SOURCE_URL = "http://nycferry.connexionz.net/rtt/public/utility/gtfs.aspx"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "ferry_gtfs"

# Facts probed live 2026-07-07; regeneration fails loudly if the feed drifts.
EXPECTED_STOPS = 50
EXPECTED_ROUTE_COLORS = {
    "AS": "FF6B00",
    "ER": "00839C",
    "GI": "9795A0",
    "RES": "00A1E1",
    "RR": "FF8672",
    "RS": "4E008E",
    "RWS": "00A1E1",
    "SB": "FFD100",
    "SG": "D0006F",
}
# The long_names the probe captured verbatim (RES/RS/RWS were listed by color
# only, so they are not asserted by name).
EXPECTED_ROUTE_NAMES = {
    "AS": "Astoria",
    "ER": "East River",
    "GI": "Governors Island Shuttle",
    "RR": "Rockaway",
    "SB": "South Brooklyn",
    "SG": "St. George",
}
EXPECTED_MULTI_VARIANT_ROUTES = 5  # routes with >1 shape in some (route, dir)
EXPECTED_MAX_VARIANTS = ("ER", 4)  # the deepest variant spread and its route

MODAL_KEEP = 3  # trips kept for the modal shape per (route, direction)
OTHER_KEEP = 1  # trips kept for every non-modal shape per (route, direction)


def _ssl_context() -> ssl.SSLContext:
    # Verify against certifi's CA bundle when present (matches the other
    # generators); ignored for the http:// URL, kept for a possible https move.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _download() -> zipfile.ZipFile:
    print(f"Downloading {SOURCE_URL} (following the 302 to the resource zip) ...")
    with urllib.request.urlopen(SOURCE_URL, timeout=120, context=_ssl_context()) as resp:  # noqa: S310
        raw = resp.read()
    print(f"  got {len(raw)} bytes from {resp.url}")
    return zipfile.ZipFile(io.BytesIO(raw))


def _read_rows(zf: zipfile.ZipFile, name: str) -> tuple[list[str], list[dict]]:
    with zf.open(name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
        return list(reader.fieldnames or []), list(reader)


def _write_rows(name: str, fieldnames: list[str], rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / name
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in fieldnames})
    print(f"  wrote {out.relative_to(REPO_ROOT)} ({len(rows)} rows)")


def _modal_picks(trips: list[dict]) -> dict[tuple[str, str], str]:
    """(route_id, direction_id) -> modal shape_id, the loader's exact rule:
    most trips, ties to the smallest shape_id."""
    counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for t in trips:
        rid = (t.get("route_id") or "").strip()
        sid = (t.get("shape_id") or "").strip()
        if rid and sid:
            counts[(rid, (t.get("direction_id") or "").strip())][sid] += 1
    return {key: min(tally, key=lambda s: (-tally[s], s)) for key, tally in counts.items()}


def main() -> int:
    zf = _download()
    problems: list[str] = []

    stops_cols, stops = _read_rows(zf, "stops.txt")
    routes_cols, routes = _read_rows(zf, "routes.txt")
    trips_cols, trips = _read_rows(zf, "trips.txt")
    shapes_cols, shapes = _read_rows(zf, "shapes.txt")

    # Stops (flat: every row with a usable id and coord is a marker).
    accessible = [s for s in stops if (s.get("wheelchair_boarding") or "").strip() == "1"]
    print(f"\nstops.txt: {len(stops)} rows, {len(accessible)} wheelchair-accessible")
    if len(stops) != EXPECTED_STOPS:
        problems.append(f"expected {EXPECTED_STOPS} stops, got {len(stops)}")
    if "wheelchair_boarding" not in stops_cols:
        problems.append("stops.txt has no wheelchair_boarding column")
    if not accessible:
        problems.append("no stop carries wheelchair_boarding=1 (passthrough would be untested)")

    # Routes (id, long_name, colors).
    colors = {
        (r.get("route_id") or "").strip(): (r.get("route_color") or "").strip().upper()
        for r in routes
    }
    names = {
        (r.get("route_id") or "").strip(): (r.get("route_long_name") or "").strip() for r in routes
    }
    print(f"\nroutes.txt: {len(routes)} rows")
    for rid in sorted(colors):
        print(f"  route {rid}: color {colors[rid]} name {names.get(rid)!r}")
    if colors != EXPECTED_ROUTE_COLORS:
        problems.append(f"route/color drift: expected {EXPECTED_ROUTE_COLORS}, got {colors}")
    for rid, name in EXPECTED_ROUTE_NAMES.items():
        if names.get(rid) != name:
            problems.append(
                f"route {rid} long_name drift: expected {name!r}, got {names.get(rid)!r}"
            )

    # Every trip must resolve to a route (the 14b join): the realtime feed
    # carries empty route_id, so a blank static route_id would make a train
    # unroutable.
    trip_routes = {
        (t.get("trip_id") or "").strip(): (t.get("route_id") or "").strip() for t in trips
    }
    blank_route = [tid for tid, rid in trip_routes.items() if tid and not rid]
    if blank_route:
        problems.append(
            f"{len(blank_route)} trips have a blank route_id (14b could not route them)"
        )

    shape_ids = {(s.get("shape_id") or "").strip() for s in shapes}
    referenced = {(t.get("shape_id") or "").strip() for t in trips} - {""}
    unresolved = referenced - shape_ids
    if unresolved:
        problems.append(f"trip shape_ids missing from shapes.txt: {sorted(unresolved)}")

    # Modal shape per (route, direction) + the variant spread.
    by_group: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for t in trips:
        rid = (t.get("route_id") or "").strip()
        sid = (t.get("shape_id") or "").strip()
        if rid and sid:
            by_group[(rid, (t.get("direction_id") or "").strip())][sid].append(t)
    full_modal = _modal_picks(trips)
    print("\nmodal shape per (route, direction), full-table counts:")
    multi_variant_routes: set[str] = set()
    max_variants = (None, 0)
    for (rid, direction), by_shape in sorted(by_group.items()):
        modal = full_modal[(rid, direction)]
        n = len(by_shape)
        print(
            f"  route {rid} dir {direction or '(blank)'}: modal {modal} "
            f"({len(by_shape[modal])} trips, {n} variant shapes)"
        )
        if n > 1:
            multi_variant_routes.add(rid)
        if n > max_variants[1]:
            max_variants = (rid, n)
    if len(multi_variant_routes) != EXPECTED_MULTI_VARIANT_ROUTES:
        problems.append(
            f"expected {EXPECTED_MULTI_VARIANT_ROUTES} multi-variant routes, "
            f"got {len(multi_variant_routes)}: {sorted(multi_variant_routes)}"
        )
    if (max_variants[0], max_variants[1]) != EXPECTED_MAX_VARIANTS:
        problems.append(
            f"deepest variant spread drift: expected {EXPECTED_MAX_VARIANTS}, got {max_variants}"
        )

    print("\ntrip -> route sample (the 14b join key):")
    for tid in sorted(trip_routes)[:5]:
        print(f"  trip {tid} -> route {trip_routes[tid]}")

    # Trim trips per the rule in the module docstring.
    kept_ids: set[str] = set()
    kept_trips: list[dict] = []
    for (rid, direction), by_shape in sorted(by_group.items()):
        modal = full_modal[(rid, direction)]
        for sid, group in sorted(by_shape.items()):
            keep = MODAL_KEEP if sid == modal else OTHER_KEEP
            for t in sorted(group, key=lambda t: t["trip_id"])[:keep]:
                if t["trip_id"] not in kept_ids:
                    kept_ids.add(t["trip_id"])
                    kept_trips.append(t)
    kept_trips.sort(key=lambda t: t["trip_id"])

    # The property the trim must preserve: identical modal picks.
    if _modal_picks(kept_trips) != full_modal:
        problems.append("trimmed trips change a modal pick; adjust MODAL_KEEP/OTHER_KEEP")
    # And every route must still be represented (the trip -> route golden).
    kept_routes = {(t.get("route_id") or "").strip() for t in kept_trips}
    if kept_routes != set(EXPECTED_ROUTE_COLORS):
        problems.append(f"trimmed trips drop a route: {set(EXPECTED_ROUTE_COLORS) - kept_routes}")

    kept_shape_ids = {(t.get("shape_id") or "").strip() for t in kept_trips} - {""}
    kept_shapes = [s for s in shapes if (s.get("shape_id") or "").strip() in kept_shape_ids]
    print(
        f"\ntrimmed: {len(kept_trips)} of {len(trips)} trips, "
        f"{len(kept_shapes)} of {len(shapes)} shape points ({len(kept_shape_ids)} shape ids)"
    )

    if problems:
        print("\nFEED DRIFT, fixture NOT written:")
        for p in problems:
            print(f"  !! {p}")
        return 1

    _write_rows("stops.txt", stops_cols, stops)
    _write_rows("routes.txt", routes_cols, routes)
    _write_rows("trips.txt", trips_cols, kept_trips)
    _write_rows("shapes.txt", shapes_cols, kept_shapes)
    print("\nEyeball the tables above against the golden test expectations before committing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
