#!/usr/bin/env python3
"""Measure what validating the subway archive costs, on the real MTA publication.

WHY THIS EXISTS (audit 2026-10-12, A02, and the A09 dependency it carries). A02's
repair makes validate_subway_archive parse every member the subway load reads, and
two of those members, stop_times.txt above all, are the biggest tables in the
publication. Validation runs synchronously on the serving event loop in two places
(staged_fetch on a download, and load_subway_stops' cached-archive check at every
load), so whatever it costs is time every other coroutine in the process (the
pollers, /healthz) waits. Whether validation moves off the loop is decided by this
number, measured on the real archive, rather than by a guess about it.

WHAT IT PRINTS, as one table:
  the publication  bytes on the wire; each member's compressed and expanded size; the
                   data rows of every member the subway load reads
  validation       wall time of static_shared.validate_archive with the LIVE
                   validate_subway_archive, and with the CANDIDATE below (the live
                   validator plus a drain of trips.txt, stop_times.txt and shapes.txt),
                   best and median of --runs runs, the archive opened each time
  the loop         the longest gap in a 1 ms heartbeat sharing the event loop with one
                   attempt of the REAL warmups._warm_subway_static, measured the way
                   the A09 record's arm 1 measures it, for each validator, from a cold
                   cache (the staged download's validation) and from a cache already on
                   disk (the cached-archive check). The gap during validation and the
                   gap anywhere else in the attempt are reported apart, and each loader
                   the attempt calls is timed, so a stall that belongs to a loader the
                   warmup runs after validation is visible as that loader's. A stall
                   AT validation is the whole uninterrupted stretch the loop could not
                   run, which includes whatever runs inline before the next await
                   (today the stops, route shapes and station loaders): that is what
                   the other coroutines feel, so it is what is reported.

THE CANDIDATE IS BRANCH-LOCAL ON PURPOSE. It is defined here, not in static_data, so
the cost of the repair can be measured before the repair is live. Its drains read
each member through the same TextIOWrapper and csv.DictReader the loaders use and
keep only a count, which is what the repaired validator is to do. Once the live
validator already parses those members (detected by behavior: a probe archive whose
trips.txt holds one 0xFF byte), the candidate is skipped and only the live code is
measured, so a re-run after the repair records the repair's own number.

THE ONE DOWNLOAD. The archive at static_data.SUBWAY_GTFS_URL is fetched once, through
production's own transfer (static_shared._stream_to_file), into the directory given
on the command line. --archive measures a copy downloaded earlier instead, so a
re-run costs no second download. After the archive is in hand, an audit hook refuses
every socket connect and name resolution, and the warmup's own staged download is
answered by a fake at httpx's socket layer that serves the file already on disk for
that one URL and refuses any other. Nothing else here touches the network, and
nothing here can reach NJ Transit (no NJ Transit module is driven).

THE SCRATCH RULE, and why it is a refusal rather than a convention. The warmup driven
here writes, promotes and deletes the subway cache, so it must never run where the
real cache lives. The data root is redirected to <dir>/data before any backend
module that reads it is imported, and the run refuses (exit 2, before any download)
when that scratch cache would be the real one, when the scratch data root overlaps
the real data root, or when --archive names the real cache. Importing this module
does nothing: every action, the environment change included, happens in main().

RUN, from the repository root:
    backend/.venv/bin/python backend/scripts/measure_subway_validation.py [DIR]
    backend/.venv/bin/python backend/scripts/measure_subway_validation.py DIR \\
        --archive DIR/gtfs_subway.zip --runs 5 --attempts 3

DIR defaults to a new temporary directory. The archive is never committed and never
used as a test fixture: it is a 5 MB download that expands to tens of megabytes.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import shutil
import statistics
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent

# The members the subway load reads, in the order the table prints them.
READ_MEMBERS = ("stops.txt", "transfers.txt", "trips.txt", "stop_times.txt", "shapes.txt")
HEARTBEAT_S = 0.001  # A09's arm 1 heartbeat: any other coroutine sharing the loop
REFUSED = 2  # exit status for a refused run (the scratch rule)


class Refused(Exception):
    """The run would touch the real cache; nothing has been done."""


# ---------------------------------------------------------------------------
# The scratch rule
# ---------------------------------------------------------------------------


def _overlaps(a: Path, b: Path) -> bool:
    """True when one path is the other or contains it."""
    return a == b or a in b.parents or b in a.parents


def scratch_plan(out_dir: Path, archive: Path | None, real_cache: Path) -> dict[str, Path]:
    """Where everything goes, or Refused when any of it would touch the real cache.

    Pure, so the rule can be tested without a download or a backend import.
    `real_cache` is the subway cache a normal run of this checkout would use.
    """
    out_dir = out_dir.resolve()
    real_cache = real_cache.resolve()
    real_root = real_cache.parent.parent  # <DATA_DIR>/gtfs_static/gtfs_subway.zip
    data_root = out_dir / "data"
    scratch_cache = data_root / "gtfs_static" / real_cache.name
    if scratch_cache == real_cache:
        raise Refused(f"the scratch cache {scratch_cache} is the real subway cache")
    if _overlaps(data_root, real_root):
        raise Refused(
            f"the scratch data root {data_root} overlaps the real data root {real_root}; "
            "the warmup this drives writes, promotes and deletes the cache there"
        )
    if archive is not None and archive.resolve() == real_cache:
        raise Refused(f"--archive {archive} is the real subway cache; measure a scratch copy")
    return {
        "out": out_dir,
        "data_root": data_root,
        "scratch_cache": scratch_cache,
        "download": out_dir / real_cache.name,
    }


def real_cache_path() -> Path:
    """The subway cache this checkout uses, read through env_seams as static_data reads
    it, BEFORE the data root is redirected (env_seams loads .env on import, which is
    where a developer's DATA_DIR may live)."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import env_seams

    return env_seams.directory("DATA_DIR", "data") / "gtfs_static" / "gtfs_subway.zip"


# ---------------------------------------------------------------------------
# The candidate validator (branch-local; see the module docstring)
# ---------------------------------------------------------------------------


def _drain(keep: Callable[[dict], bool]) -> Callable:
    """A stream-taking parser that reads every row as the loaders do and keeps only a
    count of the rows `keep` accepts."""

    def parse(raw) -> int:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        return sum(1 for row in reader if keep(row))

    return parse


def _trip_row(row: dict) -> bool:
    return bool((row.get("trip_id") or "").strip())


def _stop_time_row(row: dict) -> bool:
    return bool((row.get("trip_id") or "").strip() and (row.get("stop_id") or "").strip())


def candidate_validator(static_data, static_shared) -> Callable[[zipfile.ZipFile], None]:
    """The live validator, then the three drains the repair adds, each through
    require_parsed so a failure reads the way the live gates' failures read."""

    def shape_row(row: dict) -> bool:
        try:
            int(row["shape_pt_sequence"])
            float(row["shape_pt_lat"])
            float(row["shape_pt_lon"])
        except (KeyError, ValueError, TypeError):
            return False
        return bool(static_data._SHAPE_ID_RE.match(row.get("shape_id") or ""))

    drains = (
        ("trips.txt", _drain(_trip_row), "trips"),
        ("stop_times.txt", _drain(_stop_time_row), "stop times"),
        ("shapes.txt", _drain(shape_row), "route shape points"),
    )

    # Bound now, not looked up at call time: the attempts below put a timing wrapper
    # around static_data.validate_subway_archive, and a lookup would find the wrapper.
    live = static_data.validate_subway_archive

    def validate(zf: zipfile.ZipFile) -> None:
        live(zf)
        for member, parse, what in drains:
            static_shared.require_parsed(
                lambda m=member, p=parse: static_shared.parse_member(zf, m, p), member, what
            )

    return validate


def probe_archive() -> bytes:
    """A minimal subway archive, valid in every member but one 0xFF byte in trips.txt.

    The live validator rejecting it means the repair is live, so the candidate would
    only parse the three members a second time."""
    members = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
        "101,Alpha,40.7,-74.0,1,\n101N,Alpha,40.7,-74.0,0,101\n",
        "transfers.txt": "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n101,103,2,180\n",
        "trips.txt": "route_id,service_id,trip_id,trip_headsign\n1,wk,t1,Uptown",
        "stop_times.txt": "trip_id,stop_id,stop_sequence\nt1,101N,1\n",
        "shapes.txt": "shape_id,shape_pt_sequence,shape_pt_lat,shape_pt_lon\n"
        "1..N01R,1,40.7,-74.0\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            data = text.encode() + (b"\xff\n" if name == "trips.txt" else b"")
            zf.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------


def member_table(archive: Path) -> list[dict]:
    """Every member's sizes, and the data rows of the members the load reads."""
    rows = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            entry = {
                "name": info.filename,
                "compressed": info.compress_size,
                "expanded": info.file_size,
                "rows": None,
            }
            if info.filename in READ_MEMBERS:
                with zf.open(info) as raw:
                    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                    entry["rows"] = sum(1 for _ in reader)
            rows.append(entry)
    return rows


def time_validator(static_shared, archive: Path, validate, runs: int) -> list[float]:
    """Wall seconds of validate_archive, the zip open included, `runs` times."""
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        static_shared.validate_archive(archive, validate)
        times.append(time.perf_counter() - started)
    return times


class Timed:
    """Wraps a callable and records each call's (start, end) on perf_counter."""

    def __init__(self, inner: Callable) -> None:
        self.inner = inner
        self.windows: list[tuple[float, float]] = []

    def __call__(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return self.inner(*args, **kwargs)
        finally:
            self.windows.append((started, time.perf_counter()))


def longest_gap(
    stamps: list[float], windows: list[tuple[float, float]] | None = None, *, inside: bool = True
) -> float:
    """The longest heartbeat gap in seconds. With `windows`, only the gaps that overlap
    one (inside=True) or that overlap none (inside=False)."""
    best = 0.0
    for a, b in zip(stamps, stamps[1:]):
        if windows is not None:
            overlaps = any(a < end and b > start for start, end in windows)
            if overlaps != inside:
                continue
        best = max(best, b - a)
    return best


async def one_attempt(modules: dict, validate, *, cached: bool, archive: Path) -> dict:
    """One attempt of the real subway warmup, with `validate` as the subway validator,
    a 1 ms heartbeat sharing the loop, and the loaders it calls timed."""
    import fastapi

    main, static_data, static_shared, warmups = (
        modules["main"],
        modules["static_data"],
        modules["static_shared"],
        modules["warmups"],
    )
    cache = static_data.SUBWAY_GTFS_ZIP
    cache.unlink(missing_ok=True)
    if cached:
        cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, cache)  # mtime now: fresh, so no download
    static_shared.reset_archive_status()

    validator = Timed(validate)
    # (module, attribute, column): every loader the attempt calls, each timed in place.
    # The warmup resolves its loaders through main at call time and load_subway_stops
    # resolves the validator and _parse_stops through static_data, so these are the
    # names the attempt actually calls.
    targets = (
        (static_data, "validate_subway_archive", "validation"),
        (static_data, "_parse_stops", "stops"),
        (main, "load_subway_route_shapes", "shapes"),
        (main, "load_subway_stations", "stations"),
        (main, "load_subway_station_routes", "routes*"),
        (main, "load_subway_station_complexes", "complexes"),
    )
    saved = {(module, name): getattr(module, name) for module, name, _ in targets}
    timed = {}
    for module, name, column in targets:
        timed[column] = validator if column == "validation" else Timed(getattr(module, name))
        setattr(module, name, timed[column])

    app = fastapi.FastAPI()
    app.state.subway_static_status = "loading"  # what main.lifespan sets
    stamps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        while not stop.is_set():
            stamps.append(time.perf_counter())
            await asyncio.sleep(HEARTBEAT_S)

    ticker = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)  # let the heartbeat establish itself
    started = time.perf_counter()
    task = asyncio.create_task(warmups._warm_subway_static(app))
    try:
        while app.state.subway_static_status == "loading" and time.perf_counter() - started < 300:
            await asyncio.sleep(HEARTBEAT_S)
        elapsed = time.perf_counter() - started
        status = app.state.subway_static_status
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for (module, name), value in saved.items():
            setattr(module, name, value)
        stop.set()
        await ticker
    windows = validator.windows
    return {
        "status": status,
        "elapsed": elapsed,
        "gap_any": longest_gap(stamps),
        "gap_validation": longest_gap(stamps, windows) if windows else None,
        "gap_outside": longest_gap(stamps, windows, inside=False),
        "loaders": {name: sum(b - a for a, b in t.windows) for name, t in timed.items()},
        "downloaded": static_shared.archive_status().get("subway", {}).get("last_promoted_at")
        is not None,
    }


# ---------------------------------------------------------------------------
# The fake socket layer for the warmup's own download (after the real one)
# ---------------------------------------------------------------------------


def install_offline(url: str, archive: Path) -> None:
    """After the one real download: refuse every socket, and answer the warmup's
    staged download of `url` from the file already on disk."""
    import httpx

    def refuse_sockets(event: str, args: tuple) -> None:
        if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
            raise RuntimeError(f"measure_subway_validation: {event} refused after the download")

    sys.addaudithook(refuse_sockets)
    body = archive.read_bytes()

    class Body(httpx.AsyncByteStream):
        """The body in 64 KiB chunks with a suspension before each, as a socket read
        gives the loop a turn, so the transfer is not one block the heartbeat sees as
        part of whatever runs after it."""

        async def __aiter__(self):
            for offset in range(0, len(body), 65536):
                await asyncio.sleep(0)
                yield body[offset : offset + 65536]

    async def answer(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) != url:
            raise AssertionError(f"refusing a request this script does not serve: {request.url}")
        headers = {"content-length": str(len(body))}
        return httpx.Response(200, headers=headers, stream=Body(), request=request)

    httpx.AsyncHTTPTransport.handle_async_request = answer  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def ms(seconds: float | None) -> str:
    return "n/a" if seconds is None else f"{seconds * 1000:,.0f} ms"


async def measure(plan: dict[str, Path], archive_arg: Path | None, runs: int, attempts: int) -> int:
    os.environ["DATA_DIR"] = str(plan["data_root"])
    import main
    import static_data
    import static_shared
    import warmups

    modules = {
        "main": main,
        "static_data": static_data,
        "static_shared": static_shared,
        "warmups": warmups,
    }
    if static_data.SUBWAY_GTFS_ZIP.resolve() != plan["scratch_cache"]:
        # static_data was imported earlier in this process with another data root,
        # so the warmup below would use THAT cache. Refuse rather than guess.
        raise Refused(
            f"static_data's cache is {static_data.SUBWAY_GTFS_ZIP}, not the scratch "
            f"cache {plan['scratch_cache']}; run this as its own process"
        )
    url = static_data.SUBWAY_GTFS_URL
    plan["out"].mkdir(parents=True, exist_ok=True)

    if archive_arg is not None:
        archive = archive_arg.resolve()
        fetched = f"reused {archive} (no download)"
    else:
        archive = plan["download"]
        started = time.perf_counter()
        await static_shared._stream_to_file(url, archive, 300)
        fetched = f"downloaded {url} in {time.perf_counter() - started:.1f} s"
    install_offline(url, archive)

    print("=" * 78)
    print("SUBWAY VALIDATION COST, on the real MTA publication")
    print("=" * 78)
    print(f"archive      : {fetched}")
    print(f"scratch cache: {plan['scratch_cache']}")
    print(f"python       : {sys.version.split()[0]}; validator runs per form: {runs}")
    with zipfile.ZipFile(archive) as zf:
        newest = max(info.date_time for info in zf.infolist())
    print(f"members dated: up to {newest[0]:04d}-{newest[1]:02d}-{newest[2]:02d}")
    print()

    table = member_table(archive)
    wire = archive.stat().st_size
    print(
        f"THE PUBLICATION  {wire:,} bytes on the wire, "
        f"{sum(r['expanded'] for r in table):,} expanded"
    )
    print(f"  {'member':<20}{'compressed':>14}{'expanded':>14}{'data rows':>14}")
    for r in table:
        rows = "" if r["rows"] is None else f"{r['rows']:,}"
        print(f"  {r['name']:<20}{r['compressed']:>14,}{r['expanded']:>14,}{rows:>14}")
    print()

    probe = plan["out"] / "probe.zip"
    probe.write_bytes(probe_archive())
    try:
        static_shared.validate_archive(probe, static_data.validate_subway_archive)
        live_parses_all = False
    except static_shared.StaticValidationError as exc:
        live_parses_all = "trips.txt" in str(exc)
    probe.unlink()
    forms: list[tuple[str, Callable]] = [("live", static_data.validate_subway_archive)]
    if live_parses_all:
        print(
            "The live validator rejects a trips.txt holding one 0xFF byte: it already "
            "parses every member, so the candidate is not measured."
        )
    else:
        forms.append(("candidate", candidate_validator(static_data, static_shared)))
        print(
            "The live validator accepts a trips.txt holding one 0xFF byte (it parses "
            "stops.txt and transfers.txt only); the candidate adds the three drains."
        )
    print()

    print(f"VALIDATION  static_shared.validate_archive(archive, validator), {runs} runs")
    print(f"  {'form':<12}{'best':>12}{'median':>12}{'worst':>12}")
    for name, validate in forms:
        times = time_validator(static_shared, archive, validate, runs)
        print(
            f"  {name:<12}{ms(min(times)):>12}{ms(statistics.median(times)):>12}"
            f"{ms(max(times)):>12}"
        )
    print()

    print(
        f"THE LOOP  the real _warm_subway_static with a 1 ms heartbeat beside it, "
        f"median of {attempts} attempts"
    )
    print("  stall at validation: the longest heartbeat gap overlapping the validation")
    print("  other stall        : the longest heartbeat gap overlapping no validation")
    print(
        f"  {'form':<11}{'start':<7}{'status':<7}{'attempt':>10}{'validation':>12}"
        f"{'stall at validation':>21}{'other stall':>13}"
    )
    loaders: list[tuple[str, dict]] = []
    for name, validate in forms:
        for cached in (False, True):
            runs_ = [
                await one_attempt(modules, validate, cached=cached, archive=archive)
                for _ in range(attempts)
            ]
            start = "cached" if cached else "cold"
            status = "/".join(sorted({r["status"] for r in runs_}))

            def med(key: str, runs_=runs_) -> float | None:
                values = [r[key] for r in runs_ if r[key] is not None]
                return statistics.median(values) if values else None

            validation = statistics.median(r["loaders"]["validation"] for r in runs_)
            print(
                f"  {name:<11}{start:<7}{status:<7}{ms(med('elapsed')):>10}{ms(validation):>12}"
                f"{ms(med('gap_validation')):>21}{ms(med('gap_outside')):>13}"
            )
            columns = runs_[0]["loaders"]
            median_loaders = {
                column: statistics.median(r["loaders"][column] for r in runs_) for column in columns
            }
            loaders.append((f"{name} {start}", median_loaders))
    print()
    print("  the loaders inside each attempt, median wall time (* runs in a worker thread)")
    columns = [c for c in loaders[0][1] if c != "validation"]
    print("  " + f"{'':<17}" + "".join(f"{c:>12}" for c in columns))
    for label, timing in loaders:
        print("  " + f"{label:<17}" + "".join(f"{ms(timing[c]):>12}" for c in columns))
    static_data.SUBWAY_GTFS_ZIP.unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("dir", nargs="?", help="scratch directory (default: a new temp dir)")
    parser.add_argument("--archive", type=Path, help="measure this earlier download instead")
    parser.add_argument("--runs", type=int, default=5, help="validator runs per form")
    parser.add_argument("--attempts", type=int, default=3, help="warmup attempts per row")
    args = parser.parse_args(argv)
    out = Path(args.dir) if args.dir else Path(tempfile.mkdtemp(prefix="measure-subway-"))
    try:
        plan = scratch_plan(out, args.archive, real_cache_path())
        return asyncio.run(measure(plan, args.archive, max(1, args.runs), max(1, args.attempts)))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
