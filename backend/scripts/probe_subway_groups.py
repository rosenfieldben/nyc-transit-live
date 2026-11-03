#!/usr/bin/env python3
"""Step 0 of Amendment 1: which subway group feed carries which route id, measured.

WHY THIS EXISTS. Amendment 1 to the freshness contract (docs/design/freshness-contract.md,
A1.6 and its Q1) decided that a subway station's expected contributors come from a static
29-row route-to-group table beside SUBWAY_FEED_URLS, and that its rows are written from a
committed capture rather than from a document's grouping or a line's branding. Read from
the group KEYS, 7 of the live archive's 29 route ids name no group (6X, 7X, FS, FX, GS, H,
SI), and the only subway capture in the tree is 1-7+S's, which places GS and nothing else
of the seven. One fetch sees only what runs at that minute, so the measurement is many
samples over a full service day, the overnight included. This is the collector for those
samples and the reduction that folds them into the capture. It is a DATA PROBE in 6.0's
shape (the bus probe): it measures, it decides nothing, and it imports no decoder.

THE TWO MODES.

  sample (the default)  fetches each of the eight group feeds in SUBWAY_FEED_URLS once, in
                        that order, parses each as a GTFS-RT FeedMessage through
                        gtfs-realtime-bindings (the binding the app already depends on, and
                        nothing of the app's own decode: no stops, no static archive, no
                        placement), and appends one JSON line per feed to
                        <out-dir>/<sampled_at>.jsonl, the name being the sample's UTC time
                        in basic ISO 8601 (20261103T141007Z), which is also what the
                        workflow names the artifact by. It prints that path on stdout and
                        a one-line tally on stderr.
  --reduce DIR          folds every *.jsonl under DIR (recursively, since a download of
                        workflow artifacts puts each in its own directory) into the capture:
                        per route id, the groups that carried it with sample counts, first
                        and last seen and the UTC hours seen; per group, its route set; the
                        hour-of-day coverage and the weekday windows; the failed and the
                        stale rows; and the archive's route ids never seen in any live row,
                        each stated as unplaced. --routes names the archive to compare
                        against: the live gtfs_subway.zip as downloaded (whose calendar also
                        decides which weekdays ran weekday service), or its routes.txt alone.

WHAT A ROW IS. sampled_at, group, url, ok, error, status, bytes, header_timestamp,
header_age_s (the read instant minus the header timestamp: the feed's age at fetch, on
two machines' clocks, so a few seconds of it may be skew), fetched_at, entities (counts
by payload kind, plus `empty` for an entity that carries none, plus `total`), route_ids
(each route id seen in trip updates and in vehicle positions, with its entity count), and
no_route_id (entities whose trip names no route). Every row carries every key; a failed
row carries nulls where it measured nothing.

A FEED THAT FAILS IS A ROW, NEVER AN ABORT. A transport error, a non-200, an empty body,
bytes that do not parse and a message with no feed header are each recorded as a row with
ok false and the reason, and the other feeds are still fetched. The body rules are
feeds.shared.parse_feed's first three, restated here rather than imported (the probe uses
none of the app's decode): an empty body fails, a DecodeError fails, and a message with no
header, or a header in name only (an empty gtfs_realtime_version), fails. They are failures
and not quiet feeds because FeedMessage.ParseFromString(b"") succeeds with no entities, so
a 200 with nothing in it would otherwise read as a group carrying no routes at that hour,
which is exactly the wrong kind of evidence for a coverage table. A message with a real
header and zero entities is a quiet feed and is ok (parse_feed's rule 4). The sample exits
0 whenever its file is written, whatever failed: a failure is data, and the run's artifact
is how it is kept.

LIVE, STALE AND UNDATED, which the reduction decides and the row does not. An ok row is
LIVE when its header is dated and at most STALE_AFTER_S old at fetch, the repo's own
operator staleness limit for this content (cache.OPERATOR_STALE_AFTER_S, the contract
monitor's REALTIME_STALE_S; a test holds the three equal). An ok row that is older, or whose
header carries no timestamp, is listed as stale with its age and is not evidence: a frozen
feed showing the routes of hours ago says nothing about the hour it was fetched in, and
its silence about a route says nothing about that route's absence.

WHAT A COMPLETE SAMPLE IS. One whose every group in SUBWAY_FEED_URLS has a live row. The
coverage table counts complete samples only, because an hour is covered when every group
was seen live at it; route evidence counts every live row, because a route a group carried
is carried whether or not a sibling failed at the same minute.

A ROUTE IN TWO GROUPS IS SHOWN, NEVER RESOLVED. Placement lists a route under every group
any live row saw it in, and every route with more than one, in the archive or not, is
listed apart with its groups. The table Step 2 writes is the reader's decision; the
capture's job is to make it visible.

THE WEEKDAY WINDOWS. The collection is judged by coverage, not by duration (GitHub's
schedule drifts by hours: see the workflow), and the bar is every hour of the day sampled
across one weekday service day and its overnight. A window here is the 24 hours from
05:00 New York time on a weekday to 05:00 the next morning, so a weekday's daytime and the
night that follows it are one window rather than a night split across two. The 05:00 edge
is this script's convention, not an MTA service-day boundary; it decides only which
window an overnight sample is credited to. A Monday to Friday date is a weekday service day
when the archive's calendar runs a weekday service on it (a calendar.txt service running
Monday to Friday and no weekend day, not removed that date by calendar_dates.txt), so a
holiday on Sunday service (Thanksgiving, 2026-11-26) is listed but never counts as covered.
Given routes.txt alone there is no calendar, the capture says so, and every Monday to
Friday counts.

DETERMINISM. The reduction reads its inputs and nothing else (no wall clock), sorts every
list, and writes JSON with sorted keys, so the same samples give the same bytes however
they are laid out on disk. Two rows for the same group in the same sample must be
identical (a sample downloaded twice) or the reduction refuses the directory, and so it
does for a group fetched from more than one URL (samples from two sources).

THE NETWORK. Sample mode fetches the eight URLs in SUBWAY_FEED_URLS and nothing else, with
one GET each and no retry (a sample is a measurement, so a miss is recorded, not hidden),
following no redirect, as the app's own poller does not. Those URLs are redirectable by
SUBWAY_RT_BASE (the contract tier's seam), so sample mode refuses to run with any seam set,
through env_seams.assert_unset, exactly as the contract monitor does: a probe of the real
feeds must not quietly sample a simulator. Reduce mode makes no request at all.

NJ TRANSIT. The URL table lives in the feeds package, and importing it imports the whole
package, NJ Transit's modules included (njt_auth reads its mint allowance from the
environment at import, so a malformed NJT_DAILY_MINT_ALLOWANCE stops this script from
starting). Nothing here CALLS an NJ Transit path, and the workflow that runs it maps no
secret and declares no environment, so the credentials cannot exist there and nothing here
can spend an NJ Transit mint.

RUN, from backend/:
    python scripts/probe_subway_groups.py --out-dir SAMPLES
    python scripts/probe_subway_groups.py --reduce SAMPLES --routes gtfs_subway.zip \\
        --output tests/fixtures/<capture>.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import statistics
import sys
import time
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

# The same two-line preamble as the gen_*.py scripts and the contract monitor, so a run
# from scripts/ can import the app modules that hold the URLs and the seam guard.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import env_seams  # noqa: E402
from feeds.subway import SUBWAY_FEED_URLS  # noqa: E402

FORMAT = 1  # the reduced capture's shape; bump it when a key changes meaning
FETCH_TIMEOUT_S = 30.0  # the app's own httpx timeout for these feeds (pollers.py)
STALE_AFTER_S = 600.0  # cache.OPERATOR_STALE_AFTER_S (see LIVE, STALE AND UNDATED)
SAMPLE_SUFFIX = ".jsonl"
NEW_YORK = ZoneInfo("America/New_York")
WINDOW_START_HOUR = 5  # the weekday window's edge, New York time (see THE WEEKDAY WINDOWS)
_ISO_SECONDS = "%Y-%m-%dT%H:%M:%SZ"
_FILE_STAMP = "%Y%m%dT%H%M%SZ"
_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# Every payload a FeedEntity can carry in the bindings the app pins, read from the
# descriptor rather than listed, so a kind the MTA starts sending is counted, not missed.
ENTITY_KINDS = tuple(
    field.name
    for field in gtfs_realtime_pb2.FeedEntity.DESCRIPTOR.fields
    if field.message_type is not None
)


class Fetched(NamedTuple):
    status: int
    body: bytes


Fetch = Callable[[str], Fetched]
Clock = Callable[[], float]


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _iso_seconds(ts: float) -> str:
    return _utc(ts).strftime(_ISO_SECONDS)


def _iso_millis(ts: float) -> str:
    return _utc(ts).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso(stamp: str) -> datetime:
    return datetime.strptime(stamp, _ISO_SECONDS).replace(tzinfo=timezone.utc)


def sample_filename(sampled_at: float) -> str:
    """The sample's file name: its UTC time in basic ISO 8601, which an artifact name can
    carry (no colon), plus .jsonl."""
    return _utc(sampled_at).strftime(_FILE_STAMP) + SAMPLE_SUFFIX


# ---------------------------------------------------------------------------
# One feed
# ---------------------------------------------------------------------------


class FeedFailure(Exception):
    """A 200 body that is not a feed: empty, unparseable, or carrying no real header."""


def summarize_feed(raw: bytes) -> dict[str, Any]:
    """What one feed body carries: header timestamp, entities by kind, route ids by kind.

    Raises FeedFailure on an empty body, bytes that do not parse, or a message with no
    header or a header in name only (see A FEED THAT FAILS IS A ROW in the module
    docstring for why each is a failure rather than a quiet feed).
    """
    if not raw:
        raise FeedFailure("empty body")
    message = gtfs_realtime_pb2.FeedMessage()
    try:
        message.ParseFromString(raw)
    except DecodeError as exc:
        raise FeedFailure(f"DecodeError: {exc}") from exc
    # gtfs_realtime_version is required INSIDE the header, so an empty one is a header in
    # name only: parse_feed's rule 3, word for word in effect.
    if not message.HasField("header") or not message.header.gtfs_realtime_version:
        raise FeedFailure("no feed header")

    entities = dict.fromkeys(ENTITY_KINDS, 0)
    entities["empty"] = 0
    trip_update_routes: dict[str, int] = defaultdict(int)
    vehicle_routes: dict[str, int] = defaultdict(int)
    no_route_id = {"trip_update": 0, "vehicle": 0}
    for entity in message.entity:
        carried = [kind for kind in ENTITY_KINDS if entity.HasField(kind)]
        for kind in carried:
            entities[kind] += 1
        if not carried:
            entities["empty"] += 1
        if entity.HasField("trip_update"):
            route = entity.trip_update.trip.route_id
            if route:
                trip_update_routes[route] += 1
            else:
                no_route_id["trip_update"] += 1
        if entity.HasField("vehicle"):
            route = entity.vehicle.trip.route_id if entity.vehicle.HasField("trip") else ""
            if route:
                vehicle_routes[route] += 1
            else:
                no_route_id["vehicle"] += 1
    entities["total"] = len(message.entity)

    return {
        # 0 is the field's default: a header that does not date itself.
        "header_timestamp": message.header.timestamp or None,
        "entities": entities,
        "route_ids": {
            "trip_update": dict(sorted(trip_update_routes.items())),
            "vehicle": dict(sorted(vehicle_routes.items())),
        },
        "no_route_id": no_route_id,
    }


def _blank_row(sampled_at: str, group: str, url: str) -> dict[str, Any]:
    return {
        "sampled_at": sampled_at,
        "group": group,
        "url": url,
        "ok": False,
        "error": None,
        "status": None,
        "bytes": None,
        "fetched_at": None,
        "header_timestamp": None,
        "header_age_s": None,
        "entities": None,
        "route_ids": None,
        "no_route_id": None,
    }


def feed_row(sampled_at: str, group: str, url: str, fetch: Fetch, clock: Clock) -> dict[str, Any]:
    """One feed's row. Never raises for anything the feed or the network does."""
    row = _blank_row(sampled_at, group, url)
    try:
        fetched = fetch(url)
    except Exception as exc:  # noqa: BLE001 - any transport failure is a row, not an abort
        row["fetched_at"] = _iso_millis(clock())
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    read_at = clock()
    row["fetched_at"] = _iso_millis(read_at)
    row["status"] = fetched.status
    row["bytes"] = len(fetched.body)
    if fetched.status != 200:
        row["error"] = f"HTTP {fetched.status}"
        return row
    try:
        summary = summarize_feed(fetched.body)
    except FeedFailure as exc:
        row["error"] = str(exc)
        return row
    except Exception as exc:  # noqa: BLE001 - a parse surprise is a row, not an abort
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    row.update(summary)
    header = summary["header_timestamp"]
    row["header_age_s"] = None if header is None else round(read_at - header, 3)
    row["ok"] = True
    return row


# ---------------------------------------------------------------------------
# One sample
# ---------------------------------------------------------------------------


def take_sample(
    fetch: Fetch, clock: Clock, urls: Mapping[str, str] = SUBWAY_FEED_URLS
) -> list[dict[str, Any]]:
    """One row per group, in `urls` order, every row stamped with the sample's start."""
    sampled_at = _iso_seconds(clock())
    return [feed_row(sampled_at, group, url, fetch, clock) for group, url in urls.items()]


def write_sample(rows: Sequence[dict[str, Any]], out_dir: Path) -> Path:
    """Append the rows, one JSON line each, to the file named for the sample's UTC time."""
    if not rows:
        raise ValueError("a sample has one row per group; this one has none")
    sampled_at = _parse_iso(rows[0]["sampled_at"]).timestamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / sample_filename(sampled_at)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def run_sample(
    out_dir: Path, fetch: Fetch, clock: Clock, urls: Mapping[str, str] = SUBWAY_FEED_URLS
) -> tuple[Path, list[dict[str, Any]]]:
    rows = take_sample(fetch, clock, urls)
    return write_sample(rows, out_dir), rows


def httpx_fetch(client: httpx.Client) -> Fetch:
    """The real fetch: one GET, no redirect followed (the app's poller follows none)."""

    def fetch(url: str) -> Fetched:
        response = client.get(url, follow_redirects=False)
        return Fetched(response.status_code, response.content)

    return fetch


# ---------------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------------


class ServiceCalendar(NamedTuple):
    """calendar.txt and calendar_dates.txt, as much of them as the weekday rule reads."""

    weekly: Mapping[str, tuple[tuple[bool, ...], str, str]]  # id -> (days, start, end)
    exceptions: Mapping[str, Mapping[str, str]]  # YYYYMMDD -> id -> exception_type

    def weekday_services(self, day: date) -> list[str]:
        """The weekday services running on `day`: a service whose week is Monday to Friday
        and no weekend day, in range and in its week that day and not removed that day,
        or added that day by an exception."""
        stamp = day.strftime("%Y%m%d")
        exceptions = self.exceptions.get(stamp, {})
        running = []
        for service, (days, start, end) in self.weekly.items():
            if days != (True,) * 5 + (False,) * 2:
                continue
            scheduled = start <= stamp <= end and days[day.weekday()]
            kind = exceptions.get(service)
            if (scheduled and kind != "2") or kind == "1":
                running.append(service)
        return sorted(running)


class ArchiveRoutes(NamedTuple):
    route_ids: tuple[str, ...]
    routes_txt_sha256: str
    feed_version: str | None
    calendar: ServiceCalendar | None = None


def _csv_rows(raw: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def _read_calendar(calendar_txt: bytes, calendar_dates_txt: bytes | None) -> ServiceCalendar:
    weekly = {
        row["service_id"].strip(): (
            tuple((row.get(day) or "").strip() == "1" for day in _DAYS),
            (row.get("start_date") or "").strip(),
            (row.get("end_date") or "").strip(),
        )
        for row in _csv_rows(calendar_txt)
    }
    exceptions: dict[str, dict[str, str]] = defaultdict(dict)
    for row in _csv_rows(calendar_dates_txt or b""):
        exceptions[row["date"].strip()][row["service_id"].strip()] = row["exception_type"].strip()
    return ServiceCalendar(weekly, dict(exceptions))


def read_archive_routes(path: Path) -> ArchiveRoutes:
    """The route ids of a GTFS archive (zip, with its calendar) or of its routes.txt alone."""
    feed_version = None
    calendar = None
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            routes_txt = archive.read("routes.txt")
            if "feed_info.txt" in names:
                rows = _csv_rows(archive.read("feed_info.txt"))
                feed_version = (rows[0].get("feed_version") or None) if rows else None
            if "calendar.txt" in names:
                dates = (
                    archive.read("calendar_dates.txt") if "calendar_dates.txt" in names else None
                )
                calendar = _read_calendar(archive.read("calendar.txt"), dates)
    else:
        routes_txt = path.read_bytes()
    ids = [(row.get("route_id") or "").strip() for row in _csv_rows(routes_txt)]
    if not ids or "" in ids:
        raise ValueError(f"{path}: routes.txt has no rows, or a row with no route_id")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: routes.txt names a route_id twice")
    return ArchiveRoutes(
        route_ids=tuple(sorted(ids)),
        routes_txt_sha256=hashlib.sha256(routes_txt).hexdigest(),
        feed_version=feed_version,
        calendar=calendar,
    )


# ---------------------------------------------------------------------------
# The reduction
# ---------------------------------------------------------------------------


def load_rows(directory: Path) -> list[dict[str, Any]]:
    """Every row of every *.jsonl under `directory`. A line that does not parse refuses
    the directory, with its file and line, rather than being skipped."""
    rows = []
    for path in sorted(directory.rglob("*" + SAMPLE_SUFFIX)):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: not a JSON line ({exc})") from exc
    return rows


def _samples(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """sampled_at -> group -> row, with an exact duplicate folded and a conflict refused."""
    samples: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        by_group = samples[row["sampled_at"]]
        seen = by_group.get(row["group"])
        if seen is not None and seen != row:
            raise ValueError(
                f"two different rows for {row['group']} in the sample at {row['sampled_at']}"
            )
        by_group[row["group"]] = row
    return dict(samples)


def row_state(row: Mapping[str, Any]) -> str:
    """failed, undated, stale or live (see LIVE, STALE AND UNDATED)."""
    if not row["ok"]:
        return "failed"
    if row["header_age_s"] is None:
        return "undated"
    return "live" if row["header_age_s"] <= STALE_AFTER_S else "stale"


def _window_date(when: datetime) -> datetime:
    """New York wall time shifted back to the window edge: its date is the date of the
    weekday window that holds `when` (see THE WEEKDAY WINDOWS)."""
    local = when.astimezone(NEW_YORK)
    return local - timedelta(hours=WINDOW_START_HOUR)


def _routes_of(row: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """route id -> (trip update entities, vehicle entities) in one live row."""
    trip_updates = row["route_ids"]["trip_update"]
    vehicles = row["route_ids"]["vehicle"]
    return {
        route: (trip_updates.get(route, 0), vehicles.get(route, 0))
        for route in set(trip_updates) | set(vehicles)
    }


def reduce_samples(
    rows: Iterable[dict[str, Any]],
    archive: ArchiveRoutes,
    expected_groups: Sequence[str] = tuple(SUBWAY_FEED_URLS),
) -> dict[str, Any]:
    """Fold sample rows into the capture. Pure: the same rows give the same dict."""
    samples = _samples(rows)
    if not samples:
        raise ValueError("no sample rows to reduce")
    order = sorted(samples)
    groups_seen = sorted(set(expected_groups) | {g for s in samples.values() for g in s})

    complete_by_hour = dict.fromkeys(range(24), 0)
    incomplete_by_hour = dict.fromkeys(range(24), 0)
    windows: dict[str, dict[str, Any]] = {}
    failed: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    states: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    group_urls: dict[str, set[str]] = defaultdict(set)
    group_ages: dict[str, list[float]] = defaultdict(list)
    no_route_id: dict[str, dict[str, int]] = defaultdict(lambda: {"trip_update": 0, "vehicle": 0})
    empty_live: dict[str, int] = defaultdict(int)
    # route -> group -> running evidence
    evidence: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for stamp in order:
        sample = samples[stamp]
        when = _parse_iso(stamp)
        complete = all(g in sample and row_state(sample[g]) == "live" for g in expected_groups)
        (complete_by_hour if complete else incomplete_by_hour)[when.hour] += 1

        shifted = _window_date(when)
        if shifted.weekday() < 5:
            key = shifted.date().isoformat()
            if key not in windows:
                services = (
                    None
                    if archive.calendar is None
                    else archive.calendar.weekday_services(shifted.date())
                )
                windows[key] = {
                    "weekday": shifted.strftime("%A"),
                    "services": services,
                    "complete": 0,
                    "incomplete": 0,
                    "hours": set(),
                }
            window = windows[key]
            if complete:
                window["complete"] += 1
                window["hours"].add(when.astimezone(NEW_YORK).hour)
            else:
                window["incomplete"] += 1

        for group in expected_groups:
            if group not in sample:
                failed.append(
                    {"sampled_at": stamp, "group": group, "status": None, "error": "no row"}
                )
                states[group]["failed"] += 1
        for group, row in sample.items():
            group_urls[group].add(row["url"])
            state = row_state(row)
            states[group][state] += 1
            if state == "failed":
                failed.append(
                    {
                        "sampled_at": stamp,
                        "group": group,
                        "status": row["status"],
                        "error": row["error"],
                    }
                )
                continue
            if row["header_age_s"] is not None:
                group_ages[group].append(row["header_age_s"])
            if state != "live":
                stale.append(
                    {
                        "sampled_at": stamp,
                        "group": group,
                        "state": state,
                        "header_age_s": row["header_age_s"],
                    }
                )
                continue
            for kind in ("trip_update", "vehicle"):
                no_route_id[group][kind] += row["no_route_id"][kind]
            if row["entities"]["total"] == 0:
                empty_live[group] += 1
            for route, (trip_updates, vehicles) in _routes_of(row).items():
                seen = evidence[route].setdefault(
                    group,
                    {
                        "samples": 0,
                        "first_seen": stamp,
                        "last_seen": stamp,
                        "trip_updates": 0,
                        "vehicles": 0,
                        "utc_hours_seen": set(),
                    },
                )
                seen["samples"] += 1
                seen["last_seen"] = stamp
                seen["trip_updates"] += trip_updates
                seen["vehicles"] += vehicles
                seen["utc_hours_seen"].add(when.hour)

    two_urls = {g: sorted(urls) for g, urls in group_urls.items() if len(urls) > 1}
    if two_urls:
        raise ValueError(f"a group was sampled from more than one URL: {two_urls}")

    archive_ids = set(archive.route_ids)
    routes = {
        route: {
            "in_archive": route in archive_ids,
            "groups": {
                group: {**seen, "utc_hours_seen": sorted(seen["utc_hours_seen"])}
                for group, seen in sorted(by_group.items())
            },
        }
        for route, by_group in sorted(evidence.items())
    }

    groups = {}
    for group in groups_seen:
        ages = group_ages.get(group, [])
        counts = states.get(group, {})
        groups[group] = {
            "expected": group in expected_groups,
            "live_samples": counts.get("live", 0),
            "stale_samples": counts.get("stale", 0) + counts.get("undated", 0),
            "failed_samples": counts.get("failed", 0),
            "empty_live_samples": empty_live.get(group, 0),
            "no_route_id": dict(no_route_id.get(group, {"trip_update": 0, "vehicle": 0})),
            "routes": sorted(r for r, by_group in evidence.items() if group in by_group),
            "urls": sorted(group_urls.get(group, set())),
            "header_age_s": (
                {"median": round(statistics.median(ages), 3), "max": round(max(ages), 3)}
                if ages
                else None
            ),
        }

    placed = {}
    for route in archive.route_ids:
        carriers = sorted(evidence.get(route, {}))
        if len(carriers) == 1:
            placed[route] = carriers[0]
    in_more_than_one = {
        route: sorted(by_group) for route, by_group in sorted(evidence.items()) if len(by_group) > 1
    }
    unplaced = sorted(archive_ids - set(evidence))

    def window_counts(window: dict[str, Any]) -> bool:
        return len(window["hours"]) == 24 and window["services"] != []

    complete_total = sum(complete_by_hour.values())
    return {
        "format": FORMAT,
        "probe": "backend/scripts/probe_subway_groups.py",
        "stale_after_s": STALE_AFTER_S,
        "archive": {
            "feed_version": archive.feed_version,
            "routes_txt_sha256": archive.routes_txt_sha256,
            "route_ids": list(archive.route_ids),
            "calendar": archive.calendar is not None,
        },
        "expected_groups": list(expected_groups),
        "window": {
            "first_sample": order[0],
            "last_sample": order[-1],
            "samples": len(order),
            "complete_samples": complete_total,
            "incomplete_samples": len(order) - complete_total,
            "failed_rows": len(failed),
            "stale_rows": len(stale),
        },
        "coverage": {
            "by_utc_hour": {
                f"{hour:02d}": {
                    "complete": complete_by_hour[hour],
                    "incomplete": incomplete_by_hour[hour],
                }
                for hour in range(24)
            },
            "utc_hours_without_complete_sample": [
                f"{hour:02d}" for hour in range(24) if not complete_by_hour[hour]
            ],
            "weekday_windows": {
                key: {
                    "weekday": window["weekday"],
                    "weekday_services": window["services"],
                    "complete_samples": window["complete"],
                    "incomplete_samples": window["incomplete"],
                    "new_york_hours_without_complete_sample": [
                        f"{hour:02d}" for hour in range(24) if hour not in window["hours"]
                    ],
                }
                for key, window in sorted(windows.items())
            },
            "weekday_windows_fully_covered": sorted(
                key for key, window in windows.items() if window_counts(window)
            ),
        },
        "groups": groups,
        "routes": routes,
        "placement": {
            "placed": placed,
            "in_more_than_one_group": in_more_than_one,
            "unplaced": unplaced,
            "seen_not_in_archive": sorted(set(evidence) - archive_ids),
            "counts": {
                "archive": len(archive.route_ids),
                "placed": len(placed),
                "in_more_than_one_group": len(in_more_than_one),
                "unplaced": len(unplaced),
            },
        },
        "failed": sorted(failed, key=lambda f: (f["sampled_at"], f["group"])),
        "stale": sorted(stale, key=lambda f: (f["sampled_at"], f["group"])),
    }


def render_capture(capture: Mapping[str, Any]) -> str:
    """The capture as committed: sorted keys, two-space indent, one trailing newline."""
    return json.dumps(capture, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    clock: Clock = time.time,
) -> int:
    """The command line. `client_factory` and `clock` are the seams a test drives the
    sample path through (an httpx client over a mock transport, a fixed clock)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out-dir", type=Path, default=Path("."), help="sample mode: where")
    parser.add_argument("--reduce", type=Path, metavar="DIR", help="fold DIR's samples")
    parser.add_argument("--routes", type=Path, help="reduce: gtfs_subway.zip or routes.txt")
    parser.add_argument("--output", type=Path, help="reduce: write here, not to stdout")
    args = parser.parse_args(argv)

    if args.reduce is not None:
        if args.routes is None:
            parser.error("--reduce needs --routes (the archive the placement is checked against)")
        capture = reduce_samples(load_rows(args.reduce), read_archive_routes(args.routes))
        text = render_capture(capture)
        if args.output is None:
            sys.stdout.write(text)
        else:
            args.output.write_text(text, encoding="utf-8")
        counts = capture["placement"]["counts"]
        print(
            f"{capture['window']['samples']} samples, {counts['placed']} of "
            f"{counts['archive']} placed, {counts['in_more_than_one_group']} in more than "
            f"one group, {counts['unplaced']} unplaced",
            file=sys.stderr,
        )
        return 0

    env_seams.assert_unset("the subway group probe")
    with client_factory(timeout=FETCH_TIMEOUT_S) as client:
        path, rows = run_sample(args.out_dir, httpx_fetch(client), clock)
    print(path)
    failed = [f"{row['group']} ({row['error']})" for row in rows if not row["ok"]]
    print(
        f"{len(rows) - len(failed)} of {len(rows)} feeds ok"
        + (f"; failed: {', '.join(failed)}" if failed else ""),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
