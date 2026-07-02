#!/usr/bin/env python3
"""Generate the committed AirTrain JFK static fixture (data/airtrain_jfk.json).

WHY this exists as a one-off script, not a runtime loader:
  - AirTrain JFK has NO public real-time feed (the Port Authority does not publish
    GTFS-RT for it), so this layer is static geometry + scheduled headways only.
  - Source: 511NY open data, https://s3.amazonaws.com/datatools-511ny/public/Airtrain_JFK.zip
    (~8.5 KB). NY open data license: attribution optional, derived products allowed.
  - CRITICAL CAVEAT: the feed's calendar.txt end_date is 20211231, so it is STALE as
    a schedule authority. We treat it as geometry + station reference + headway
    REFERENCE VALUES only. The headways emitted here are SCHEDULED reference numbers,
    never live data, and the map UI must label them "(scheduled)" and never render a
    countdown from them.
  - We deliberately do NOT feed this zip into the app's static GTFS warmup/loader:
    its expired calendar would be rejected or need special casing, and we do not want
    a runtime dependency on a stale third-party URL. So this script runs by hand, emits
    one committed JSON artifact, and the app reads only that artifact.

Run:  python backend/scripts/gen_airtrain_fixture.py
It prints an eyeball table (raw bands, detected overlaps, reconciled non-overlapping
bands per route) for manual verification against the Port Authority's published
AirTrain frequencies, then writes data/airtrain_jfk.json. Re-run to regenerate.
"""

from __future__ import annotations

import csv
import io
import json
import ssl
import sys
import urllib.request
import zipfile
from pathlib import Path

SOURCE_URL = "https://s3.amazonaws.com/datatools-511ny/public/Airtrain_JFK.zip"
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "data" / "airtrain_jfk.json"

COORD_DECIMALS = 5  # ~1 m; matches the subway/railroad shape rounding
SECONDS_PER_DAY = 86400


def _ssl_context() -> ssl.SSLContext:
    # Verify against certifi's CA bundle when present (a macOS framework Python has no
    # system CA store, so the default context fails cert verification); fall back to
    # the platform default elsewhere. certifi ships with the backend venv via httpx.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _download() -> zipfile.ZipFile:
    print(f"Downloading {SOURCE_URL} ...")
    with urllib.request.urlopen(SOURCE_URL, timeout=60, context=_ssl_context()) as resp:  # noqa: S310
        raw = resp.read()
    print(f"  got {len(raw)} bytes")
    return zipfile.ZipFile(io.BytesIO(raw))


def _read_csv(zf: zipfile.ZipFile, name: str) -> list[dict]:
    with zf.open(name) as fh:
        # utf-8-sig strips a BOM if the publisher left one on the first header.
        return list(csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")))


def _parse_gtfs_time(value: str) -> int:
    """GTFS HH:MM:SS to seconds since service-day midnight. Hours can exceed 24 (a
    trip that runs past midnight is expressed as 25:10:00, etc.), so this returns a
    raw second count that may be > 86400; the caller normalizes."""
    h, m, s = (int(p) for p in value.strip().split(":"))
    return h * 3600 + m * 60 + s


def _fmt_hhmm(seconds: int) -> str:
    """Format seconds-since-midnight as HH:MM, keeping 24:00 as a valid end-of-day."""
    h, m = divmod(seconds // 60, 60)
    return f"{h:02d}:{m:02d}"


def _stations(zf: zipfile.ZipFile) -> list[dict]:
    """All AirTrain stops with coordinates. AirTrain has a flat stop list (no parent
    stations), so every stops.txt row with a usable lat/lon is a rider-facing station."""
    out: list[dict] = []
    for row in _read_csv(zf, "stops.txt"):
        sid = (row.get("stop_id") or "").strip()
        if not sid:
            continue
        # location_type 1 would be a parent station (not a boardable platform); skip
        # it if the publisher ever adds one, so the count stays "boardable stations".
        if (row.get("location_type") or "0").strip() not in ("", "0"):
            continue
        try:
            lat = round(float(row["stop_lat"]), COORD_DECIMALS)
            lon = round(float(row["stop_lon"]), COORD_DECIMALS)
        except (KeyError, ValueError):
            continue
        out.append(
            {"id": sid, "name": (row.get("stop_name") or "").strip(), "lat": lat, "lon": lon}
        )
    out.sort(key=lambda s: s["id"])
    return out


def _route_names(zf: zipfile.ZipFile) -> dict[str, str]:
    names: dict[str, str] = {}
    for row in _read_csv(zf, "routes.txt"):
        rid = (row.get("route_id") or "").strip()
        if not rid:
            continue
        # Prefer the rider-facing long name, fall back to the short name, then the id.
        names[rid] = (
            (row.get("route_long_name") or "").strip()
            or (row.get("route_short_name") or "").strip()
            or rid
        )
    return names


def _shapes(zf: zipfile.ZipFile) -> dict[str, list[list[float]]]:
    """shape_id to ordered [[lat, lon], ...] polyline."""
    pts: dict[str, list[tuple[int, float, float]]] = {}
    if "shapes.txt" not in zf.namelist():
        return {}
    for row in _read_csv(zf, "shapes.txt"):
        try:
            sid = row["shape_id"].strip()
            seq = int(row["shape_pt_sequence"])
            lat = round(float(row["shape_pt_lat"]), COORD_DECIMALS)
            lon = round(float(row["shape_pt_lon"]), COORD_DECIMALS)
        except (KeyError, ValueError):
            continue
        pts.setdefault(sid, []).append((seq, lat, lon))
    return {sid: [[lat, lon] for _, lat, lon in sorted(rows)] for sid, rows in pts.items()}


def reconcile_bands(
    raw: list[tuple[int, int, int]],
) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, list[int]]], list[tuple[int, int]]]:
    """Reconcile per-route frequency bands into non-overlapping bands.

    frequencies.txt bands are per trip and overlap per route: a base 00:00-24:00 900s
    (15 min) band commonly sits under narrower daytime bands. Where windows overlap we
    take the MOST FREQUENT (smallest headway) covering band, since the narrow daytime
    entries represent added service on top of the all-day base.

    WHY base-plus-override rather than concurrent patterns: the overlapping entries are
    NOT two independent services running at once (which would sum to a shorter effective
    wait); they are one all-day base with denser daytime service layered over it, so the
    rider-facing headway inside an overlap is the denser band, not a combination. This
    reading was validated against the Port Authority's published AirTrain service levels
    (about every 7 min peak, every 15 min overnight), which match the reconciled bands.

    Returns:
      - merged:   non-overlapping (start_s, end_s, headway_s), sorted, adjacent equal
                  headways merged
      - overlaps: (start_s, end_s, [competing headways]) wherever >1 band covered a
                  slice with DIFFERENT headways (shown in the eyeball table)
      - gaps:     (start_s, end_s) slices no band covered (a gap means NOT clean)
    """
    bounds = sorted({b[0] for b in raw} | {b[1] for b in raw})
    merged: list[tuple[int, int, int]] = []
    overlaps: list[tuple[int, int, list[int]]] = []
    gaps: list[tuple[int, int]] = []
    for lo, hi in zip(bounds, bounds[1:]):
        if hi <= lo:
            continue
        covering = [b for b in raw if b[0] <= lo and b[1] >= hi]
        if not covering:
            gaps.append((lo, hi))
            continue
        headways = sorted({b[2] for b in covering})
        if len(headways) > 1:
            overlaps.append((lo, hi, headways))
        eff = headways[0]  # min headway wins
        if merged and merged[-1][2] == eff and merged[-1][1] == lo:
            merged[-1] = (merged[-1][0], hi, eff)
        else:
            merged.append((lo, hi, eff))
    return merged, overlaps, gaps


def build() -> tuple[dict, bool]:
    zf = _download()
    members = set(zf.namelist())
    print(f"  members: {sorted(members)}\n")

    stations = _stations(zf)
    route_names = _route_names(zf)
    shapes = _shapes(zf)
    trips = _read_csv(zf, "trips.txt")
    stop_times = _read_csv(zf, "stop_times.txt")
    freqs = _read_csv(zf, "frequencies.txt") if "frequencies.txt" in members else []

    # route_id -> set of trip_ids, and trip_id -> (route_id, shape_id)
    route_trips: dict[str, list[str]] = {}
    trip_route: dict[str, str] = {}
    trip_shape: dict[str, str] = {}
    for t in trips:
        rid = (t.get("route_id") or "").strip()
        tid = (t.get("trip_id") or "").strip()
        if not rid or not tid:
            continue
        route_trips.setdefault(rid, []).append(tid)
        trip_route[tid] = rid
        trip_shape[tid] = (t.get("shape_id") or "").strip()

    # trip_id -> ordered stop_ids (dedup, by stop_sequence)
    trip_stops: dict[str, list[tuple[int, str]]] = {}
    for st in stop_times:
        tid = (st.get("trip_id") or "").strip()
        sid = (st.get("stop_id") or "").strip()
        if not tid or not sid:
            continue
        try:
            seq = int(st["stop_sequence"])
        except (KeyError, ValueError):
            continue
        trip_stops.setdefault(tid, []).append((seq, sid))

    # trip_id -> list of (start_s, end_s, headway_s), normalized into [0, 86400]
    trip_bands: dict[str, list[tuple[int, int, int]]] = {}
    for f in freqs:
        tid = (f.get("trip_id") or "").strip()
        try:
            start = _parse_gtfs_time(f["start_time"])
            end = _parse_gtfs_time(f["end_time"])
            headway = int(f["headway_secs"])
        except (KeyError, ValueError):
            continue
        # Normalize past-midnight service (>24:00) by clamping to the 24 h service day;
        # AirTrain runs 24/7 so an end of 24:00:00 is the intended all-day close.
        start = max(0, min(start, SECONDS_PER_DAY))
        end = max(0, min(end, SECONDS_PER_DAY))
        if end > start:
            trip_bands.setdefault(tid, []).append((start, end, headway))

    routes_out: list[dict] = []
    clean = True
    print("=" * 78)
    print("AIRTRAIN JFK HEADWAY EYEBALL TABLE (verify against Port Authority published)")
    print("=" * 78)

    for rid in sorted(route_trips):
        name = route_names.get(rid, rid)
        tids = route_trips[rid]

        # Representative shape: the longest polyline among this route's trips' shapes.
        # Iterate SORTED candidates so a tie in point count breaks deterministically by
        # shape_id (the fixture is a golden artifact and must regenerate byte-identically).
        shape_ids = sorted({trip_shape.get(t, "") for t in tids} & set(shapes))
        best_shape = max(shape_ids, key=lambda s: len(shapes[s]), default="")
        polyline = shapes.get(best_shape, [])

        # Stations served: ordered stops from the longest trip on the route. Sort the
        # trip ids first so a tie in stop count is broken deterministically by trip_id.
        best_stop_trip = max(sorted(tids), key=lambda t: len(trip_stops.get(t, [])), default="")
        served = [sid for _, sid in sorted(trip_stops.get(best_stop_trip, []))]
        served_dedup: list[str] = []
        for sid in served:
            if sid not in served_dedup:
                served_dedup.append(sid)

        raw_bands = [b for t in tids for b in trip_bands.get(t, [])]
        merged, overlaps, gaps = reconcile_bands(raw_bands)

        print(f"\nROUTE {rid}  '{name}'")
        print(f"  shape: {best_shape or '(none)'} ({len(polyline)} pts) | stations: {served_dedup}")
        print(f"  raw frequency bands ({len(raw_bands)}):")
        for start, end, hw in sorted(raw_bands):
            print(f"    {_fmt_hhmm(start)}-{_fmt_hhmm(end)}  every {hw / 60:g} min ({hw}s)")
        if overlaps:
            print("  OVERLAPS (resolved by taking the MOST FREQUENT band):")
            for start, end, hws in overlaps:
                comp = ", ".join(f"{h / 60:g}min" for h in hws)
                window = f"{_fmt_hhmm(start)}-{_fmt_hhmm(end)}"
                print(f"    {window}  competing: {comp} -> kept {min(hws) / 60:g}min")
        if gaps:
            clean = False
            print("  !! GAPS (no band covers these slices; NOT clean):")
            for start, end in gaps:
                print(f"    {_fmt_hhmm(start)}-{_fmt_hhmm(end)}")

        bands_out = []
        for start, end, hw in merged:
            if hw % 60 != 0:
                clean = False
                print(
                    f"  !! non-integer minute headway {hw}s in {_fmt_hhmm(start)}-{_fmt_hhmm(end)}"
                )
            bands_out.append(
                {"start": _fmt_hhmm(start), "end": _fmt_hhmm(end), "headway_min": round(hw / 60)}
            )

        print("  RECONCILED non-overlapping bands:")
        for b in bands_out:
            print(f"    {b['start']}-{b['end']}  every ~{b['headway_min']} min (scheduled)")
        if not raw_bands:
            clean = False
            print("  !! no frequency bands for this route (NOT clean)")

        routes_out.append(
            {
                "id": rid,
                "name": name,
                "polyline": polyline,
                "stations": served_dedup,
                "headways": bands_out,
            }
        )

    fixture = {
        "_provenance": {
            "source_url": SOURCE_URL,
            "caveat": (
                "GTFS calendar end_date 20211231; used as GEOMETRY and STATION + HEADWAY "
                "REFERENCE only, not a live schedule authority."
            ),
            "headways": (
                "Scheduled reference values, not real-time. AirTrain JFK has no GTFS-RT feed."
            ),
            "generated_by": "backend/scripts/gen_airtrain_fixture.py",
        },
        "stations": stations,
        "routes": routes_out,
    }

    print("\n" + "=" * 78)
    print(f"stations: {len(stations)} (expect 10) | routes: {len(routes_out)} (expect 3)")
    print(f"RECONCILIATION: {'CLEAN' if clean else 'NEEDS REVIEW (see !! flags above)'}")
    print("=" * 78)
    return fixture, clean


def main() -> int:
    fixture, clean = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT_PATH.relative_to(REPO_ROOT)}")
    if not clean:
        print(
            "\nRECONCILIATION NEEDS REVIEW: do not trust the headway table "
            "until the flags above are resolved."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
