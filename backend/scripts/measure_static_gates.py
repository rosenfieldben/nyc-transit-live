#!/usr/bin/env python3
"""Measure what each sibling loader's cached check costs, on the real publications.

WHY THIS EXISTS (claude/static-rule-siblings, D2). Every loader's cached-archive check
used to run a LIGHT validator (members present, stops and routes parsed, the rest
presence-only), so a cache whose trips.txt or stop_times.txt could not be read was
called usable and then failed every load until MAX_AGE_DAYS. The branch makes the
check run the loader's PUBLICATION gate instead, which parses every member the load
requires. That gate is the expensive one, and it now runs at every process start and
whenever the cache is re-checked, off the serving loop (PR #126 put the check behind
asyncio.to_thread). This prints what that costs, per loader, beside what the light
check cost, the way backend/scripts/measure_subway_validation.py measured A02's repair
for the subway, so the number is measured rather than guessed.

WHAT IT PRINTS, one block per archive:
  the publication  bytes on the wire; each member the load reads, compressed and
                   expanded, and its data rows
  the checks       wall time of static_shared.validate_archive with the light validator
                   (the cached check before D2), with the publication gate (the cached
                   check after D2), and with the decorative gate where the loader has one
                   (asked of every download and, since the operator's ruling of
                   2026-10-16, of a usable cache at the cached check), best, median and
                   worst of --runs runs, the archive opened each time

THE ARCHIVES. LIRR, Metro-North, PATH and NYC Ferry are fetched once each, through
production's own transfer (static_shared._stream_to_file, which follows the ferry's
302), into the directory given on the command line, unless a file of the cache's name
is already there, in which case it is reused and nothing is downloaded. NJ TRANSIT IS
MEASURED ON THE COMMITTED FIXTURE (backend/tests/fixtures/njt_gtfs), zipped here,
because its archive is behind a token and a token is a mint out of the ten the account
gets a day, shared with production. The fixture is a trim (164 of 172 stops, the
shapes simplified), so its numbers are a fixture's and are labelled as such. Nothing
here can reach NJ Transit: no NJ Transit transfer is called.

THE SCRATCH RULE. The downloads are written under the given directory with the caches'
own file names, so the run refuses (exit 2, before any download) a directory that is,
or overlaps, a data root this checkout's app could be using (DATA_DIR as env_seams
resolves it, and the checkout's default data/), or that is any checkout's root. Nothing
is ever written anywhere else, and no loader is driven: only validators run, over the
files in that directory. Importing this module does nothing.

RUN, from the repository root:
    backend/.venv/bin/python backend/scripts/measure_static_gates.py DIR [--runs 5]

The archives are never committed and never used as test fixtures.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import statistics
import sys
import time
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REFUSED = 2  # exit status for a refused run (the scratch rule)


class Refused(Exception):
    """The run would write where a real cache lives; nothing has been done."""


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def real_data_roots() -> tuple[Path, ...]:
    """The data roots this checkout's app could be using: DATA_DIR as env_seams resolves
    it (the environment, or backend/.env, which env_seams loads on import) and the
    checkout's default data/, both, for the reason measure_subway_validation gives."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import env_seams

    configured = env_seams.directory("DATA_DIR", "data")
    return tuple(dict.fromkeys((configured.resolve(), (env_seams.PROJECT_ROOT / "data").resolve())))


def check_scratch(out_dir: Path, roots: Iterable[Path]) -> Path:
    """`out_dir` resolved, or Refused when writing there could touch a real cache. Pure
    apart from one existence test, so the rule is testable without a download."""
    out_dir = out_dir.resolve()
    if (out_dir / "backend" / "env_seams.py").exists():
        raise Refused(f"{out_dir} is a checkout of this repository")
    for root in roots:
        if _overlaps(out_dir, root.resolve()):
            raise Refused(f"{out_dir} overlaps the data root {root}, where the caches live")
    return out_dir


def data_rows(zf: zipfile.ZipFile, member: str) -> int | None:
    """Rows after the header, read as the loaders read them, or None when unreadable."""
    try:
        with zf.open(member) as raw:
            return sum(1 for _ in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")))
    except Exception:  # noqa: BLE001 - a table that cannot be read has no row count
        return None


def time_gate(static_shared, archive: Path, gate: Callable, runs: int) -> tuple[list[float], str]:
    """Seconds per run of validate_archive(archive, gate), and the verdict."""
    times: list[float] = []
    verdict = "passes"
    for _ in range(runs):
        started = time.perf_counter()
        try:
            static_shared.validate_archive(archive, gate)
        except static_shared.StaticValidationError as exc:
            verdict = f"REFUSES ({exc})"
        times.append(time.perf_counter() - started)
    return times, verdict


def ms(seconds: float) -> str:
    return f"{seconds * 1000:,.1f} ms"


def njt_fixture_archive(out_dir: Path) -> tuple[Path, float]:
    """The committed NJ Transit fixture zipped into `out_dir`, and an instant inside its
    service span (noon New York on its first added service day), so the service-date
    guard passes whatever the wall clock reads."""
    from datetime import datetime

    from feeds.shared import NYC_TZ

    fixture = BACKEND / "tests" / "fixtures" / "njt_gtfs"
    archive = out_dir / "gtfs_njt_fixture.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(fixture.glob("*.txt")):
            zf.write(path, path.name)
    with zipfile.ZipFile(archive) as zf, zf.open("calendar_dates.txt") as raw:
        days = [
            row["date"]
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            if (row.get("exception_type") or "").strip() == "1"
        ]
    first = min(days)
    noon = datetime(int(first[:4]), int(first[4:6]), int(first[6:8]), 12, tzinfo=NYC_TZ)
    return archive, noon.timestamp()


async def measure(out_dir: Path, runs: int) -> int:
    import ferry_static
    import njt_static
    import path_static
    import railroad_static
    import static_shared

    out_dir.mkdir(parents=True, exist_ok=True)
    njt_archive, njt_now = njt_fixture_archive(out_dir)

    def njt(gate: Callable) -> Callable:
        return lambda zf: gate(zf, now=njt_now)

    # (label, url or None, file, light, publication gate, decorative gate or None, reads)
    plans = [
        (
            "LIRR",
            railroad_static.RAILROAD_STATIC_URLS["LIRR"],
            out_dir / railroad_static.RAILROAD_STATIC_ZIPS["LIRR"].name,
            railroad_static.validate_railroad_archive,
            railroad_static.validate_railroad_publication,
            getattr(railroad_static, "validate_railroad_stop_times", None),
            ("stops.txt", "trips.txt", "shapes.txt", "routes.txt", "stop_times.txt"),
        ),
        (
            "Metro-North",
            railroad_static.RAILROAD_STATIC_URLS["MNR"],
            out_dir / railroad_static.RAILROAD_STATIC_ZIPS["MNR"].name,
            railroad_static.validate_railroad_archive,
            railroad_static.validate_railroad_publication,
            getattr(railroad_static, "validate_railroad_stop_times", None),
            ("stops.txt", "trips.txt", "shapes.txt", "routes.txt", "stop_times.txt"),
        ),
        (
            "PATH",
            path_static.PATH_STATIC_URL,
            out_dir / path_static.PATH_STATIC_ZIP.name,
            path_static.validate_path_archive,
            path_static.validate_path_publication,
            None,
            ("stops.txt", "trips.txt", "shapes.txt", "routes.txt", "stop_times.txt"),
        ),
        (
            "NYC Ferry",
            ferry_static.FERRY_STATIC_URL,
            out_dir / ferry_static.FERRY_STATIC_ZIP.name,
            ferry_static.validate_ferry_archive,
            ferry_static.validate_ferry_publication,
            None,
            ("stops.txt", "trips.txt", "shapes.txt", "routes.txt", "stop_times.txt"),
        ),
        (
            "NJ Transit (the committed fixture, not the publication)",
            None,
            njt_archive,
            njt(njt_static.validate_njt_archive),
            njt(njt_static.validate_njt_publication),
            getattr(njt_static, "validate_njt_geometry", None),
            (
                "stops.txt",
                "routes.txt",
                "trips.txt",
                "shapes.txt",
                "stop_times.txt",
                "calendar_dates.txt",
            ),
        ),
    ]

    print("=" * 78)
    print("THE SIBLING LOADERS' CACHED CHECK, light validator against publication gate")
    print("=" * 78)
    print(f"python: {sys.version.split()[0]}; runs per gate: {runs}; directory: {out_dir}")
    for label, url, archive, light, gate, decorative, reads in plans:
        print()
        if url is None:
            source = "built from backend/tests/fixtures/njt_gtfs"
        elif archive.exists():
            source = f"reused {archive.name} (no download)"
        else:
            started = time.perf_counter()
            await static_shared._stream_to_file(url, archive, 300)
            source = f"downloaded in {time.perf_counter() - started:.1f} s"
        with zipfile.ZipFile(archive) as zf:
            newest = max(info.date_time for info in zf.infolist())
            infos = {info.filename: info for info in zf.infolist()}
            print(f"{label}: {archive.stat().st_size:,} bytes, {source}")
            print(f"  members dated up to {newest[0]:04d}-{newest[1]:02d}-{newest[2]:02d}")
            print(f"  {'member the load reads':<24}{'compressed':>13}{'expanded':>13}{'rows':>11}")
            for member in reads:
                info = infos.get(member)
                if info is None:
                    print(f"  {member:<24}{'absent':>13}")
                    continue
                rows = data_rows(zf, member)
                shown = "?" if rows is None else f"{rows:,}"
                print(f"  {member:<24}{info.compress_size:>13,}{info.file_size:>13,}{shown:>11}")
        gates = [("light (before)", light), ("publication (after)", gate)]
        if decorative is not None:
            gates.append(("decorative", decorative))
        print(f"  {'gate':<24}{'best':>13}{'median':>13}{'worst':>13}  verdict")
        for name, validate in gates:
            times, verdict = time_gate(static_shared, archive, validate, runs)
            print(
                f"  {name:<24}{ms(min(times)):>13}{ms(statistics.median(times)):>13}"
                f"{ms(max(times)):>13}  {verdict}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dir", help="scratch directory for the downloads")
    parser.add_argument("--runs", type=int, default=5, help="runs per gate")
    args = parser.parse_args(argv)
    try:
        out_dir = check_scratch(Path(args.dir), real_data_roots())
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return REFUSED
    return asyncio.run(measure(out_dir, max(1, args.runs)))


if __name__ == "__main__":
    sys.exit(main())
