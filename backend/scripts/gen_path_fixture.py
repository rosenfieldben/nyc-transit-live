#!/usr/bin/env python3
"""Generate the committed PATH GTFS test fixture (backend/tests/fixtures/path_gtfs/).

WHY a trimmed committed fixture instead of the synthetic-zip approach the
railroad tests use: the PATH loader's golden tests assert real-feed facts (13
parent stations, 51 child platforms, the 7 routes and their colors, modal
shape selection on real variant spreads like route 1024's 18 shape ids), so
the fixture must be a captured slice of the real feed, not handcrafted rows.
The full feed is too big to commit (trips.txt alone is ~22k rows), so this
script trims it while provably preserving the properties under test.

TRIM RULE (the golden tests depend on it):
  - stops.txt and routes.txt are committed IN FULL (64 and 7 rows): the
    parent/child counts and the route/color table are asserted against the
    untrimmed truth.
  - trips.txt keeps, for each (route_id, direction_id): the 3
    lexicographically-first trips of the MODAL shape_id (most trips; ties to
    the smallest shape_id, the same tie-break the loader uses) plus the 1
    lexicographically-first trip of EVERY other shape_id. Modal selection on
    the trimmed table therefore picks the same shape as on the full table
    (3 > 1) while every variant shape stays present to exercise the selection.
  - shapes.txt keeps only the shape_ids referenced by the kept trips, with
    their full point sets.

The script verifies the live feed still matches the facts checked on
2026-07-05 (counts, route set, colors, every trip shape_id resolving) and
that the trimmed tables reproduce the full tables' modal picks; it exits
nonzero on any drift so a stale regeneration cannot slip in quietly. Eyeball
the printed tables against the golden JSON expectations before committing,
per house rules.

Run:  python backend/scripts/gen_path_fixture.py
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

SOURCE_URL = "https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "path_gtfs"

# Facts verified live 2026-07-05; regeneration fails loudly if the feed drifts.
EXPECTED_PARENTS = 13
EXPECTED_CHILDREN = 51
EXPECTED_ROUTE_COLORS = {
    "859": "4d92fb",
    "860": "65c100",
    "861": "ff9900",
    "862": "d93a30",
    "1024": "ff9900",
    "74320": "8c3c96",
    "77285": "65c100",
}

MODAL_KEEP = 3  # trips kept for the modal shape per (route, direction)
OTHER_KEEP = 1  # trips kept for every non-modal shape per (route, direction)


def _ssl_context() -> ssl.SSLContext:
    # Verify against certifi's CA bundle when present (matches
    # gen_airtrain_fixture.py); fall back to the platform default elsewhere.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _download() -> zipfile.ZipFile:
    print(f"Downloading {SOURCE_URL} ...")
    with urllib.request.urlopen(SOURCE_URL, timeout=120, context=_ssl_context()) as resp:  # noqa: S310
        raw = resp.read()
    print(f"  got {len(raw)} bytes")
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

    parents = [s for s in stops if (s.get("location_type") or "").strip() == "1"]
    children = [s for s in stops if (s.get("parent_station") or "").strip()]
    print(f"\nstops.txt: {len(stops)} rows, {len(parents)} parents, {len(children)} children")
    for p in sorted(parents, key=lambda s: s["stop_id"]):
        print(f"  parent {p['stop_id']}: {p.get('stop_name', '').strip()}")
    if len(parents) != EXPECTED_PARENTS:
        problems.append(f"expected {EXPECTED_PARENTS} parents, got {len(parents)}")
    if len(children) != EXPECTED_CHILDREN:
        problems.append(f"expected {EXPECTED_CHILDREN} children, got {len(children)}")

    colors = {
        (r.get("route_id") or "").strip(): (r.get("route_color") or "").strip().lower()
        for r in routes
    }
    print(f"\nroutes.txt: {len(routes)} rows")
    for rid, color in sorted(colors.items()):
        print(f"  route {rid}: color {color}")
    if colors != EXPECTED_ROUTE_COLORS:
        problems.append(f"route/color drift: expected {EXPECTED_ROUTE_COLORS}, got {colors}")

    shape_ids = {(s.get("shape_id") or "").strip() for s in shapes}
    referenced = {(t.get("shape_id") or "").strip() for t in trips} - {""}
    unresolved = referenced - shape_ids
    if unresolved:
        problems.append(f"trip shape_ids missing from shapes.txt: {sorted(unresolved)}")

    # Trim trips per the rule in the module docstring.
    by_group: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for t in trips:
        rid = (t.get("route_id") or "").strip()
        sid = (t.get("shape_id") or "").strip()
        if rid and sid:
            by_group[(rid, (t.get("direction_id") or "").strip())][sid].append(t)
    full_modal = _modal_picks(trips)
    kept_trips: list[dict] = []
    print("\nmodal shape per (route, direction), full-table counts:")
    for (rid, direction), by_shape in sorted(by_group.items()):
        modal = full_modal[(rid, direction)]
        print(
            f"  route {rid} dir {direction or '(blank)'}: modal {modal} "
            f"({len(by_shape[modal])} trips, {len(by_shape)} variant shapes)"
        )
        for sid, group in sorted(by_shape.items()):
            keep = MODAL_KEEP if sid == modal else OTHER_KEEP
            kept_trips.extend(sorted(group, key=lambda t: t["trip_id"])[:keep])
    kept_trips.sort(key=lambda t: t["trip_id"])

    # The property the trim must preserve: identical modal picks.
    trimmed_modal = _modal_picks(kept_trips)
    if trimmed_modal != full_modal:
        problems.append("trimmed trips change a modal pick; adjust MODAL_KEEP/OTHER_KEEP")

    kept_shape_ids = {(t.get("shape_id") or "").strip() for t in kept_trips} - {""}
    kept_shapes = [s for s in shapes if (s.get("shape_id") or "").strip() in kept_shape_ids]

    print(
        f"\ntrimmed: {len(kept_trips)} of {len(trips)} trips, "
        f"{len(kept_shapes)} of {len(shapes)} shape points "
        f"({len(kept_shape_ids)} shape ids)"
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
