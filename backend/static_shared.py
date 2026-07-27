"""One staged-download pipeline for every GTFS static archive (C5).

THE BUG THIS EXISTS FOR: every static downloader renamed its finished download
over the cached archive BEFORE anything parsed it. So one bad upstream
publication (a truncated zip, an HTML error page saved as .zip, a stops.txt with
headers and no rows) destroyed the last-known-good file, and the
invalidate-on-empty recovery then re-downloaded the same bad publication in a
loop with nothing good left to fall back on. The archive could destroy its own
fallback.

The order is now stage, validate, promote: download to a stage file beside the
cache, validate the STAGED bytes with that loader's own gates, and only then
rename over the cache. A failed validation deletes the stage and leaves the cache
byte-untouched and still serving.

WHAT THIS MODULE DOES NOT DECIDE. It never guesses what a loader needs from an
archive; the loader passes a validator and that validator says. Counts inside
expected ranges are the contract monitor's job (it watches the upstream from the
other side); the question here is only "can I serve from this at all".

THE THREE TRANSFER LAYERS, all preserved, none flattened:
  1. R2's whole-ATTEMPT deadline (warmups.STATIC_ATTEMPT_DEADLINE_S) still wraps
     the entire load, download and parses together, outside this module.
  2. 13a's whole-TRANSFER deadline is `deadline_s` below, bounding the download
     alone, because httpx's own timeout is per socket read and does not bound a
     response that trickles.
  3. 14a's https-with-redirects handling lives in the default transfer, where the
     ferry's 302 makes following load-bearing rather than tidy.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import IO, TypeVar

import httpx

logger = logging.getLogger(__name__)

# Parser return type, so parse_member below stays transparent to a type checker
# (the PATH stops parser returns a tuple its validator indexes into).
_Parsed = TypeVar("_Parsed")


class StaticValidationError(Exception):
    """A staged or cached archive cannot be served from.

    Carries a SHAPE-NAMING message ("stops.txt yielded no usable stops", "missing
    required member(s): trips.txt"), not a stack of parser detail: the message is
    published on /api/status, and it is written here rather than derived from an
    upstream string, so it is safe to serve as-is.
    """


# ---------------------------------------------------------------------------
# Validator helpers. Loaders compose these; the pipeline never calls them itself.
# ---------------------------------------------------------------------------


def require_members(zf: zipfile.ZipFile, required: Iterable[str]) -> None:
    """Every named member must be present, or the archive cannot be served from.

    Presence only. This is the gate for the members a loader READS but degrades
    around (trips, shapes, stop_times): their absence is a broken publication, and
    rejecting it keeps the last-known-good archive serving instead of promoting a
    quietly diminished map. Emptiness is checked separately, and only for the two
    tables whose emptiness voids the layer outright (see require_parsed).

    Call this BEFORE any require_parsed, so an archive missing a member reports
    the missing member rather than whatever the parser makes of nothing.
    """
    present = set(zf.namelist())
    missing = [name for name in required if name not in present]
    if missing:
        raise StaticValidationError(f"missing required member(s): {', '.join(sorted(missing))}")


def parse_member(
    zf: zipfile.ZipFile, member: str, parse: Callable[[IO[bytes]], _Parsed]
) -> _Parsed:
    """Open `member` and run a stream-taking parser over it, closing the member.

    Most of the loaders' parsers take the open binary stream ZipFile.open yields
    (so tests can feed them io.BytesIO fixtures) and leave closing to the caller.
    Validators call them on archives they do not otherwise open, so the close has
    to happen somewhere; here it happens once.
    """
    with zf.open(member) as raw:
        return parse(raw)


def require_parsed(parse: Callable[[], object], member: str, what: str) -> None:
    """The member must parse, THROUGH THE LOADER'S OWN PARSER, to something usable.

    THIS IS THE GATE FOR THIRD-AUDIT FINDING 4 when a loader points it at stops.
    A stops.txt with headers and no rows parses cleanly to an empty table, and the
    subway loader promoted that to "ready" forever: every station vanished from
    the map and nothing retried, because structurally the archive was fine. PATH,
    ferry and the railroad each grew their own nonempty check over 13a, 14a and
    R3; the oldest loader never inherited it, and this is where all four now get
    it.

    Running the LOADER'S parser rather than a generic has-a-data-row check is what
    makes the gate exact. The predicate becomes identical to the one the load
    itself applies, so "the validator passed" implies "the load will produce
    something", and the R3-era recovery arms (parse, notice the emptiness,
    invalidate, re-download) have nothing left to catch: an archive that would
    parse to nothing never gets promoted, and a cached one that does is rejected
    by cached_archive_is_valid before it is parsed. A generic row check would
    leave a gap exactly the width of "has rows, none of them usable", and a
    fresh-by-mtime archive sitting in that gap is the wedge R3 was written for.

    The double parse costs one extra pass over stops.txt or routes.txt, the two
    smallest tables in every feed (tens to low thousands of rows), never over
    stop_times.txt.
    """
    try:
        parsed = parse()
    except KeyError as exc:
        raise StaticValidationError(f"missing required member(s): {member}") from exc
    except (UnicodeDecodeError, csv.Error) as exc:
        raise StaticValidationError(f"{member} is not readable as CSV") from exc
    if not parsed:
        raise StaticValidationError(f"{member} yielded no usable {what}")


def validate_archive(path: Path, validate: Callable[[zipfile.ZipFile], None]) -> None:
    """Open `path` as a zip and run the loader's validator over it.

    THE ONE FAILURE TYPE AT THIS BOUNDARY. Anything that goes wrong while reading
    an archive means the archive is unreadable, so everything below
    StaticValidationError is converted into one.

    The broad `except Exception` is deliberate, and it is the fix for a real wedge
    rather than defensive habit. An enumerated catch list looked complete twice and
    was wrong twice: it started with BadZipFile alone, gained NotImplementedError
    (a member compressed with a method this build cannot decompress), and still let
    zlib.error through, which is what a zip with an intact central directory and a
    damaged deflate stream raises on read. That mattered far more than a missing
    error message, because cached_archive_is_valid catches only StaticValidationError
    and OSError, and the loaders call it BEFORE the freshness test: an escaping
    exception meant no download was ever attempted, so the corrupt cache blocked its
    own repair forever. The set of ways a zip library can fail to read bytes is not
    something to enumerate, so it is not enumerated.

    The cost of being broad is that a genuine bug inside a validator (a TypeError,
    say) reads as a bad archive rather than crashing. The type name goes into the
    message and the original is chained, which is what makes that case diagnosable
    from /api/status alone.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            validate(zf)
    except StaticValidationError:
        raise  # already the shape-naming failure the validators raise
    except zipfile.BadZipFile as exc:
        # Named separately only because its message is the most useful one here
        # ("File is not a zip file", "Bad CRC-32 for file 'stops.txt'") and is
        # library text about the archive, never about our filesystem.
        raise StaticValidationError(f"not a readable zip archive ({exc})") from exc
    except Exception as exc:
        raise StaticValidationError(f"unreadable archive ({type(exc).__name__})") from exc


def cached_archive_is_valid(path: Path, validate: Callable[[zipfile.ZipFile], None]) -> bool:
    """Is the CACHED archive still something we can serve from?

    Run at load time, before the freshness check decides anything. A cache can be
    bad without any download having happened: bytes written by a pre-C5 build (the
    era when a bad publication could land), disk corruption, or a hand-placed
    file. Such a cache is treated as ABSENT, which forces a fresh staged download
    rather than parsing garbage or serving nothing.

    Treated as absent, NOT deleted. Deleting buys nothing here: if the forced
    download succeeds it overwrites the file anyway, and if it fails the file is
    rejected again on the next attempt by this same call, so unlinking would only
    destroy the evidence an operator needs to see what upstream published.

    THIS ABSORBS R3'S RAILROAD INVALIDATE-ON-EMPTY-PARSE. That site raised on a
    clean parse yielding zero stops and unlinked the cache so the retry would
    actually re-fetch, because a just-downloaded bad zip is fresh by mtime and
    would otherwise be re-parsed forever. Freshness now means valid AND recent
    rather than recent alone, so the same protection comes from two directions at
    once: a bad publication can no longer be promoted in the first place, and a
    bad cache is rejected here before it is parsed.
    """
    try:
        validate_archive(path, validate)
    except (StaticValidationError, OSError) as exc:
        logger.warning("Cached archive %s is unusable (%s); treating as absent", path.name, exc)
        return False
    return True


# ---------------------------------------------------------------------------
# Per-archive status, for the operator surface
# ---------------------------------------------------------------------------

# key -> {last_promoted_at, last_download_error, failed_downloads}
# Module-level because the loaders have no app handle (the warmups call them as
# plain module functions, and two of them run off the event loop); routes/status.py
# reads it through archive_status(). The contract monitor needs none of this: it
# watches the same publications from the upstream side, so these two vantage
# points are independent on purpose. This one says "what did MY last download do",
# the monitor says "what is upstream serving right now".
_ARCHIVE_STATUS: dict[str, dict] = {}


def archive_status() -> dict[str, dict]:
    """A copy of the per-archive record, for /api/status."""
    return {key: dict(value) for key, value in _ARCHIVE_STATUS.items()}


def reset_archive_status() -> None:
    """Clear the record. For tests: this is module state, so it outlives an app."""
    _ARCHIVE_STATUS.clear()


def _record(key: str, *, promoted: bool, error: str | None = None) -> None:
    entry = _ARCHIVE_STATUS.setdefault(
        key,
        {"last_promoted_at": None, "last_download_error": None, "failed_downloads": 0},
    )
    if promoted:
        entry["last_promoted_at"] = time.time()
        # A promotion clears the error: the operator question these fields answer
        # is "is what I am serving current, and if not why", and a superseded
        # failure answers neither.
        entry["last_download_error"] = None
        entry["failed_downloads"] = 0
    else:
        entry["last_download_error"] = error
        entry["failed_downloads"] += 1


def describe_failure(exc: BaseException) -> str:
    """A publishable one-line description of a failed download.

    TYPE PLUS SHAPE, NEVER RAW UPSTREAM TEXT, the same rule C4 settled on for the
    poll loop: this string is served by /api/status, and an httpx error's str()
    embeds the request URL (which for some feeds carries a key). A
    StaticValidationError is the exception, and deliberately so: its message is
    written by us in require_members / require_parsed, names a file shape, and is
    the single most useful thing an operator can read here.
    """
    if isinstance(exc, StaticValidationError):
        return f"invalid archive: {exc}"
    return type(exc).__name__


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

# Whole-transfer deadline shared by every static download (layer 2 of the three in
# the module docstring). Each loader used to hold its own copy of this constant
# with the same value and nearly the same comment ("Duplicated in railroad_static
# rather than shared: the two modules are kept intentionally separate"), which C5
# unifies. It stays a per-call parameter, not a hardcoded constant, so a loader
# with a genuinely different transfer profile can still say so.
DOWNLOAD_DEADLINE_S = 120


async def _stream_to_file(url: str, dest: Path, deadline_s: float) -> None:
    """The default transfer: stream `url` into `dest` under a whole-transfer deadline.

    follow_redirects=True IS LOAD-BEARING, not tidiness (14a). The ferry utility
    URL 302s to the resource zip, and httpx returns the 302 unfollowed by default,
    which raise_for_status treats as success and yields an EMPTY body: the exact
    silent-failure shape this module exists to catch, arriving before validation
    could see it. The other three loaders already passed the same flag, so
    unifying here keeps every caller's behavior and loses none of the ferry's.
    """
    async with asyncio.timeout(deadline_s):
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with dest.open("wb") as handle:
                    async for chunk in resp.aiter_bytes():
                        handle.write(chunk)


async def staged_fetch(
    url: str,
    dest: Path,
    validate: Callable[[zipfile.ZipFile], None],
    *,
    key: str,
    label: str,
    deadline_s: float = DOWNLOAD_DEADLINE_S,
    download: Callable[[str, Path, float], Awaitable[None]] | None = None,
) -> None:
    """Download to a stage file, validate it, and only then promote it over `dest`.

    The contract, in order:

    1. Sweep stage files this stem orphaned by an earlier hard kill. SCOPED TO THE
       STEM, because all four archives share one directory: the subway loader used
       to sweep a bare "*.part" glob, which could delete another loader's in-flight
       transfer and fail that download for no reason. Unifying here fixes that.
    2. Stream into a unique stage file in the SAME directory, so the promotion below
       is a rename within one filesystem and therefore atomic, and so concurrent
       workers (uvicorn --workers N all run lifespan) cannot interleave writes into
       one file.
    3. Validate the staged bytes with the loader's own validator.
    4. PASS: rename over the cache. This is the one rename that always existed;
       C5 only moved it after the validation.
       FAIL: delete the stage, leave the cache untouched, and raise. The caller's
       warmup treats that like any other failed attempt, so re-attempts follow the
       R3 rung schedule instead of a tight loop.

    THE CRASH WINDOW between step 3 and step 4 is safe by construction: the cache
    still holds the previous archive (nothing has written to it), and the stage
    file left behind is swept by step 1 of the next attempt. There is no state in
    which a half-validated archive is visible at `dest`, because a rename either
    happens or does not.

    `download` is injectable so tests can publish bytes without a socket; it
    defaults to the streaming transfer above.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    for orphan in dest.parent.glob(f"{dest.stem}.*.part"):
        orphan.unlink(missing_ok=True)
    fd, stage_name = tempfile.mkstemp(dir=dest.parent, prefix=f"{dest.stem}.", suffix=".part")
    os.close(fd)
    stage = Path(stage_name)
    logger.info("Downloading %s from %s", label, url)
    transfer = download or _stream_to_file
    try:
        await transfer(url, stage, deadline_s)
        validate_archive(stage, validate)
    except BaseException as exc:
        stage.unlink(missing_ok=True)
        # A cancellation is not a publication failure and must not be recorded as
        # one: shutdown would otherwise show up on /api/status as a bad upstream.
        if not isinstance(exc, asyncio.CancelledError):
            _record(key, promoted=False, error=describe_failure(exc))
            if isinstance(exc, StaticValidationError):
                logger.warning(
                    "Rejected %s publication (%s); keeping the cached archive at %s",
                    label,
                    exc,
                    dest,
                )
        raise
    try:
        stage.replace(dest)
    except OSError as exc:
        # A promotion that cannot complete is still a failed publication, and item
        # 5's "the failure is not silent" has to hold for it too. Deliberately
        # OUTSIDE the cleanup above: the stage file is left on disk exactly as a
        # process death here would leave it, for step 1 of the next attempt to
        # sweep, and the cache is untouched either way because a rename that raises
        # did not happen.
        _record(key, promoted=False, error=describe_failure(exc))
        raise
    _record(key, promoted=True)
    logger.info("Promoted %s to %s", label, dest)
