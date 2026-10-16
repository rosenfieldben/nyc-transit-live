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
import zlib
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

    `member` names the one archive member the failure is about, where it is about one
    (require_members and require_parsed set it), so staged_fetch can publish a
    degraded member under its own name rather than parse it back out of the message.
    """

    def __init__(self, message: str, *, member: str | None = None) -> None:
        super().__init__(message)
        self.member = member


def require_decoration(zf: zipfile.ZipFile, member: str) -> None:
    """The decorative member must be present: its absence is a failure of the member's
    gate like an unreadable one, named by member, which the two-mode rule degrades on a
    cold start and refuses over a cache that draws.

    THE SUBWAY'S BEHAVIOUR, FOR EVERY LOADER (the operator's ruling on
    claude/static-rule-siblings, 2026-10-16). D3 had made absence degraded in both modes
    for the railroad's stop_times.txt and NJ Transit's shapes.txt, on the ground that
    both loaders serve a publication without the member; the rule is now that a
    publication is promotable when nothing is lost, and one without the member over a
    cache whose member draws loses the layer as surely as a damaged one. Over a cache
    that is itself without it, nothing is lost, and it is promoted."""
    if member not in set(zf.namelist()):
        raise StaticValidationError(f"{member} is absent from the publication", member=member)


# ---------------------------------------------------------------------------
# Validator helpers. Loaders compose these; the pipeline never calls them itself.
# ---------------------------------------------------------------------------


def require_members(zf: zipfile.ZipFile, required: Iterable[str]) -> None:
    """Every named member must be present, or the archive cannot be served from.

    Presence only, and so the FIRST gate, never the whole one. An absent member is a
    broken publication, and rejecting it keeps the last-known-good archive serving
    instead of promoting a quietly diminished map. Whether a present member can be
    READ is require_parsed's question, asked with the loader's own parser, and a
    member that passes here and is parsed by nothing can still fail the load: that is
    how an undecodable trips.txt was promoted over a working subway cache while this
    said it was there (audit 2026-10-12, A02). The subway's validator now parses every
    member it names here; which members another loader parses, and why, is stated at
    its own validator.

    Call this BEFORE any require_parsed, so an archive missing a member reports
    the missing member rather than whatever the parser makes of nothing.
    """
    present = set(zf.namelist())
    missing = [name for name in required if name not in present]
    if missing:
        raise StaticValidationError(
            f"missing required member(s): {', '.join(sorted(missing))}",
            member=missing[0] if len(missing) == 1 else None,
        )


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


_OTHER_READ_ERRORS: tuple[type[BaseException], ...] = (OSError, NotImplementedError, RuntimeError)
try:
    import lzma

    _OTHER_READ_ERRORS += (lzma.LZMAError,)
except ImportError:  # a build without liblzma, where zipfile cannot read such a member
    pass


# THE RESIDUAL ARM'S CLASSES, ONE TUPLE FOR ALL FIVE LOADERS (claude/static-rule-siblings,
# D1). Each loader wraps its reads of the CACHED archive in an arm that discards the cache
# on one of these and fails the attempt, so the next attempt downloads instead of failing
# on the same bytes until MAX_AGE_DAYS. Reaching the arm means the cached check and the
# load disagree about what is readable. Discarding is right in exactly that case: the file
# IS the cache, nothing better sits behind it, and keeping it wedges every retry.
#
# THE ARCHIVE'S ERRORS ONLY, as the operator ruled on claude/a02-publication-validation:
# the bytes do not decode, are not CSV, do not inflate (zlib.error, which the ferry's, the
# PATH's and the railroad's own tuples lacked, so a damaged DEFLATE stream wedged them for
# a month and, in the railroad, escaped _load_one and took both systems down through its
# gather), are truncated (EOFError), fail a CRC (BadZipFile) or lack a member (KeyError).
# OSError is not one: running out of file descriptors or a failing disk says nothing about
# the archive, so such an error fails the attempt with the cache kept, and the warmup's
# next rung reads it again. Five loaders kept five copies of this list and they drifted
# apart (four of them in three different shapes); one tuple is what keeps them equal.
#
# NOT QUITE EVERY ARCHIVE ERROR, and why that no longer wedges anything: a member
# compressed with bzip2 whose stream is damaged raises a plain OSError, one with lzma
# raises LZMAError, and a bit-rotted central-directory offset raises OSError(EINVAL) on
# every member read, errno and all, so no test on the class or the errno tells them from
# the environment. They are caught one step earlier instead. Every loader's cached check
# runs its PUBLICATION gate (D2 on the same branch), which reads every member the load
# requires through read_or_refuse and under validate_archive's broad catch, so such a
# cache is refused at the check, named by member, treated as absent and re-downloaded;
# it never reaches a load. What reaches this arm is a cache that changed after its check.
UNREADABLE_AT_LOAD: tuple[type[BaseException], ...] = (
    UnicodeDecodeError,
    csv.Error,
    zipfile.BadZipFile,
    zlib.error,
    KeyError,
    EOFError,
)

# WHAT READING ONE MEMBER IS KNOWN TO RAISE, the classes read_or_refuse names with the
# reader's words: the archive's own (above) and the rest (_OTHER_READ_ERRORS: bzip2's
# plain OSError, LZMAError, a method this build lacks, an encrypted member, EINVAL, and
# the environment's OSError). NOT EVERYTHING: a zip64 header offset past 2**63 makes the
# member's seek raise ValueError (OverflowError on BytesIO), found by the review of
# claude/static-rule-siblings. read_or_refuse names whatever else a read raises by
# type, and the DECORATIVE members' reads at load (the railroad's stop_times.txt, NJ
# Transit's shapes.txt) degrade on any Exception, because the gate that promoted the
# member with it degraded (validate_archive) catches any Exception too.
UNREADABLE_MEMBER: tuple[type[BaseException], ...] = UNREADABLE_AT_LOAD + _OTHER_READ_ERRORS


def read_or_refuse(parse: Callable[[], _Parsed], member: str) -> _Parsed:
    """Run `parse`, a read of `member`, and return what it yields; whatever reading the
    member raises becomes a StaticValidationError that NAMES it.

    require_parsed is this plus a floor, and a gate that needs the parsed value rather
    than a floor (NJ Transit's service calendar) calls this directly, so every member a
    gate reads is refused under its own name with the same classes, never as "archive".

    THE MESSAGE CARRIES THE READER'S OWN WORDS (A02, D7), because it is what an operator
    reads on /api/status as last_download_error. The decode and CSV errors say what
    ("'utf-8' codec can't decode byte 0xff"), and a damaged deflate stream or a CRC
    mismatch inside the member says which member, which the archive-level catch in
    validate_archive cannot. Not where: a decode error's "position" counts from the start
    of the chunk the text reader was decoding, not from the start of the member, so in a
    large member it does not locate the byte, and a csv.Error carries no line at all. All
    of it is library text about the archive's bytes, never about our filesystem, so it is
    safe to publish (describe_failure's rule).
    """
    try:
        return parse()
    except KeyError as exc:
        raise StaticValidationError(f"missing required member(s): {member}", member=member) from exc
    except (UnicodeDecodeError, csv.Error) as exc:
        raise StaticValidationError(
            f"{member} is not readable as CSV ({exc})", member=member
        ) from exc
    except (zlib.error, zipfile.BadZipFile, EOFError) as exc:
        raise StaticValidationError(
            f"{member} is not readable ({type(exc).__name__}: {exc})", member=member
        ) from exc
    except _OTHER_READ_ERRORS as exc:
        # THE REST OF WHAT READING A MEMBER CAN RAISE, named by member so a degraded
        # member is keyed by its name and not as "archive" (the A02 branch's review, F4):
        # a bz2 stream the decompressor refuses (a plain OSError with no errno), an
        # lzma one (LZMAError), a compression method this build lacks, an encrypted
        # member, a bit-rotted central-directory offset (OSError(EINVAL) on every member
        # read), and the environment (an OSError with an errno). The TYPE only, as
        # validate_archive's broad catch does, because an OSError's text can carry a
        # path of ours and this message is published.
        raise StaticValidationError(
            f"{member} is not readable ({type(exc).__name__})", member=member
        ) from exc
    except Exception as exc:
        # ANYTHING ELSE, by type and under the member's name, so the refusal or the
        # degraded member says which member rather than "archive": validate_archive would
        # refuse it anyway (its catch is broad on purpose), only without the name. A zip64
        # header offset past 2**63 is one such class (ValueError on the seek).
        raise StaticValidationError(
            f"{member} is not readable ({type(exc).__name__})", member=member
        ) from exc


def read_as_loaded(parse: Callable[[], _Parsed], member: str) -> _Parsed:
    """read_or_refuse's twin for the LOAD: the same call shape, and the parser's own
    errors let through, so the residual arm sees the classes it names.

    A loader whose gate runs its whole parse (_parse_open) takes one of the two as its
    `read`: the load passes this one, the gate passes read_or_refuse. So the gate and
    the load run ONE sequence of reads through the loader's own parsers, and a failure
    at the gate is refused under the member's own name rather than as "unreadable
    archive (UnicodeDecodeError)", which named no member and was what the ferry's, the
    PATH's and the railroad's gates said before claude/static-rule-siblings (D2)."""
    del member  # the load's errors name nothing; the arm logs the exception itself
    return parse()


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

    The double parse is cheap for the small tables. It is not cheap for stop_times.txt,
    which four loaders' gates parse now that the gate is also the cached check
    (claude/static-rule-siblings, D2): the subway's since A02, PATH's, the ferry's and NJ
    Transit's since D2. The railroad's publication gate leaves it out: since D3 the member
    is decorative there, parsed by validate_railroad_stop_times at a download and at the
    cached check of a usable cache (cached_archive_draws). What each costs is measured
    per loader in the 2026-10-12 ledger.

    A read that fails is refused through read_or_refuse, by member and in the reader's
    own words.
    """
    if not read_or_refuse(parse, member):
        raise StaticValidationError(f"{member} yielded no usable {what}", member=member)


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


async def cached_archive_draws(
    path: Path, gate: Callable[[zipfile.ZipFile], None], *, key: str
) -> bool:
    """Does the cache at `path`, which the loader has already called usable, pass the
    DECORATIVE gate (`gate`, the one the loader hands staged_fetch as
    validate_when_replacing)? Decided off the serving loop, and recorded.

    THE MODE OF THE TWO-MODE RULE IS THE CACHE'S OWN VERDICT AGAINST THE GATE THAT
    DECIDES IT (the operator's ruling on claude/static-rule-siblings, 2026-10-16). It had
    been the always-gate's, which does not open the decorative member, so a cache promoted
    without its lines or index still "served": an equally broken publication was refused
    over it, and a stale one was downloaded again at every start. Now the loader runs both
    gates at its cached check, once per attempt, and hands this verdict to staged_fetch as
    cache_serves: a cache that draws (or indexes) keeps its layer against a broken
    publication, and one that does not is replaced by any publication the always-gate
    passes, still degraded if that one is too.

    RECORDED: a failure is written under `key`'s degraded_members, so /api/status says what
    the cache serves without, a start over a volume that persisted included, until a
    promotion replaces the entry. A cache that draws clears an existing entry's members and
    creates no entry of its own. An environment fault while the gate reads the cache reads
    as "does not draw", as the always-gate's reads as "no cache"."""
    try:
        await asyncio.to_thread(validate_archive, path, gate)
    except StaticValidationError as exc:
        _record_degraded(key, {exc.member or "archive": str(exc)})
        return False
    if key in _ARCHIVE_STATUS:
        _ARCHIVE_STATUS[key]["degraded_members"] = {}
    return True


async def cached_archive_is_usable(path: Path, validate: Callable[[zipfile.ZipFile], None]) -> bool:
    """Is there a cached archive at `path` we can serve from? Decided OFF the serving loop.

    What every loader asks before its freshness test, as an await: the existence check
    here, and cached_archive_is_valid (the validation) in a worker thread through
    asyncio.to_thread. The reason is staged_fetch's below, and the subway is where it
    bites: its validator parses every member its load reads (A02), and on the live
    publication that is 1.1 to 1.5 s of CPU, every one of which a coroutine on the
    serving loop (a poller, /healthz) would otherwise wait out at every process start.
    Behind an await the loop keeps running while the thread parses.
    """
    if not path.exists():
        return False
    return await asyncio.to_thread(cached_archive_is_valid, path, validate)


def cache_is_fresh(path: Path, *, usable: bool, max_age_days: float) -> bool:
    """Is the cache at `path` fresh: usable, and written no more than `max_age_days` ago
    and not in the future? The one freshness test every static loader and the bus route
    index run (claude/static-rule-siblings, D4), so a loader added later cannot drift.

    THE FLOOR AT ZERO AGE. Age is now minus the file's mtime. An mtime ahead of the
    clock (the host clock stepped back after a promotion, a file restored with its
    times) gives a negative age, which `age < MAX_AGE_DAYS` alone called fresh, so such
    a cache was never re-fetched until real time caught up with it. Here it is stale,
    and the download that replaces it is stamped with the clock as it now reads.

    THE CLOCK IS READ HERE, AT THE CALL, through time.time: never captured in a default
    or passed in, so whatever moves time.time (a test, an audit script's virtual clock)
    moves this. NJ Transit's `now` is its service-date guard's clock, not this one.

    `usable` comes first and short-circuits, so a cache the check refused, or one that
    is not there, is never stat'ed."""
    if not usable:
        return False
    age = time.time() - path.stat().st_mtime
    return 0 <= age < max_age_days * 86400


# ---------------------------------------------------------------------------
# Per-archive status, for the operator surface
# ---------------------------------------------------------------------------

# key -> {last_promoted_at, last_download_error, failed_downloads, degraded_members}
# Module-level because the loaders have no app handle (the warmups call them as
# plain module functions, and two of them run off the event loop); routes/status.py
# reads it through archive_status(). The contract monitor needs none of this: it
# watches the same publications from the upstream side, so these two vantage
# points are independent on purpose. This one says "what did MY last download do",
# the monitor says "what is upstream serving right now".
_ARCHIVE_STATUS: dict[str, dict] = {}


def archive_status() -> dict[str, dict]:
    """A copy of the per-archive record, for /api/status."""
    return {
        key: {**value, "degraded_members": dict(value["degraded_members"])}
        for key, value in _ARCHIVE_STATUS.items()
    }


def reset_archive_status() -> None:
    """Clear the record. For tests: this is module state, so it outlives an app."""
    _ARCHIVE_STATUS.clear()


def _entry(key: str) -> dict:
    return _ARCHIVE_STATUS.setdefault(
        key,
        {
            "last_promoted_at": None,
            "last_download_error": None,
            "failed_downloads": 0,
            "degraded_members": {},
        },
    )


def _record_degraded(key: str, degraded: dict[str, str]) -> None:
    """What the cache being served lacks, found by its own decorative gate at the cached
    check (cached_archive_draws); the download fields are left as they are."""
    _entry(key)["degraded_members"] = dict(degraded)


def _record(
    key: str,
    *,
    promoted: bool,
    error: str | None = None,
    degraded: dict[str, str] | None = None,
) -> None:
    entry = _entry(key)
    if promoted:
        entry["last_promoted_at"] = time.time()
        # A promotion clears the error: the operator question these fields answer
        # is "is what I am serving current, and if not why", and a superseded
        # failure answers neither.
        entry["last_download_error"] = None
        entry["failed_downloads"] = 0
        # And replaces the degraded members wholesale: they describe the archive now
        # being served, which is this one. A refused download leaves them alone,
        # since the archive they describe is still the one serving.
        entry["degraded_members"] = dict(degraded or {})
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
    validate_when_replacing: Callable[[zipfile.ZipFile], None] | None = None,
    cache_serves: bool | None = None,
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
    3. Validate the staged bytes with the loader's own validator, in a worker thread.
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

    THE TWO-MODE RULE, for what `validate_when_replacing` checks (the operator's ruling
    on the A02 branch's review). `validate` is the always-gate: what the layer cannot
    do without, so a failure refuses the publication whatever the cache holds (the
    subway's stations, its routes join and its transfers floor, which the H2 ruling made
    a rejection). `validate_when_replacing` is for what is DECORATIVE (the subway's
    route lines): it runs on every publication, and what its failure does depends on
    what the publication would replace.
      - A cache that DRAWS is there (it passes `validate` and `validate_when_replacing`
        both): the publication is refused and the cache keeps serving, exactly as an
        always-gate failure would be. Bad geometry never replaces geometry the
        cache's own check found drawing (2026-09-05's F08); a fault on that check
        reads as "does not draw" (cached_archive_draws), so a fault costs the
        layer, never the stations.
      - No such cache: none at all (a cold start, which on this deployment is every
        deploy: the data directory does not persist, confirmed against production's
        promotion stamps of 2026-10-13), or one that is itself without the layer. The
        publication is promoted, since nothing is lost, and the failure is recorded on
        the archive's status as a degraded member with the parser's reason, published on
        /api/status. Refusing it would trade every station for the lines, or keep a
        cache no better than the publication, and the loader of the decorative layer
        degrades to nothing as it always has.
    THE SIBLINGS' DECORATIVE MEMBERS take the same rule since claude/static-rule-siblings
    (D3): the railroad's stop_times.txt (the routes-per-station index) and NJ Transit's
    shapes.txt (its route lines, where 15c had promoted any publication without usable
    geometry). An absent member is a gate failure like an unreadable one, as the
    subway's always was (require_decoration).

    WHOSE VERDICT DECIDES THE MODE: the caller's, when it has one. `cache_serves` is
    what the loader concluded about `dest` at the start of this attempt with BOTH gates
    (cached_archive_is_usable, then cached_archive_draws: the operator's ruling of
    2026-10-16), so the mode and the loader cannot disagree about the same cache in the
    same attempt. The A02 branch's review found what a second, independent read here
    did: a fault while re-reading a cache that serves promoted geometry that draws
    nothing over lines that draw, and an attempt to tell the environment's faults from
    the bytes' by errno called "keep" a cache the loader had already called unusable,
    so the publication was refused, the loader raised and no station served (a
    bit-rotted end-of-central-directory offset raises OSError(EINVAL), with an errno,
    from the bytes). Without a verdict (None) the cache is read here with both gates,
    as every loader reads it.
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
        # OFF THE SERVING LOOP (A02's measurement, A09's arm 1). Validation is
        # synchronous CPU, and the subway's parses every member its load reads: 1.1 to
        # 1.5 s on the live publication, during which every other coroutine sharing
        # the loop (the pollers, /healthz) used to wait. In a worker thread the loop
        # keeps running, and a caller's expired asyncio.timeout is delivered HERE, at
        # this await, before the promotion below rather than after it (A09 recorded
        # an 80 ms validator running through a 10 ms deadline and promoting). The
        # thread cannot be stopped and finishes on its own; the stage it reads is
        # unlinked below, which on Linux leaves its open handle readable, and a late
        # result has nowhere to go.
        await asyncio.to_thread(validate_archive, stage, validate)
        degraded: dict[str, str] = {}
        if validate_when_replacing is not None:
            try:
                await asyncio.to_thread(validate_archive, stage, validate_when_replacing)
            except StaticValidationError as exc:
                if cache_serves is None:
                    cache_serves = await cached_archive_is_usable(
                        dest, validate
                    ) and await cached_archive_is_usable(dest, validate_when_replacing)
                if cache_serves:
                    raise  # a cache that draws is there: refuse, as above
                degraded[exc.member or "archive"] = str(exc)
                logger.warning(
                    "Promoting %s publication with a degraded member (%s): there is no "
                    "cached archive at %s whose member draws to keep",
                    label,
                    exc,
                    dest,
                )
    except BaseException as exc:
        stage.unlink(missing_ok=True)
        # A cancellation is not a publication failure and must not be recorded as
        # one: shutdown would otherwise show up on /api/status as a bad upstream.
        if not isinstance(exc, asyncio.CancelledError):
            _record(key, promoted=False, error=describe_failure(exc))
            if isinstance(exc, StaticValidationError):
                # Says which of the two it is, because a cold start has no cache to keep
                # and an operator sent looking for one at `dest` finds nothing there.
                logger.warning(
                    "Rejected %s publication (%s); %s %s",
                    label,
                    exc,
                    "keeping the cached archive at" if dest.exists() else "no cached archive at",
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
    _record(key, promoted=True, degraded=degraded)
    logger.info("Promoted %s to %s", label, dest)
