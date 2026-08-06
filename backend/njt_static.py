"""Download and load the static GTFS for NJ Transit Rail (15a).

The 15a data foundation: NJ Transit's stops, routes, trips and stop_times loaded
into memory, fetched through the token door (njt_auth) so the credentialed path is
exercised from birth rather than landing untested with 15b's realtime work.
Modeled on ferry_static (flat stops, no parent/child split) rather than on
path_static, with the differences this feed forces spelled out below. main.py's
lifespan warms it in the background onto its own app.state fields (njt_static,
njt_stops, ...): NJT stop ids are small integers that collide with MTA, PATH and
ferry ids (84.9% of NJT stop ids already mean something else in one of our other
feeds, and stop_id 112 alone names four different places across them), so they are
never merged into any shared namespace.

WHAT THIS FEED DOES NOT SHIP, probed 2026-08-05 against GTFS_NJT_Rail.zip and
treated as ground truth by the validators below:

  * NO calendar.txt AND NO feed_info.txt. Neither is missing by accident and
    neither is coming: service is expressed as 8,697 ADDITIVE calendar_dates rows
    spanning 2026-07-28 to 2027-05-01. Requiring either member (every other loader
    in this app requires neither, but the contract monitor's staleness check reads
    feed_info everywhere it exists) would reject every valid NJT publication. The
    staleness question therefore has a different answer here; see
    validate_njt_archive's service-date guard.
  * NO wheelchair data anywhere. Not on stops, not on trips. The stop record
    deliberately carries no wheelchair field rather than a hardcoded False, which
    would read downstream as an affirmative "not accessible" that this feed never
    said. Ferry's marker has one because the ferry feed publishes one.
  * NO parent stations, NO location_type. 172 flat stops with ids 1..176.

WHAT IT SHIPS THAT NOTHING ELSE HERE DOES:

  * route_type=113 on all 12 routes. That is the GTFS EXTENDED route type for
    "Rail Service", and any consumer switch-casing the classic 0-7 basic types
    falls straight through it. Tolerated explicitly in _parse_routes; the repo-wide
    sweep for 0-7 assumptions is recorded in the 15a PR body.
  * route_text_color EMPTY on all 12 routes, so a renderer must have its own
    contrast fallback rather than trusting the feed.
  * Port Jervis has NO route of its own. Its nine stations are served under the
    MAIN (route_id 6) and BERG (route_id 5) ids, with the Port Jervis identity
    living only in trip_headsign. Pascack Valley, by contrast, IS first class:
    route_id 13, PASC. Anything that later groups NJT service by line has to know
    that, which is why the fixture generator pins both cases.
  * route_id overlap with the LIRR is 75%, another reason nothing here shares a
    namespace with another system.

shapes.txt IS PRESENT AND DELIBERATELY UNPARSED (15a decision). It is 10 MB of
the 11.1 MB unzipped payload and nothing in 15a or 15b consumes it: placed-train
interpolation uses stop coordinates. It stays out of _REQUIRED_MEMBERS too, so a
publication that dropped it would still serve; parsing and indexing it is 15c's
call, when the line-drawing decision actually asks for geometry.

REALTIME, FOR THE PHASE THAT FOLLOWS (15b consumes this module, never re-parses
it): the 2026-08-05 probes measured 100% trip_id persistence across 633 survivor
observations and a 112/112 static join, with entity.id equal to the train number
equal to trip_short_name at 745/745. So NJT needs no identity matcher: trip_id is
the join key and trip_short_name is the second one. That is why the trips index
below carries short_name as a first-class field.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import IO

import env_seams
import njt_auth
from feeds.shared import NYC_TZ
from static_routes import fold_stop_routes
from static_shared import (
    StaticValidationError,
    cached_archive_is_valid,
    parse_member,
    require_members,
    require_parsed,
    staged_fetch,
)

logger = logging.getLogger(__name__)

DATA_DIR = env_seams.directory("DATA_DIR", "data")
_STATIC_DIR = DATA_DIR / "gtfs_static"

# Verified 2026-08-05: POST multipart/form-data with a token form field, 200,
# ~3.3 MB zipped / 11.1 MB unzipped, 7 tables. Answered in 428 ms overnight and
# 8.9 s at peak, which is why the transfer deadline stays generous. Overridable
# (C6), used whole: the seam changes WIRING, never behavior, so the loader still
# mints and POSTs against a simulator exactly as it does against NJ Transit.
NJT_STATIC_URL = env_seams.url(
    "NJT_STATIC_URL", "https://raildata.njtransit.com/api/GTFSRT/getGTFS"
)
NJT_STATIC_ZIP = _STATIC_DIR / "gtfs_njt.zip"

# Re-download when the cached copy is older than this, the same policy as PATH,
# the ferry and the railroads. NJ Transit republishes on schedule changes, and the
# service-date guard below is the thing that actually notices an expired feed;
# this is just the ordinary refresh cadence.
MAX_AGE_DAYS = 30

# Members the NJT load READS and cannot degrade around.
#
# calendar.txt and feed_info.txt are ABSENT BY DESIGN in this feed (see the module
# docstring) and requiring either would reject every valid publication. shapes.txt
# is present upstream but deliberately not required: 15a does not parse it, so a
# publication that dropped it is still fully servable and rejecting one would trade
# a working map for geometry nothing reads yet.
_REQUIRED_MEMBERS = (
    "agency.txt",
    "routes.txt",
    "trips.txt",
    "stops.txt",
    "stop_times.txt",
    "calendar_dates.txt",
)

# GTFS calendar_dates exception_type for ADDED service. Only added rows extend the
# feed's service span: a removal row says a service does NOT run that day, so
# folding removals into the span could let a feed whose real service ended in
# March look current because someone published a cancellation for December.
_SERVICE_ADDED = "1"


def _parse_stops(raw: IO[bytes]) -> dict[str, dict]:
    """stops.txt -> stop_id -> {id, name, lat, lon}.

    FLAT, like the ferry and unlike PATH: as of the 2026-08-05 probe this feed ships
    no location_type and no parent_station at all, so every row with a usable id and
    coordinate is a marker. Rows with a blank stop_id or a missing/malformed
    coordinate are skipped; first-writer-wins on a duplicate stop_id.

    THE ONE THING NOT ASSUMED, because the cost of being wrong is visible to riders:
    a row whose location_type is anything but blank or "0" is NOT a boardable stop.
    location_type 1 is a parent station, 2 an entrance, 3 a generic node, 4 a
    boarding area, and none of them is a place a train calls at. This feed publishes
    none of them today, so the filter is inert; if it ever does, the alternative is
    that /api/njt-stops sprouts a station pin and a street-entrance pin a few metres
    from the platform that actually carries the routes, which is precisely the
    silently-wrong map this codebase keeps removing. Skipping them is right whatever
    the feed does, and it costs one comparison.

    What the filter deliberately does NOT do is invent a parent/child fold. If
    parent stations ever appear, the marker set should probably become the parents
    (the subway and PATH shape) and that is a design decision for a human, not
    something to infer at parse time. The contract monitor watches for exactly that
    shape change so the decision is prompted rather than discovered.

    NO WHEELCHAIR FIELD, deliberately. The ferry marker carries one because the
    ferry feed publishes wheelchair_boarding; NJ Transit's carries no accessibility
    data at all, and a hardcoded False would be indistinguishable downstream from
    an affirmative "this station is not accessible", which the feed never says.
    Absence is the honest answer, so the key is absent.
    """
    stops: dict[str, dict] = {}
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        stop_id = (row.get("stop_id") or "").strip()
        if not stop_id or stop_id in stops:
            continue
        if (row.get("location_type") or "").strip() not in ("", "0"):
            continue  # not a boardable stop; see the docstring
        try:
            lat = float(row.get("stop_lat") or "")
            lon = float(row.get("stop_lon") or "")
        except ValueError:
            continue
        stops[stop_id] = {
            "id": stop_id,
            "name": (row.get("stop_name") or "").strip() or None,
            "lat": lat,
            "lon": lon,
        }
    return stops


def _parse_routes(raw: IO[bytes]) -> dict[str, dict]:
    """routes.txt -> route_id -> {long_name, short_name, color, text_color, type}.

    ROUTE_TYPE 113 IS TOLERATED EXPLICITLY and passed through as a string. All 12
    NJT routes carry it: it is the GTFS EXTENDED type for "Rail Service", not one
    of the classic 0-7 basic types, so anything that switch-cases 0-7 falls through
    it. This parser deliberately does not validate, map, or normalize the value; it
    carries it so a later consumer can see what the feed actually said rather than
    inferring rail from a value it never checked. Rows with no route_id are
    skipped; first-writer-wins on a duplicate.

    text_color is read like PATH's and the ferry's, and is EMPTY on all 12 NJT
    routes as of the 2026-08-05 probe. It stays None rather than being defaulted to
    a contrast colour here: picking the readable foreground is a rendering
    decision, and inventing one in the parser would hide that the feed said
    nothing.
    """
    routes: dict[str, dict] = {}
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        route_id = (row.get("route_id") or "").strip()
        if not route_id or route_id in routes:
            continue
        routes[route_id] = {
            "long_name": (row.get("route_long_name") or "").strip() or None,
            "short_name": (row.get("route_short_name") or "").strip() or None,
            "color": (row.get("route_color") or "").strip() or None,
            "text_color": (row.get("route_text_color") or "").strip() or None,
            "type": (row.get("route_type") or "").strip() or None,
        }
    return routes


def _parse_trips(raw: IO[bytes]) -> dict[str, dict]:
    """trips.txt -> trip_id -> {route_id, direction_id, service_id, headsign,
    short_name}, each a stripped string or None when blank. Rows with no trip_id
    are skipped; first-writer-wins on a duplicate trip_id.

    short_name IS THE TRAIN NUMBER, and it is 15b's second join key. The
    2026-08-05 probes measured entity.id == the train number == trip_short_name at
    745 of 745 observations, alongside 100% trip_id persistence across 633 survivor
    observations. So NJT needs no identity matcher (unlike PATH, whose bridge feed
    churns every id): a realtime entity joins here by trip_id, and short_name
    confirms it. Both are carried rather than derived.

    headsign matters more here than in any other feed we load, and not for display:
    PORT JERVIS HAS NO ROUTE OF ITS OWN. Its nine stations are served under the
    MAIN (6) and BERG (5) route ids, and the only place the Port Jervis identity
    appears anywhere in this feed is trip_headsign. Anything that later groups NJT
    service by line has to read it from here.
    """
    trips: dict[str, dict] = {}
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        trip_id = (row.get("trip_id") or "").strip()
        if not trip_id or trip_id in trips:
            continue
        trips[trip_id] = {
            "route_id": (row.get("route_id") or "").strip() or None,
            "direction_id": (row.get("direction_id") or "").strip() or None,
            "service_id": (row.get("service_id") or "").strip() or None,
            "headsign": (row.get("trip_headsign") or "").strip() or None,
            "short_name": (row.get("trip_short_name") or "").strip() or None,
        }
    return trips


def gtfs_seconds(value: str) -> int | None:
    """ "HH:MM:SS" after service-day midnight, as seconds, or None when unusable.

    HOURS MAY EXCEED 24 and routinely do: a train departing 00:40 on the day after
    its service day is published as "24:40:00", which is why this returns seconds
    since service-day midnight rather than a time of day. Converting to a wall
    clock needs the service date, which lives in calendar_dates, so that join stays
    the caller's business and this stays a pure lexical parse.
    """
    parts = (value or "").strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError:
        return None
    if minutes < 0 or seconds < 0 or hours < 0:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _parse_stop_times(raw: IO[bytes]) -> dict[str, list[dict]]:
    """stop_times.txt -> trip_id -> [{stop_id, seq, arrival, departure}] ordered by
    stop_sequence.

    RICHER THAN THE OTHER LOADERS' stop_times parsers, which keep only the stop
    ids, because 15a owes the panel era a scheduled arrivals index and that needs
    times. arrival and departure are seconds after service-day midnight (see
    gtfs_seconds) or None when the feed leaves them blank, which it does at some
    intermediate stops.

    Rows with a blank trip_id/stop_id or a non-integer stop_sequence are skipped;
    a duplicate (trip, sequence) keeps the first row seen, matching the
    first-writer-wins discipline of every other parser here.
    """
    raw_seqs: dict[str, dict[int, dict]] = defaultdict(dict)
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        trip_id = (row.get("trip_id") or "").strip()
        stop_id = (row.get("stop_id") or "").strip()
        if not trip_id or not stop_id:
            continue
        try:
            seq = int((row.get("stop_sequence") or "").strip())
        except ValueError:
            continue
        raw_seqs[trip_id].setdefault(
            seq,
            {
                "stop_id": stop_id,
                "seq": seq,
                "arrival": gtfs_seconds(row.get("arrival_time") or ""),
                "departure": gtfs_seconds(row.get("departure_time") or ""),
            },
        )
    return {
        trip_id: [call for _seq, call in sorted(seqs.items())] for trip_id, seqs in raw_seqs.items()
    }


def _service_day(value: str) -> str | None:
    """A GTFS YYYYMMDD date, or None when the value is not one.

    EIGHT DIGITS IS NOT A DATE, and the difference decides whether this feed's only
    staleness check works. The guard below compares dates as zero-padded strings,
    which is exact for real dates and meaningless for impossible ones: "20261301"
    (month 13) sorts above every real date in 2026, so a single such row anywhere in
    an 8,697-row table would make a fully expired schedule look like one running
    into next year. The monitor cannot cover for it either, because it downgrades an
    unparseable date to WARN and a WARN never fails a run.

    So the date is really parsed, and a value that is not a calendar day is dropped
    exactly like a malformed coordinate is dropped in _parse_stops: the row simply
    does not contribute a service day, the max falls back to a real date (or to
    None, which already raises), and the guard keeps meaning what it says.
    """
    text = (value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _parse_calendar(raw: IO[bytes]) -> str | None:
    """calendar.txt -> the latest end_date over all services, or None.

    THIS FEED SHIPS NO calendar.txt (probed 2026-08-05), which is why
    calendar_dates.txt is the whole schedule and why the service-date guard reads
    it. This parser exists for the publication that stops being true.

    WITHOUT IT, a feed that adopted the ordinary GTFS shape would be MISDIAGNOSED
    rather than merely unsupported, and that is the reason to write it now rather
    than when it happens. Three measured variants, all against a calendar.txt
    running to 2027-12-31: a calendar_dates holding only removals is rejected as
    "schedules no service days"; a conventional exception-style calendar_dates
    whose few additive rows are all in the past is rejected as "the schedule has
    expired"; and one with a single near-future addition is ACCEPTED while
    publishing that addition as the feed's end date, so /api/status, the warmup log
    and the monitor's band all report a number 15 months early and the guard starts
    rejecting a perfectly good feed on that day. Every one of those is a wrong
    answer with a confident message, which is worse than an unsupported feed.

    Folding end_date into the span fixes all three at once and asks nothing of a
    feed that never grows the member. A row with no usable end_date contributes
    nothing; a table of them leaves the span exactly where calendar_dates put it.
    """
    latest: str | None = None
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        end = _service_day(row.get("end_date") or "")
        if end is not None and (latest is None or end > latest):
            latest = end
    return latest


def _parse_calendar_dates(raw: IO[bytes]) -> dict[str, set[str]]:
    """calendar_dates.txt -> service_id -> {YYYYMMDD} of ADDED service days.

    THIS FEED HAS NO calendar.txt, so this table is not the exception list it is in
    a normal GTFS feed: it is the WHOLE schedule. 8,697 additive rows spanning
    2026-07-28 to 2027-05-01 as of the 2026-08-05 probe.

    Only exception_type=1 (added) rows are kept, and that is a correctness choice
    rather than tidiness: a removal row says a service does NOT run on a day, so
    counting one toward the feed's service span would let a feed whose real service
    ended in March pass the staleness guard on the strength of a cancellation
    published for December. Rows with a blank service_id or a date that is not a
    real calendar day are skipped (see _service_day), so neither a malformed row nor
    an impossible one can become a fake service day.
    """
    dates: dict[str, set[str]] = defaultdict(set)
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        service_id = (row.get("service_id") or "").strip()
        exception = (row.get("exception_type") or "").strip()
        if not service_id or exception != _SERVICE_ADDED:
            continue
        date = _service_day(row.get("date") or "")
        if date is None:
            continue
        dates[service_id].add(date)
    return dict(dates)


def latest_service_date(calendar_dates: dict[str, set[str]]) -> str | None:
    """The last day this publication schedules any service, as YYYYMMDD, or None
    when it schedules none at all. Pure, so the validator, the warmup log and the
    contract monitor all read the same number from the same code."""
    latest = max((max(days) for days in calendar_dates.values() if days), default=None)
    return latest


def _today(now: float | None = None) -> str:
    """Today in New York, as YYYYMMDD.

    NEW YORK, NOT UTC, and the difference is a real off-by-one rather than
    pedantry. A GTFS service date is a local calendar day at the agency, and for
    five hours every evening (four under EDT) the UTC date is already tomorrow. A
    guard reading UTC would declare a feed whose last service day is today expired
    at 20:00 local, on the last evening it is actually serving trains. The clock is
    injectable so the boundary case (max == today, exactly) is testable without
    waiting for midnight.
    """
    stamp = time.time() if now is None else now
    return datetime.fromtimestamp(stamp, NYC_TZ).strftime("%Y%m%d")


def validate_njt_archive(zf: zipfile.ZipFile, *, now: float | None = None) -> None:
    """Can we serve NJ Transit from this archive? Raises StaticValidationError if not.

    Three gates, in the order a failure is most useful to read:

    1. REQUIRED MEMBERS. Presence only, and the LIST is the interesting part:
       calendar.txt and feed_info.txt are not on it, because this feed ships
       neither and requiring either would reject every valid publication (see the
       module docstring). shapes.txt is not on it either, because 15a does not
       parse it.

    2. NONEMPTY THROUGH THE LOADER'S OWN PARSERS, the finding-4 rule this loader
       gets from birth rather than growing later like the other four did. A
       headers-only stops.txt is structurally perfect and parses cleanly to
       nothing; promoting that reaches "ready" with an empty map and nothing
       retrying. Three tables are gated here rather than only stops: a feed with
       stops and no trips places markers that can never carry a train, which is the
       same silent lie one table over.

       stop_times.txt IS NOT GATED HERE, and its absence from this list is what
       makes the split with validate_njt_publication real. It is the one expensive
       table, this validator runs on EVERY load, and the publication gate below
       parses it in full before any archive may be promoted. So a cached archive
       cannot contain a stop_times that never passed that gate, and a load pays for
       the three cheap tables rather than for all four. (An earlier draft gated all
       four here, which quietly made the two validators equivalent: the publication
       gate's extra parse became unreachable, and the test that claimed to pin it
       could not fail.)

    3. THE SERVICE-DATE GUARD, this feed's staleness truth. Every other loader in
       this app can defer staleness to the contract monitor's feed_info reader;
       there is NO feed_info.txt here, so there is no feed_end_date to check and the
       question has to be answered from the schedule itself. The span is the later
       of calendar.txt's end_date (absent today, folded in for the publication that
       changes that; see _parse_calendar) and the latest ADDED calendar_dates day.
       The rule: that span must reach today or later, in New York local time. A
       publication whose service has entirely expired schedules nothing for today or
       any day after, so it cannot be served from, and accepting one would replace a
       working archive with a dead calendar.

       THE VALIDATOR DRAWS THE HARD LINE, THE MONITOR BANDS THE APPROACH. This
       raises only when the feed leads today by NOTHING; the contract monitor
       WARNs while the lead is under 30 days, so a human sees the edge coming long
       before this gate fires. Splitting it that way is deliberate: a validator
       that rejected an almost-expired feed would drop a system that is still
       serving correct trains today, which is a worse outcome than the warning.

    `now` is injected so the boundary (latest == today, which PASSES) is testable.
    """
    require_members(zf, _REQUIRED_MEMBERS)
    require_parsed(lambda: parse_member(zf, "stops.txt", _parse_stops), "stops.txt", "stops")
    require_parsed(lambda: parse_member(zf, "routes.txt", _parse_routes), "routes.txt", "routes")
    require_parsed(lambda: parse_member(zf, "trips.txt", _parse_trips), "trips.txt", "trips")
    latest = _service_span(zf)
    if latest is None:
        raise StaticValidationError("calendar_dates.txt schedules no service days")
    today = _today(now)
    if latest < today:
        # String comparison is exact for zero-padded YYYYMMDD and needs no date
        # arithmetic, and _service_day has already dropped anything that is not a
        # real calendar day, so an impossible value cannot sort its way past this.
        raise StaticValidationError(
            f"service ended {latest}, before today ({today}); the schedule has expired"
        )


def _service_span(zf: zipfile.ZipFile) -> str | None:
    """The last day this archive schedules any service, as YYYYMMDD, or None.

    The later of calendar.txt's end_date and the latest added calendar_dates day.
    calendar.txt is absent from this feed today, so in practice this is the
    calendar_dates answer; reading both is what keeps a publication that adopts the
    ordinary GTFS shape from being confidently misdiagnosed rather than merely
    unsupported (see _parse_calendar for the three measured variants).
    """
    try:
        calendar_dates = parse_member(zf, "calendar_dates.txt", _parse_calendar_dates)
        # Present-or-absent rather than required: this feed ships no calendar.txt,
        # and _REQUIRED_MEMBERS deliberately does not ask for one.
        try:
            member = zf.open("calendar.txt")
        except KeyError:
            calendar_end = None
        else:
            with member as raw:
                calendar_end = _parse_calendar(raw)
    except (UnicodeDecodeError, csv.Error) as exc:
        # The same shape-naming message require_parsed produces for the tables it
        # gates. Checked by hand rather than through require_parsed because
        # emptiness is not the only question asked here: the guard needs the parsed
        # value, and parsing twice to reuse a helper would buy nothing.
        raise StaticValidationError("the service calendar is not readable as CSV") from exc
    latest = latest_service_date(calendar_dates)
    if calendar_end is not None and (latest is None or calendar_end > latest):
        return calendar_end
    return latest


def validate_njt_publication(zf: zipfile.ZipFile, *, now: float | None = None) -> None:
    """The gate a NEW archive must pass before it may replace the cached one.

    Strictly stronger than validate_njt_archive, the same split every other loader
    keeps and for the same reason: this runs the REAL parse of every table the load
    reads, so an archive that would fail the load can never be promoted over a
    working one. A publication with clean stops, routes and trips but an undecodable
    byte in stop_times.txt (which the light validator does not open at all) would
    otherwise pass, be renamed over the last-known-good, and then be discarded by
    the residual arm in the loader: one bad publication, both archives gone.

    stop_times.txt IS THE DIFFERENCE, and it is the whole difference: one table, one
    added gate. Between the two validators every table the load reads is parsed for
    real before an archive may be promoted, and the expensive one is paid for once
    per download attempt rather than on every load.

    DELIBERATELY NOT a blanket `_parse_open(zf)` on top. That is what this used to
    be, and it made the two validators equivalent: every table _parse_open touches
    was already gated above, so the extra call could be deleted without failing a
    single test, and the strength split the docstring promised did not exist. One
    named gate for the one uncovered table is both cheaper and actually falsifiable.
    """
    validate_njt_archive(zf, now=now)
    require_parsed(
        lambda: parse_member(zf, "stop_times.txt", _parse_stop_times),
        "stop_times.txt",
        "stop times",
    )


async def _download_via_token(url: str, dest: Path, deadline_s: float) -> None:
    """The transfer staged_fetch injects for NJT: one POST through the token door.

    NOT the shared _stream_to_file, and it cannot be: every RailData endpoint is
    POST multipart/form-data with the token as a form field, so a GET gets nothing.
    Going through njt_auth.njt_post rather than posting here is what makes the
    invalid-token dance (HTTP 500 + {"errorMessage":"Invalid token."}, one re-mint,
    one retry) apply to the static download too, and what exercises that path from
    birth instead of leaving it for 15b's realtime work to discover.

    The body is buffered whole rather than streamed. At ~3.3 MB that is a
    non-issue, and the alternative would mean reimplementing the auth retry around
    a streaming response whose first bytes arrive before the status can be judged.
    """
    async with asyncio.timeout(deadline_s):
        body = await njt_auth.njt_post(url, {})
    dest.write_bytes(body)


async def _download_zip(*, now: float | None = None) -> None:
    """Stage, validate, then promote the NJ Transit archive (see static_shared)."""

    def validate(zf: zipfile.ZipFile) -> None:
        validate_njt_publication(zf, now=now)

    await staged_fetch(
        NJT_STATIC_URL,
        NJT_STATIC_ZIP,
        validate,
        key="njt",
        label="NJ Transit static GTFS",
        download=_download_via_token,
    )


def derive_njt_stop_routes(
    trips: dict[str, dict], stop_times: dict[str, list[dict]]
) -> dict[str, list[str]]:
    """Pure: stop_id -> sorted [route_id] serving it (the H5 pattern).

    NJT stops are flat (no parent/child fold), so the stop_times ids join straight
    to the markers. Delegates the join to static_routes.fold_stop_routes after
    projecting each trip's route_id and each call's stop_id, so the fold itself
    stays one implementation across all five systems. No zip read, so the warmup
    builds it from app.state without re-parsing.

    WHAT THIS INDEX WILL AND WILL NOT SAY ABOUT PORT JERVIS: its nine stations come
    back carrying MAIN (6) and BERG (5), because those are the route ids their
    trips run under. There is no Port Jervis route id to return, and inventing one
    here would be a fiction the feed never published; the identity lives in
    trip_headsign and belongs to whatever surfaces it. The fixture goldens pin this
    exact behavior so a later phase cannot quietly "fix" it into a lie.
    """
    trip_routes = {trip_id: trip.get("route_id") for trip_id, trip in trips.items()}
    trip_stops = {
        trip_id: [call["stop_id"] for call in calls] for trip_id, calls in stop_times.items()
    }
    return fold_stop_routes(trip_routes, trip_stops)


def build_njt_trip_index(trips: dict[str, dict]) -> dict[str, dict]:
    """Pure: trip_id -> {route_id, headsign, short_name}, 15b's join target.

    A NARROWED PROJECTION of the parsed trips table rather than the table itself,
    and the narrowing is the point: these three fields are what a realtime entity
    needs to become a rider-facing train (which line, where it is going, which
    train number), and publishing exactly them says so. direction_id and service_id
    stay in the full parsed table for anything that needs them.

    trip_id is the primary join key and short_name the second, both measured at
    100% on 2026-08-05 (633 survivor observations for trip_id persistence, 745/745
    for entity.id == train number == trip_short_name). No matcher is needed or
    wanted here; PATH's synthesized identity exists because its bridge feed churns
    ids, and NJ Transit's does not.
    """
    return {
        trip_id: {
            "route_id": trip.get("route_id"),
            "headsign": trip.get("headsign"),
            "short_name": trip.get("short_name"),
        }
        for trip_id, trip in trips.items()
    }


def build_njt_stop_schedule(
    trips: dict[str, dict], stop_times: dict[str, list[dict]]
) -> dict[str, list[dict]]:
    """Pure: stop_id -> scheduled calls at that stop, soonest-first within a service
    day, for the panel era.

    Each call is {trip_id, route_id, headsign, train, arrival, departure, seq}
    where arrival/departure are seconds after service-day midnight (they can exceed
    86400; see gtfs_seconds) and `train` is trip_short_name, the number a rider
    reads off a departure board.

    SCHEDULED, NOT PREDICTED, and the naming keeps that visible: this index says
    what the timetable claims, with no realtime input of any kind, and a consumer
    that shows it must label it as scheduled the way the AirTrain layer does. It is
    also service-day scoped rather than wall-clock scoped: turning a call into an
    instant needs the trip's service_id joined through calendar_dates, which is the
    caller's join and deliberately not baked in here.

    Sorted by arrival, falling back to departure for a call that publishes only
    one, and finally by (trip_id, seq) so the order is deterministic across
    regenerations rather than dependent on dict insertion. A call with neither time
    sorts last rather than being dropped: it is still a real stop on a real trip.
    """
    by_stop: dict[str, list[dict]] = defaultdict(list)
    for trip_id, calls in stop_times.items():
        trip = trips.get(trip_id) or {}
        for call in calls:
            by_stop[call["stop_id"]].append(
                {
                    "trip_id": trip_id,
                    "route_id": trip.get("route_id"),
                    "headsign": trip.get("headsign"),
                    "train": trip.get("short_name"),
                    "arrival": call["arrival"],
                    "departure": call["departure"],
                    "seq": call["seq"],
                }
            )
    ordered: dict[str, list[dict]] = {}
    for stop_id, calls in by_stop.items():
        calls.sort(key=_schedule_sort_key)
        ordered[stop_id] = calls
    return ordered


# Sorts a scheduled call: by its best available time, then deterministically.
# float("inf") for a call with neither time puts it last without a branch, and
# without pretending it happens at midnight (0 would sort it first, ahead of every
# real departure, which is the wrong end of the list to be wrong at).
def _schedule_sort_key(call: dict) -> tuple[float, str, int]:
    when = call["arrival"]
    if when is None:
        when = call["departure"]
    return (float("inf") if when is None else float(when), call["trip_id"], call["seq"])


def _parse_zip(zip_path: Path) -> dict:
    """Parse the NJ Transit GTFS zip into {stops, routes, trips, stop_times,
    calendar_dates} in a single open."""
    with zipfile.ZipFile(zip_path) as zf:
        return _parse_open(zf)


def _parse_open(zf: zipfile.ZipFile) -> dict:
    """The parse itself, over an already-open archive, so validate_njt_publication
    can run the REAL load against a staged file that has no cache path yet.

    Every member read here is required, so unlike the PATH and ferry parsers there
    is no per-member leniency: those tolerate a missing routes.txt or stop_times.txt
    at parse time because pre-C5 caches predate the requirement, and this loader has
    no such history to survive. shapes.txt is not opened at all (15a decision, see
    the module docstring).
    """
    with zf.open("stops.txt") as raw:
        stops = _parse_stops(raw)
    with zf.open("routes.txt") as raw:
        routes = _parse_routes(raw)
    with zf.open("trips.txt") as raw:
        trips = _parse_trips(raw)
    with zf.open("stop_times.txt") as raw:
        stop_times = _parse_stop_times(raw)
    with zf.open("calendar_dates.txt") as raw:
        calendar_dates = _parse_calendar_dates(raw)
    return {
        "stops": stops,
        "routes": routes,
        "trips": trips,
        "stop_times": stop_times,
        "calendar_dates": calendar_dates,
    }


async def load_njt_static(*, now: float | None = None) -> dict:
    """Ensure/refresh the NJ Transit GTFS zip and parse it.

    Returns {stops, routes, trips, stop_times, calendar_dates} on success, or {} on
    any failure. Lenient by design, matching load_path_static and
    load_ferry_static: a download or parse failure logs and yields an EMPTY result
    rather than raising, because the warmup owns retrying, and NJ Transit is a
    single system so the caller reads an empty result as the whole group failing.

    ONE EXCEPTION TO THE LENIENCY, AND IT IS THE POINT OF THE 15a CREDENTIALS
    DECISION: absent credentials raise NjtNotConfigured, before the cache is
    consulted and before any network call. Not-configured is a DIFFERENT STATE from
    failed, not a quieter shade of it, and collapsing it into the empty-dict return
    would put an unconfigured deployment into a retry loop that can never succeed
    and would hammer a mint endpoint that would reject it anyway. The warmup catches
    the type, publishes the state, and stops.

    The disk cache is deliberately not consulted either. A process with no
    credentials is not an NJT deployment, even if a previous one left a valid
    archive on the volume; serving that would make /api/njt-stops answer while the
    status honestly said "not configured", which is exactly the kind of composite
    lie this codebase keeps removing.
    """
    if not njt_auth.is_configured():
        raise njt_auth.NjtNotConfigured(
            f"{njt_auth.USERNAME_VAR}/{njt_auth.PASSWORD_VAR} are not set; "
            "NJ Transit is not configured"
        )
    zip_path = NJT_STATIC_ZIP

    def validate(zf: zipfile.ZipFile) -> None:
        validate_njt_archive(zf, now=now)

    # FRESH MEANS VALID AND RECENT (C5), and for this feed "valid" carries the
    # service-date guard, so a cached archive whose schedule expired overnight is
    # rejected here and re-downloaded rather than served for another 30 days.
    usable = zip_path.exists() and cached_archive_is_valid(zip_path, validate)
    fresh = usable and time.time() - zip_path.stat().st_mtime < MAX_AGE_DAYS * 86400
    if not fresh:
        try:
            await _download_zip(now=now)
        except njt_auth.NjtNotConfigured:
            # Cannot happen behind the guard above, and re-raised rather than
            # swallowed if it somehow does: turning it into an empty result here
            # would convert the distinct not-configured state into an ordinary
            # failure two frames from where the distinction was made.
            raise
        except Exception as exc:
            if not usable:
                logger.warning("NJ Transit static GTFS download failed (%s); no cached copy", exc)
                return {}
            # Serving old while new is bad, INCLUDING past MAX_AGE_DAYS: the age
            # policy exists to pick up upstream's corrections, so it yields to
            # validity rather than dropping a working system. The cached archive
            # already passed the service-date guard above, so this cannot serve an
            # expired schedule. staged_fetch recorded the reason for /api/status.
            logger.warning(
                "NJ Transit static GTFS re-download failed (%s); using the cached copy", exc
            )
    try:
        data = _parse_zip(zip_path)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, csv.Error, OSError) as exc:
        # The residual below the CACHED-archive validator. Reaching here means the
        # bytes on disk were fully parseable when they were promoted and are not now
        # (rot, a truncated write), or no file was ever written because the first
        # download failed. Unlink is right in exactly that case: this file IS the
        # cache, nothing better sits behind it, and keeping it would wedge every
        # retry on the same bytes. A freshly promoted archive cannot land here,
        # because validate_njt_publication ran this same parse before the rename.
        logger.warning("Cached NJ Transit static GTFS is unparseable (%s); discarding", exc)
        zip_path.unlink(missing_ok=True)
        return {}
    if not data["stop_times"]:
        # The member is required (so it is present) and every PUBLICATION is gated
        # on it parsing to something, but the light cached-archive validator does
        # not open it (see validate_njt_archive), so a cache that rotted since it
        # was promoted can still reach here empty. The group stays ready because
        # stops still place markers; what degrades is the routes-per-station index
        # and the scheduled stop schedule, both of which come up empty. Worth one
        # line so an operator can tell a degraded index from a code bug. Same
        # treatment as load_path_static's stop_times warning.
        logger.warning(
            "NJ Transit stop_times.txt has no usable rows; "
            "routes-per-station and the scheduled stop schedule will be empty"
        )
    logger.info(
        "Loaded NJ Transit static GTFS: %d stops, %d routes, %d trips, "
        "%d trips with stop_times, service through %s",
        len(data["stops"]),
        len(data["routes"]),
        len(data["trips"]),
        len(data["stop_times"]),
        latest_service_date(data["calendar_dates"]) or "(none)",
    )
    return data
