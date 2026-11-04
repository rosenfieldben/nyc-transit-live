"""backend/scripts/probe_subway_groups.py, Step 0 of Amendment 1's collector and reduction.

Everything here is hermetic: the fetch and the clock are injected, the feeds are the
committed 1-7+S capture or messages built in the test, and the one import that could open
a socket is watched. What is pinned:

  * the parse, on the committed 1-7+S capture: GS appears under 1-7+S BY MEASUREMENT (14
    trip updates and 1 vehicle), where the key's first character would say G;
  * a sample fetches each of the eight SUBWAY_FEED_URLS once, in order, and nothing else,
    and refuses to run at all with a contract-tier seam set;
  * the loop's clock and stop rule: samples on the grid start + k * ten minutes, only at
    slots strictly before the budget, an overrun skipping the slots it missed rather than
    bursting, every sample in one file named for the loop's start and written as it
    completes, so a loop killed partway keeps what it finished; and every row's
    provenance, counted by the reduction;
  * a feed that fails is a row, for every way a feed can fail (parse_feed's body rules
    included, a header in name only among them), and never aborts the sample;
  * the reduction: placement from every live row (stale and undated rows listed, not
    counted), a route in two groups shown and not resolved, an archive route never seen
    stated unplaced, hour coverage from complete samples only, the weekday windows at their
    edges and against the archive's calendar, determinism over the layout on disk, sorted
    keys, and a refusal for a conflicting row, an unreadable line, or a group fetched from
    two URLs;
  * the temporary workflow: its schedule, its dispatched loop, a queue per trigger, the
    timeouts nesting budget inside step inside job, its read-only permissions, that no
    expression in it reads a secret (which is what makes "nothing here can spend an NJ
    Transit mint" structural), and the shell of both its run steps, run for real. That
    section goes with the workflow, in the commit that deletes it.

THE CAPTURE IS SERVED RE-DATED TO THE SAMPLE'S MINUTE in the reduction tests. It was taken
on 2026-06-13, so in a November sample its own header would make it stale and, correctly,
not evidence. _capture_at changes the header timestamp and nothing else; the parse tests
read the bytes as committed.

The script lives under scripts/ (not an importable package), so it is loaded from its file
path, the way test_measure_subway_validation loads its script.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from google.transit import gtfs_realtime_pb2 as pb

from feeds.subway import SUBWAY_FEED_URLS

_BACKEND = Path(__file__).resolve().parent.parent
_SCRIPT = _BACKEND / "scripts" / "probe_subway_groups.py"
_WORKFLOW = _BACKEND.parent / ".github" / "workflows" / "probe-subway-groups.yml"
_CAPTURE = _BACKEND / "tests" / "fixtures" / "subway_1_7_s.pb"
_CAPTURE_HEADER = 1781380197  # 2026-06-13T19:49:57Z, the capture's own header

# Socket events seen while _WATCHING is set. An audit hook cannot be removed, so it is
# installed once and gated, and it only records: the assertion is the test's.
_WATCHING = False
_SOCKET_EVENTS: list[str] = []


def _record_sockets(event: str, args: tuple) -> None:
    if _WATCHING and event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
        _SOCKET_EVENTS.append(event)


sys.addaudithook(_record_sockets)


def _load():
    spec = importlib.util.spec_from_file_location("probe_subway_groups", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    return _load()


@pytest.fixture
def watched():
    global _WATCHING
    _SOCKET_EVENTS.clear()
    _WATCHING = True
    try:
        yield _SOCKET_EVENTS
    finally:
        _WATCHING = False


def _ts(stamp: str) -> float:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def _stamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _feed(header: int | None, routes: dict[str, tuple[int, int]]) -> bytes:
    """A FeedMessage carrying, per route, that many trip updates and vehicle positions."""
    message = pb.FeedMessage()
    message.header.gtfs_realtime_version = "1.0"
    if header is not None:
        message.header.timestamp = header
    n = 0
    for route, (trip_updates, vehicles) in routes.items():
        for _ in range(trip_updates):
            entity = message.entity.add()
            entity.id = f"tu{n}"
            entity.trip_update.trip.route_id = route
            n += 1
        for _ in range(vehicles):
            entity = message.entity.add()
            entity.id = f"vp{n}"
            entity.vehicle.trip.route_id = route
            n += 1
    return message.SerializeToString()


def _capture_at(header: int) -> bytes:
    """The committed 1-7+S capture with its header timestamp moved, and nothing else."""
    message = pb.FeedMessage()
    message.ParseFromString(_CAPTURE.read_bytes())
    message.header.timestamp = header
    return message.SerializeToString()


# One plausible routing of the other seven groups, so a sample is complete. The test that
# needs a group to carry something else says so.
_OTHERS = {
    "ACE": {"A": (2, 2), "C": (1, 1), "E": (1, 1), "H": (1, 1)},
    "BDFM": {"B": (1, 1), "D": (1, 1), "F": (2, 2), "FS": (1, 1), "M": (1, 1)},
    "G": {"G": (1, 1)},
    "JZ": {"J": (1, 1), "Z": (1, 0)},
    "NQRW": {"N": (1, 1), "Q": (1, 1), "R": (1, 1), "W": (1, 1)},
    "L": {"L": (2, 1)},
    "SIR": {"SI": (1, 1)},
}


class _Clock:
    """A clock that starts at `start` and advances `step` seconds per read."""

    def __init__(self, start: float, step: float = 0.25):
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


def _fetch_for(probe, bodies: dict[str, object]):
    """A fetch serving each group's URL from `bodies`: bytes is a 200, a (status, bytes)
    pair is that response, an exception instance is raised. It records every URL asked."""
    by_url = {SUBWAY_FEED_URLS[group]: body for group, body in bodies.items()}
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        body = by_url[url]
        if isinstance(body, BaseException):
            raise body
        if isinstance(body, tuple):
            return probe.Fetched(*body)
        return probe.Fetched(200, body)

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _complete_bodies(at: float, **overrides: object) -> dict[str, object]:
    """Every group live at `at`: each header 5 s before the sample's start."""
    header = int(at) - 5
    bodies: dict[str, object] = {"1-7+S": _capture_at(header)}
    bodies.update({group: _feed(header, routes) for group, routes in _OTHERS.items()})
    bodies.update(overrides)
    return bodies


def _sample(probe, stamp: str, **overrides: object) -> list[dict]:
    at = _ts(stamp)
    return probe.take_sample(_fetch_for(probe, _complete_bodies(at, **overrides)), _Clock(at))


# The live archive's 29 (Last-Modified 2026-10-28), so the counts below read as the
# acceptance's own.
_LIVE_29 = "1 2 3 4 5 6 6X 7 7X A B C D E F FS FX G GS H J L M N Q R SI W Z".split()

# The live archive's calendar, as published (calendar.txt and calendar_dates.txt).
_CALENDAR_TXT = (
    b"service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
    b"Sunday,0,0,0,0,0,0,1,20261101,20270515\n"
    b"Saturday,0,0,0,0,0,1,0,20261101,20270515\n"
    b"Weekday,1,1,1,1,1,0,0,20261101,20270515\n"
)
_CALENDAR_DATES_TXT = (
    b"service_id,date,exception_type\n"
    b"Sunday,20261126,1\nSunday,20261225,1\nSunday,20270101,1\n"
    b"Saturday,20270118,1\nSaturday,20270215,1\n"
    b"Weekday,20261126,2\nWeekday,20261225,2\nWeekday,20270101,2\n"
    b"Weekday,20270118,2\nWeekday,20270215,2\n"
)


def _archive(probe, ids=None, calendar=None):
    return probe.ArchiveRoutes(tuple(sorted(ids or _LIVE_29)), "0" * 64, "test-archive", calendar)


def _hourly(start: str, hours: int = 24, offset_s: int = 120) -> list[str]:
    return [_stamp(_ts(start) + 3600 * h + offset_s) for h in range(hours)]


# ---------------------------------------------------------------------------
# Import and the network
# ---------------------------------------------------------------------------


def test_importing_the_script_opens_no_socket(watched):
    module = _load()
    assert watched == []
    assert callable(module.main)


def test_a_sample_fetches_each_group_url_once_in_order_and_nothing_else(probe, watched):
    at = _ts("2026-11-04T14:00:00Z")
    fetch = _fetch_for(probe, _complete_bodies(at))
    rows = probe.take_sample(fetch, _Clock(at))
    assert fetch.calls == list(SUBWAY_FEED_URLS.values())
    assert [row["group"] for row in rows] == list(SUBWAY_FEED_URLS)
    assert [row["url"] for row in rows] == list(SUBWAY_FEED_URLS.values())
    assert all(row["ok"] for row in rows)
    assert watched == []


def test_the_real_fetch_is_one_get_that_follows_no_redirect(probe):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.example/feed"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetched = probe.httpx_fetch(client)(SUBWAY_FEED_URLS["G"])
    assert fetched.status == 302
    assert [(r.method, str(r.url)) for r in seen] == [("GET", SUBWAY_FEED_URLS["G"])]


def test_sample_mode_refuses_to_run_with_a_seam_set(probe, tmp_path, monkeypatch):
    """SUBWAY_RT_BASE moves every URL in SUBWAY_FEED_URLS (the contract tier's simulator).
    A probe of the real feeds must refuse it rather than sample the simulator, before any
    client is built."""
    monkeypatch.setenv("SUBWAY_RT_BASE", "http://127.0.0.1:9/nyct%2Fgtfs")

    def no_client(**kwargs):
        raise AssertionError("a client was built with a seam set")

    with pytest.raises(RuntimeError, match="SUBWAY_RT_BASE"):
        probe.main(["--out-dir", str(tmp_path)], client_factory=no_client)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# The parse, on the committed capture
# ---------------------------------------------------------------------------


def test_the_committed_1_7_s_capture_carries_gs_by_measurement(probe):
    summary = probe.summarize_feed(_CAPTURE.read_bytes())
    trip_updates = summary["route_ids"]["trip_update"]
    vehicles = summary["route_ids"]["vehicle"]
    assert trip_updates["GS"] == 14
    assert vehicles["GS"] == 1
    assert set(trip_updates) == {"1", "2", "3", "4", "5", "6", "7", "GS"}
    assert set(vehicles) == set(trip_updates)
    assert sum(trip_updates.values()) == 160
    assert sum(vehicles.values()) == 98
    assert summary["no_route_id"] == {"trip_update": 0, "vehicle": 0}
    assert summary["header_timestamp"] == _CAPTURE_HEADER
    entities = summary["entities"]
    assert (entities["total"], entities["trip_update"], entities["vehicle"]) == (259, 160, 98)
    assert entities["alert"] == 1
    assert entities["empty"] == 0
    assert set(entities) == set(probe.ENTITY_KINDS) | {"empty", "total"}


def test_every_payload_kind_the_pinned_bindings_define_is_counted_by_its_name(probe):
    """gtfs-realtime-bindings 2.1.0 defines six payloads. A list typed by hand would miss
    the three newest and count them as empty; the descriptor-derived one does not."""
    assert set(probe.ENTITY_KINDS) == {
        "trip_update",
        "vehicle",
        "alert",
        "shape",
        "stop",
        "trip_modifications",
    }
    message = pb.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.entity.add(id="s").shape.shape_id = "shape-1"
    message.entity.add(id="p").stop.stop_id = "stop-1"
    message.entity.add(id="m").trip_modifications.selected_trips.add().trip_ids.append("t")
    entities = probe.summarize_feed(message.SerializeToString())["entities"]
    assert (entities["shape"], entities["stop"], entities["trip_modifications"]) == (1, 1, 1)
    assert (entities["empty"], entities["total"]) == (0, 3)


def test_an_entity_with_no_route_id_is_counted_apart_not_dropped(probe):
    message = pb.FeedMessage()
    message.header.gtfs_realtime_version = "1.0"
    message.header.timestamp = 100
    message.entity.add(id="a").trip_update.trip.trip_id = "no-route"
    message.entity.add(id="b").vehicle.vehicle.id = "no-trip"
    message.entity.add(id="c")
    summary = probe.summarize_feed(message.SerializeToString())
    assert summary["route_ids"] == {"trip_update": {}, "vehicle": {}}
    assert summary["no_route_id"] == {"trip_update": 1, "vehicle": 1}
    assert summary["entities"]["empty"] == 1
    assert summary["entities"]["total"] == 3


def test_header_age_is_the_read_instant_minus_the_header(probe):
    at = _ts("2026-06-13T19:50:00Z")
    clock = _Clock(at, step=0.5)
    bodies = _complete_bodies(at, **{"1-7+S": _CAPTURE.read_bytes()})
    rows = probe.take_sample(_fetch_for(probe, bodies), clock)
    first = rows[0]
    assert first["sampled_at"] == "2026-06-13T19:50:00Z"
    # The sample's start is the first read, the 1-7+S response the second.
    assert first["fetched_at"] == "2026-06-13T19:50:00.500Z"
    assert first["header_age_s"] == round(at + 0.5 - _CAPTURE_HEADER, 3)
    assert first["header_timestamp"] == _CAPTURE_HEADER


def test_an_undated_header_records_no_timestamp_and_no_age(probe):
    rows = _sample(probe, "2026-11-04T14:00:00Z", G=_feed(None, {"G": (1, 1)}))
    g = rows[list(SUBWAY_FEED_URLS).index("G")]
    assert g["ok"] is True
    assert (g["header_timestamp"], g["header_age_s"]) == (None, None)


# ---------------------------------------------------------------------------
# A feed that fails is a row
# ---------------------------------------------------------------------------


def test_every_kind_of_failure_is_a_row_and_the_sample_goes_on(probe):
    at = _ts("2026-11-04T03:00:05Z")
    headerless = pb.FeedMessage()
    headerless.entity.add(id="x").trip_update.trip.route_id = "J"
    quiet = pb.FeedMessage()
    quiet.header.gtfs_realtime_version = "1.0"
    quiet.header.timestamp = int(at) - 5
    rows = _sample(
        probe,
        "2026-11-04T03:00:05Z",
        ACE=httpx.ConnectError("refused"),
        BDFM=(503, b"busy"),
        G=b"",
        JZ=headerless.SerializePartialToString(),
        NQRW=b"\xff\xff\xff\xff",
        L=quiet.SerializeToString(),
        SIR=(302, b""),
    )
    by_group = {row["group"]: row for row in rows}
    assert list(by_group) == list(SUBWAY_FEED_URLS)
    assert len({frozenset(row) for row in rows}) == 1, "every row carries every key"

    def outcome(group):
        row = by_group[group]
        return row["ok"], row["status"], row["bytes"], row["error"]

    assert outcome("1-7+S") == (True, 200, len(_capture_at(int(at) - 5)), None)
    assert outcome("ACE") == (False, None, None, "ConnectError: refused")
    assert outcome("BDFM") == (False, 503, 4, "HTTP 503")
    assert outcome("G") == (False, 200, 0, "empty body")
    assert outcome("JZ")[:2] == (False, 200)
    assert outcome("JZ")[3] == "no feed header"
    assert outcome("NQRW")[:2] == (False, 200)
    assert outcome("NQRW")[3].startswith("DecodeError: ")
    assert outcome("SIR") == (False, 302, 0, "HTTP 302")
    # A real header with nothing under it is a quiet feed, not a failure.
    assert outcome("L")[:2] == (True, 200)
    assert by_group["L"]["entities"]["total"] == 0
    assert by_group["L"]["route_ids"] == {"trip_update": {}, "vehicle": {}}
    for group in ("ACE", "BDFM", "G", "JZ", "NQRW", "SIR"):
        row = by_group[group]
        assert row["entities"] is None and row["route_ids"] is None
        assert row["fetched_at"] is not None


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"\x0a\x00", id="an empty header"),
        pytest.param(b"\x0a\x02\x10\x01", id="a header with only a timestamp"),
    ],
)
def test_a_header_in_name_only_is_no_header(probe, body):
    """parse_feed's rule 3 in full: the header must carry its required version. Both
    bodies parse, and parse_feed refuses both, so the probe must too."""
    from feeds.shared import FeedDecodeError, parse_feed

    with pytest.raises(FeedDecodeError):
        parse_feed(body)
    with pytest.raises(probe.FeedFailure, match="no feed header"):
        probe.summarize_feed(body)
    rows = _sample(probe, "2026-11-04T14:00:00Z", L=body)
    assert rows[list(SUBWAY_FEED_URLS).index("L")]["error"] == "no feed header"


def test_an_unexpected_parse_error_is_a_row_too(probe, monkeypatch):
    def explode(raw):
        raise RuntimeError("binding surprise")

    monkeypatch.setattr(probe, "summarize_feed", explode)
    rows = _sample(probe, "2026-11-04T14:00:00Z")
    assert [row["error"] for row in rows] == ["RuntimeError: binding surprise"] * 8
    assert not any(row["ok"] for row in rows)


# ---------------------------------------------------------------------------
# The sample file
# ---------------------------------------------------------------------------


def test_the_sample_file_is_named_for_its_utc_time_one_line_per_feed(probe, tmp_path):
    at = _ts("2026-11-04T14:07:33Z")
    fetch = _fetch_for(probe, _complete_bodies(at))
    path, rows = probe.run_sample(tmp_path / "out", fetch, _Clock(at))
    assert path == tmp_path / "out" / "20261104T140733Z.jsonl"
    lines = path.read_text().splitlines()
    assert [json.loads(line) for line in lines] == rows
    assert all(line == json.dumps(json.loads(line), sort_keys=True) for line in lines)
    # Appending, as the brief says: a second write of the same sample adds its lines.
    probe.write_sample(rows, tmp_path / "out")
    assert len(path.read_text().splitlines()) == 16


def test_the_cli_sample_prints_the_path_and_exits_zero_with_failures(probe, tmp_path, capsys):
    """main end to end over the real fetch and a real httpx client on a mock transport:
    no socket, and a failed feed still exits 0 because the failure is in the file."""
    at = _ts("2026-11-04T14:00:00Z")
    bodies = {SUBWAY_FEED_URLS[g]: b for g, b in _complete_bodies(at).items()}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if str(request.url) == SUBWAY_FEED_URLS["G"]:
            return httpx.Response(500)
        return httpx.Response(200, content=bodies[str(request.url)])

    def client_factory(**kwargs):
        assert kwargs == {"timeout": probe.FETCH_TIMEOUT_S}
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    code = probe.main(["--out-dir", str(tmp_path)], client_factory=client_factory, clock=_Clock(at))
    assert code == 0
    assert asked == list(SUBWAY_FEED_URLS.values())
    out, err = capsys.readouterr()
    assert out.strip() == str(tmp_path / "20261104T140000Z.jsonl")
    assert err.strip() == "7 of 8 feeds ok; failed: G (HTTP 500)"
    rows = [json.loads(line) for line in Path(out.strip()).read_text().splitlines()]
    assert [(row["group"], row["ok"]) for row in rows] == [
        (group, group != "G") for group in SUBWAY_FEED_URLS
    ]


# ---------------------------------------------------------------------------
# The reduction
# ---------------------------------------------------------------------------


def test_the_reduction_places_gs_under_1_7_s_from_the_committed_capture(probe):
    capture = probe.reduce_samples(_sample(probe, "2026-11-04T14:00:00Z"), _archive(probe))
    placement = capture["placement"]
    assert placement["placed"]["GS"] == "1-7+S"
    assert capture["routes"]["GS"]["groups"]["1-7+S"]["trip_updates"] == 14
    assert capture["routes"]["GS"]["groups"]["1-7+S"]["vehicles"] == 1
    assert capture["groups"]["1-7+S"]["routes"] == ["1", "2", "3", "4", "5", "6", "7", "GS"]
    # The capture is a Saturday's: no rush-only 6X or 7X in it, and nothing here has FX.
    assert placement["unplaced"] == ["6X", "7X", "FX"]
    assert placement["counts"] == {
        "archive": 29,
        "placed": 26,
        "in_more_than_one_group": 0,
        "unplaced": 3,
    }


def test_the_stale_threshold_is_the_repos_operator_staleness_limit(probe):
    import cache

    assert probe.STALE_AFTER_S == cache.OPERATOR_STALE_AFTER_S == 600.0


def test_a_stale_or_undated_row_is_listed_not_evidence_and_leaves_its_sample_incomplete(probe):
    """A frozen feed's routes are hours old and its silence proves nothing, so a row past
    STALE_AFTER_S, or with no header timestamp, counts for neither coverage nor placement.
    At most 600 s old is live (the clock reads BDFM at the sample's start plus 0.75 s)."""
    first, second = "2026-11-04T14:00:00Z", "2026-11-04T14:10:00Z"
    stale_bdfm = _feed(int(_ts(first)) - 600, {**_OTHERS["BDFM"], "FX": (1, 1)})
    edge_bdfm = _feed(int(_ts(second)) - 599, _OTHERS["BDFM"])
    rows = _sample(probe, first, BDFM=stale_bdfm, G=_feed(None, {"G": (1, 1)})) + _sample(
        probe, second, BDFM=edge_bdfm
    )
    capture = probe.reduce_samples(rows, _archive(probe))
    assert capture["stale"] == [
        {"sampled_at": first, "group": "BDFM", "state": "stale", "header_age_s": 600.75},
        {"sampled_at": first, "group": "G", "state": "undated", "header_age_s": None},
    ]
    assert capture["window"]["stale_rows"] == 2
    assert (capture["window"]["complete_samples"], capture["window"]["incomplete_samples"]) == (
        1,
        1,
    )
    assert capture["coverage"]["by_utc_hour"]["14"] == {"complete": 1, "incomplete": 1}
    # FX was only in the stale row, so it is unplaced; BDFM's other routes count once.
    assert "FX" in capture["placement"]["unplaced"]
    assert capture["routes"]["F"]["groups"]["BDFM"]["samples"] == 1
    assert capture["routes"]["G"]["groups"]["G"]["samples"] == 1
    bdfm = capture["groups"]["BDFM"]
    assert (bdfm["live_samples"], bdfm["stale_samples"], bdfm["failed_samples"]) == (1, 1, 0)
    # The stale age still shows in the group's header ages, so a freeze is visible there.
    assert bdfm["header_age_s"]["max"] == 600.75
    assert capture["groups"]["G"]["stale_samples"] == 1


def test_a_route_in_two_groups_is_shown_under_both_and_not_resolved(probe):
    rows = _sample(probe, "2026-11-04T14:00:00Z") + _sample(
        probe,
        "2026-11-04T14:10:00Z",
        BDFM=_feed(int(_ts("2026-11-04T14:10:00Z")), {**_OTHERS["BDFM"], "H": (1, 0)}),
    )
    capture = probe.reduce_samples(rows, _archive(probe))
    placement = capture["placement"]
    assert placement["in_more_than_one_group"] == {"H": ["ACE", "BDFM"]}
    assert placement["counts"]["in_more_than_one_group"] == 1
    assert "H" not in placement["placed"]
    assert "H" not in placement["unplaced"]
    h = capture["routes"]["H"]["groups"]
    assert (h["ACE"]["samples"], h["ACE"]["trip_updates"], h["ACE"]["vehicles"]) == (2, 2, 2)
    assert h["ACE"]["first_seen"] == "2026-11-04T14:00:00Z"
    assert h["ACE"]["last_seen"] == "2026-11-04T14:10:00Z"
    assert (h["BDFM"]["samples"], h["BDFM"]["trip_updates"], h["BDFM"]["vehicles"]) == (1, 1, 0)
    assert h["BDFM"]["first_seen"] == h["BDFM"]["last_seen"] == "2026-11-04T14:10:00Z"
    assert "H" in capture["groups"]["BDFM"]["routes"]


def test_a_route_outside_the_archive_in_two_groups_is_flagged_too(probe):
    at = int(_ts("2026-11-04T14:00:00Z"))
    rows = _sample(
        probe,
        "2026-11-04T14:00:00Z",
        ACE=_feed(at, {**_OTHERS["ACE"], "5X": (1, 0)}),
        JZ=_feed(at, {**_OTHERS["JZ"], "5X": (0, 1)}),
    )
    placement = probe.reduce_samples(rows, _archive(probe))["placement"]
    assert placement["in_more_than_one_group"] == {"5X": ["ACE", "JZ"]}
    assert placement["counts"]["in_more_than_one_group"] == 1
    assert placement["seen_not_in_archive"] == ["5X"]


def test_an_archive_route_never_seen_is_stated_unplaced_and_a_stray_route_is_listed(probe):
    at = int(_ts("2026-11-04T14:00:00Z"))
    rows = _sample(
        probe,
        "2026-11-04T14:00:00Z",
        # FX in vehicle positions only: a vehicle is as much evidence as a trip update.
        BDFM=_feed(at, {**_OTHERS["BDFM"], "FX": (0, 1)}),
        JZ=_feed(at, {"J": (1, 1), "5X": (1, 0)}),
    )
    capture = probe.reduce_samples(rows, _archive(probe))
    placement = capture["placement"]
    assert placement["placed"]["FX"] == "BDFM"
    fx = capture["routes"]["FX"]["groups"]["BDFM"]
    assert (fx["trip_updates"], fx["vehicles"]) == (0, 1)
    assert placement["unplaced"] == ["6X", "7X", "Z"]
    assert placement["seen_not_in_archive"] == ["5X"]
    assert capture["routes"]["5X"]["in_archive"] is False
    assert "5X" not in placement["placed"]


def test_unattributed_and_empty_live_rows_are_counted_per_group(probe):
    """A route hidden in entities with no route_id, or a group that went quiet, must be
    visible in the capture beside an `unplaced` it might explain."""
    at = int(_ts("2026-11-04T14:00:00Z"))
    jz = pb.FeedMessage()
    jz.ParseFromString(_feed(at, {"J": (1, 1)}))
    jz.entity.add(id="no-route-tu").trip_update.trip.trip_id = "z-trip"
    jz.entity.add(id="no-route-vp").vehicle.vehicle.id = "z-car"
    quiet = pb.FeedMessage()
    quiet.header.gtfs_realtime_version = "1.0"
    quiet.header.timestamp = at
    rows = _sample(
        probe, "2026-11-04T14:00:00Z", JZ=jz.SerializeToString(), L=quiet.SerializeToString()
    )
    groups = probe.reduce_samples(rows, _archive(probe))["groups"]
    assert groups["JZ"]["no_route_id"] == {"trip_update": 1, "vehicle": 1}
    assert groups["ACE"]["no_route_id"] == {"trip_update": 0, "vehicle": 0}
    assert (groups["L"]["empty_live_samples"], groups["L"]["live_samples"]) == (1, 1)
    assert groups["L"]["routes"] == []
    assert groups["JZ"]["empty_live_samples"] == 0


def test_hour_coverage_counts_complete_samples_and_route_evidence_counts_every_live_row(probe):
    rows = (
        _sample(probe, "2026-11-04T13:00:00Z")
        + _sample(probe, "2026-11-04T14:00:00Z", BDFM=(503, b""))
        + _sample(probe, "2026-11-04T14:30:00Z", SIR=httpx.ReadTimeout("slow"))
    )
    capture = probe.reduce_samples(rows, _archive(probe))
    hours = capture["coverage"]["by_utc_hour"]
    assert hours["13"] == {"complete": 1, "incomplete": 0}
    assert hours["14"] == {"complete": 0, "incomplete": 2}
    assert len(hours) == 24
    assert "13" not in capture["coverage"]["utc_hours_without_complete_sample"]
    assert "14" in capture["coverage"]["utc_hours_without_complete_sample"]
    assert capture["window"] == {
        "first_sample": "2026-11-04T13:00:00Z",
        "last_sample": "2026-11-04T14:30:00Z",
        "samples": 3,
        "complete_samples": 1,
        "incomplete_samples": 2,
        "failed_rows": 2,
        "stale_rows": 0,
        "samples_by_provenance": {"dispatched": 0, "local": 3, "scheduled": 0, "unrecorded": 0},
    }
    assert capture["failed"] == [
        {"sampled_at": "2026-11-04T14:00:00Z", "group": "BDFM", "status": 503, "error": "HTTP 503"},
        {
            "sampled_at": "2026-11-04T14:30:00Z",
            "group": "SIR",
            "status": None,
            "error": "ReadTimeout: slow",
        },
    ]
    # ACE was live in all three samples, so all three are evidence, complete or not.
    assert capture["routes"]["A"]["groups"]["ACE"]["samples"] == 3
    assert capture["routes"]["A"]["groups"]["ACE"]["utc_hours_seen"] == [13, 14]
    assert capture["routes"]["F"]["groups"]["BDFM"]["samples"] == 2
    assert capture["groups"]["BDFM"]["live_samples"] == 2
    assert capture["groups"]["BDFM"]["failed_samples"] == 1


def test_a_sample_missing_a_group_row_is_incomplete_and_says_which(probe):
    rows = [row for row in _sample(probe, "2026-11-04T13:00:00Z") if row["group"] != "L"]
    capture = probe.reduce_samples(rows, _archive(probe))
    assert capture["window"]["complete_samples"] == 0
    assert capture["failed"] == [
        {"sampled_at": "2026-11-04T13:00:00Z", "group": "L", "status": None, "error": "no row"}
    ]
    assert capture["groups"]["L"]["failed_samples"] == 1


@pytest.mark.parametrize(
    ("stamp", "window"),
    [
        # Wednesday 2026-11-04 (New York is UTC-5 after 2026-11-01).
        pytest.param("2026-11-04T09:59:59Z", "2026-11-03", id="04:59:59 Wed is Tuesday's night"),
        pytest.param("2026-11-04T10:00:00Z", "2026-11-04", id="05:00:00 Wed opens Wednesday"),
        pytest.param("2026-11-04T14:00:00Z", "2026-11-04", id="09:00 Wed"),
        # Friday's overnight runs into Saturday morning and is Friday's.
        pytest.param("2026-11-07T08:00:00Z", "2026-11-06", id="03:00 Sat is Friday's night"),
        pytest.param("2026-11-07T10:00:00Z", None, id="05:00 Sat is no window"),
        # Sunday's overnight runs into Monday morning and is no weekday's.
        pytest.param("2026-11-09T08:00:00Z", None, id="03:00 Mon is Sunday's night"),
        pytest.param("2026-11-09T10:00:00Z", "2026-11-09", id="05:00 Mon opens Monday"),
    ],
)
def test_a_weekday_window_runs_from_five_in_new_york_to_five_the_next_morning(probe, stamp, window):
    windows = probe.reduce_samples(_sample(probe, stamp), _archive(probe))["coverage"][
        "weekday_windows"
    ]
    assert list(windows) == ([] if window is None else [window])
    if window is not None:
        local_hour = (datetime.fromtimestamp(_ts(stamp), tz=probe.NEW_YORK)).strftime("%H")
        assert local_hour not in windows[window]["new_york_hours_without_complete_sample"]
        assert len(windows[window]["new_york_hours_without_complete_sample"]) == 23
        assert windows[window]["weekday"] == datetime.strptime(window, "%Y-%m-%d").strftime("%A")


def test_a_window_is_fully_covered_only_when_every_new_york_hour_has_a_complete_sample(probe):
    stamps = _hourly("2026-11-04T10:00:00Z")  # from 05:02 Wednesday in New York
    full = [row for stamp in stamps for row in _sample(probe, stamp)]
    coverage = probe.reduce_samples(full, _archive(probe))["coverage"]
    assert coverage["weekday_windows_fully_covered"] == ["2026-11-04"]
    assert coverage["utc_hours_without_complete_sample"] == []

    # The same day with 02:00 New York's one sample failing is not covered, and says so.
    gap = stamps[21]  # 10:02Z + 21h = 07:02Z on Thursday, 02:02 in New York
    rows = [row for stamp in stamps if stamp != gap for row in _sample(probe, stamp)]
    rows += _sample(probe, gap, G=(500, b""))
    coverage = probe.reduce_samples(rows, _archive(probe))["coverage"]
    assert coverage["weekday_windows_fully_covered"] == []
    window = coverage["weekday_windows"]["2026-11-04"]
    assert window["new_york_hours_without_complete_sample"] == ["02"]
    assert (window["complete_samples"], window["incomplete_samples"]) == (23, 1)

    # Many samples are not many hours: 30 complete samples over 23 hours do not cover it.
    extra = [_stamp(_ts(stamps[0]) + 60 * m) for m in range(1, 8)]
    rows = [row for stamp in stamps if stamp != gap for row in _sample(probe, stamp)]
    rows += [row for stamp in extra for row in _sample(probe, stamp)]
    coverage = probe.reduce_samples(rows, _archive(probe))["coverage"]
    assert coverage["weekday_windows"]["2026-11-04"]["complete_samples"] == 30
    assert coverage["weekday_windows_fully_covered"] == []


def test_a_holiday_on_sunday_service_is_listed_but_never_covers(probe):
    """Thanksgiving 2026-11-26 is a Thursday on which the live archive removes Weekday and
    adds Sunday. Sampled through every hour, it is listed and is not a weekday service day;
    the Wednesday before it is."""
    calendar = probe._read_calendar(_CALENDAR_TXT, _CALENDAR_DATES_TXT)
    stamps = _hourly("2026-11-25T10:00:00Z", hours=48)
    rows = [row for stamp in stamps for row in _sample(probe, stamp)]
    coverage = probe.reduce_samples(rows, _archive(probe, calendar=calendar))["coverage"]
    windows = coverage["weekday_windows"]
    assert windows["2026-11-25"]["weekday_services"] == ["Weekday"]
    assert windows["2026-11-26"]["weekday_services"] == []
    assert windows["2026-11-26"]["new_york_hours_without_complete_sample"] == []
    assert coverage["weekday_windows_fully_covered"] == ["2026-11-25"]
    # Without a calendar (routes.txt alone) the capture cannot know, and says so.
    bare = probe.reduce_samples(rows, _archive(probe))
    assert bare["archive"]["calendar"] is False
    assert bare["coverage"]["weekday_windows"]["2026-11-26"]["weekday_services"] is None
    assert bare["coverage"]["weekday_windows_fully_covered"] == ["2026-11-25", "2026-11-26"]


def test_the_weekday_rule_reads_the_calendar_as_published(probe):
    from datetime import date

    calendar = probe._read_calendar(_CALENDAR_TXT, _CALENDAR_DATES_TXT)
    assert calendar.weekday_services(date(2026, 11, 4)) == ["Weekday"]
    assert calendar.weekday_services(date(2026, 11, 26)) == []  # Thanksgiving
    assert calendar.weekday_services(date(2026, 12, 25)) == []
    assert calendar.weekday_services(date(2026, 10, 30)) == []  # before the pick starts
    assert calendar.weekday_services(date(2027, 5, 14)) == ["Weekday"]
    assert calendar.weekday_services(date(2027, 5, 17)) == []  # after it ends


def test_the_reduction_is_byte_identical_however_the_samples_lie_on_disk(probe, tmp_path):
    samples = [
        _sample(probe, "2026-11-04T13:00:00Z"),
        _sample(probe, "2026-11-04T13:10:00Z", ACE=(503, b"")),
        _sample(probe, "2026-11-04T23:40:00Z"),
    ]
    flat = tmp_path / "flat"
    for rows in samples:
        probe.write_sample(rows, flat)
    # As a download of artifacts lays them out: one directory each, in another order,
    # each file's lines reversed, and one sample present twice.
    nested = tmp_path / "nested"
    for i, rows in enumerate(reversed(samples)):
        path = probe.write_sample(list(reversed(rows)), nested / f"artifact-{i}")
    shutil.copy(path, nested / "duplicate.jsonl")

    archive = _archive(probe)
    a = probe.render_capture(probe.reduce_samples(probe.load_rows(flat), archive))
    b = probe.render_capture(probe.reduce_samples(probe.load_rows(nested), archive))
    assert a == b
    assert json.loads(a)["window"]["samples"] == 3
    # Deterministic in key order too, at every depth, as committed.
    assert a == json.dumps(json.loads(a), indent=2, sort_keys=True) + "\n"


def test_two_different_rows_for_one_group_in_one_sample_refuse_the_reduction(probe):
    rows = _sample(probe, "2026-11-04T13:00:00Z")
    altered = dict(rows[1], bytes=rows[1]["bytes"] + 1)
    with pytest.raises(ValueError, match="two different rows for ACE"):
        probe.reduce_samples(rows + [altered], _archive(probe))


def test_a_group_sampled_from_two_urls_refuses_the_reduction(probe):
    rows = _sample(probe, "2026-11-04T13:00:00Z") + [
        dict(row, url="http://127.0.0.1:9/nyct%2Fgtfs-ace") if row["group"] == "ACE" else row
        for row in _sample(probe, "2026-11-04T13:10:00Z")
    ]
    with pytest.raises(ValueError, match="more than one URL"):
        probe.reduce_samples(rows, _archive(probe))


def test_a_line_that_is_not_json_refuses_the_directory_with_its_place(probe, tmp_path):
    probe.write_sample(_sample(probe, "2026-11-04T13:00:00Z"), tmp_path)
    (tmp_path / "torn.jsonl").write_text('{"sampled_at": "2026-11-04T13:10:00Z", "gro\n')
    with pytest.raises(ValueError, match=r"torn\.jsonl:1: not a JSON line"):
        probe.load_rows(tmp_path)


def test_nothing_to_reduce_is_refused_rather_than_written_empty(probe):
    with pytest.raises(ValueError, match="no sample rows"):
        probe.reduce_samples([], _archive(probe))


def test_the_archive_routes_read_the_same_from_the_zip_and_from_routes_txt(probe, tmp_path):
    routes_txt = (
        "\ufeffroute_id,agency_id,route_short_name\nGS,MTA NYCT,S\nA,MTA NYCT,A\nFS,MTA NYCT,S\n"
    ).encode()
    feed_info = b"feed_publisher_name,feed_version\nMTA New York City Transit,20261028-x\n"
    bare = tmp_path / "routes.txt"
    bare.write_bytes(routes_txt)
    archive = tmp_path / "gtfs_subway.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("routes.txt", routes_txt)
        z.writestr("feed_info.txt", feed_info)
        z.writestr("calendar.txt", _CALENDAR_TXT)
        z.writestr("calendar_dates.txt", _CALENDAR_DATES_TXT)

    from_zip = probe.read_archive_routes(archive)
    from_txt = probe.read_archive_routes(bare)
    assert from_zip.route_ids == from_txt.route_ids == ("A", "FS", "GS")
    assert from_zip.routes_txt_sha256 == from_txt.routes_txt_sha256
    assert from_zip.feed_version == "20261028-x"
    assert from_txt.feed_version is None
    assert from_zip.calendar == probe._read_calendar(_CALENDAR_TXT, _CALENDAR_DATES_TXT)
    assert from_txt.calendar is None

    bare.write_bytes(b"route_id\nA\nA\n")
    with pytest.raises(ValueError, match="twice"):
        probe.read_archive_routes(bare)
    bare.write_bytes(b"route_id\n")
    with pytest.raises(ValueError, match="no rows"):
        probe.read_archive_routes(bare)


def test_the_cli_reduce_writes_the_capture_and_needs_the_archive(probe, tmp_path, capsys):
    samples = tmp_path / "samples"
    probe.write_sample(_sample(probe, "2026-11-04T13:00:00Z"), samples)
    routes = tmp_path / "routes.txt"
    routes.write_text("route_id\n" + "\n".join(_archive(probe).route_ids) + "\n")
    out = tmp_path / "capture.json"
    argv = ["--reduce", str(samples), "--routes", str(routes), "--output", str(out)]
    assert probe.main(argv) == 0
    capture = json.loads(out.read_text())
    assert capture["placement"]["placed"]["GS"] == "1-7+S"
    assert capsys.readouterr().err.strip() == (
        "1 samples, 26 of 29 placed, 0 in more than one group, 3 unplaced"
    )
    with pytest.raises(SystemExit) as refused:
        probe.main(["--reduce", str(samples)])
    assert refused.value.code == 2


def test_the_capture_states_what_it_was_compared_against_and_what_it_fetched(probe):
    at = [_ts(s) for s in ("2026-11-04T13:00:00Z", "2026-11-04T13:10:00Z", "2026-11-04T13:20:00Z")]
    # ACE's header 5, 65 and 305 s before each start; read 0.5 s after it (the third read).
    rows = [
        row
        for when, lag in zip(at, (5, 65, 305))
        for row in _sample(probe, _stamp(when), ACE=_feed(int(when) - lag, _OTHERS["ACE"]))
    ]
    capture = probe.reduce_samples(rows, _archive(probe))
    assert capture["archive"] == {
        "feed_version": "test-archive",
        "routes_txt_sha256": "0" * 64,
        "route_ids": list(_archive(probe).route_ids),
        "calendar": False,
    }
    assert capture["stale_after_s"] == 600.0
    assert capture["expected_groups"] == list(SUBWAY_FEED_URLS)
    assert {g: v["urls"] for g, v in capture["groups"].items()} == {
        g: [url] for g, url in SUBWAY_FEED_URLS.items()
    }
    assert capture["groups"]["ACE"]["header_age_s"] == {"median": 65.5, "max": 305.5}


# ---------------------------------------------------------------------------
# The loop: its clock and its stop rule
# ---------------------------------------------------------------------------


class _Time:
    """A fake clock and sleep in one: a read costs `read_s`, a sleep advances by what it
    was asked, and `jump` adds an overrun in the middle of a sample."""

    def __init__(self, start: float, read_s: float = 0.05):
        self.now = start
        self.read_s = read_s
        self.sleeps: list[float] = []

    def clock(self) -> float:
        value = self.now
        self.now += self.read_s
        return value

    def sleep(self, seconds: float) -> None:
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


def _loop_fetch(probe, time_: _Time, overrun: dict[int, float] | None = None):
    """A fetch serving complete, live bodies at the fake clock's current time. `overrun`
    maps a sample's index to seconds added during its first fetch."""
    calls = {"n": 0}

    def fetch(url: str):
        index = calls["n"] // len(SUBWAY_FEED_URLS)
        if overrun and calls["n"] % len(SUBWAY_FEED_URLS) == 0 and index in overrun:
            time_.now += overrun[index]
        calls["n"] += 1
        group = next(g for g, u in SUBWAY_FEED_URLS.items() if u == url)
        return probe.Fetched(200, _complete_bodies(time_.now)[group])

    return fetch


def _offsets(samples, start: float) -> list[float]:
    return [_ts(rows[0]["sampled_at"]) - start for rows in samples]


def test_a_loop_samples_on_the_ten_minute_grid_into_one_file_named_for_its_start(probe, tmp_path):
    start = _ts("2026-11-04T14:00:00Z")
    time_ = _Time(start)
    path, samples = probe.run_loop(
        tmp_path,
        _loop_fetch(probe, time_),
        time_.clock,
        time_.sleep,
        budget_s=30 * 60,
        provenance="dispatched",
    )
    assert path == tmp_path / "20261104T140000Z.jsonl"
    assert list(tmp_path.iterdir()) == [path]
    assert _offsets(samples, start) == [0, 600, 1200]
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [row for sample in samples for row in sample]
    assert len(rows) == 3 * len(SUBWAY_FEED_URLS)
    assert {row["provenance"] for row in rows} == {"dispatched"}
    # It slept up to each slot, never past it: the sample's own reads are the remainder.
    assert len(time_.sleeps) == 2 and all(590 < s < 600 for s in time_.sleeps)
    # And it does not sleep out the rest of the budget after its last sample.
    assert time_.now < start + 1200 + 60


@pytest.mark.parametrize(
    ("budget_minutes", "taken"),
    [(1, 1), (10, 1), (11, 2), (20, 2), (21, 3), (340, 34)],
)
def test_the_stop_rule_takes_only_slots_strictly_before_the_budget(
    probe, tmp_path, budget_minutes, taken
):
    start = _ts("2026-11-04T14:00:00Z")
    time_ = _Time(start)
    _, samples = probe.run_loop(
        tmp_path, _loop_fetch(probe, time_), time_.clock, time_.sleep, budget_s=budget_minutes * 60
    )
    assert len(samples) == taken == probe.loop_slots(start, budget_minutes * 60)
    assert max(_offsets(samples, start)) < budget_minutes * 60


def test_an_overrun_skips_the_slots_it_missed_and_stays_on_the_grid(probe, tmp_path):
    """Sample 1 (at +600) takes 700 s, so slot +1200 has passed when it ends: the loop takes
    +1800 next, never +1300 (a burst) and never +1200 late."""
    start = _ts("2026-11-04T14:00:00Z")
    time_ = _Time(start)
    _, samples = probe.run_loop(
        tmp_path,
        _loop_fetch(probe, time_, overrun={1: 700.0}),
        time_.clock,
        time_.sleep,
        budget_s=40 * 60,
    )
    assert _offsets(samples, start) == [0, 600, 1800]


def test_an_overrun_past_the_budget_ends_the_loop_without_another_sample(probe, tmp_path):
    start = _ts("2026-11-04T14:00:00Z")
    time_ = _Time(start)
    _, samples = probe.run_loop(
        tmp_path,
        _loop_fetch(probe, time_, overrun={1: 700.0}),
        time_.clock,
        time_.sleep,
        budget_s=20 * 60,
    )
    assert _offsets(samples, start) == [0, 600]


def test_a_loop_killed_partway_keeps_every_sample_it_finished(probe, tmp_path):
    start = _ts("2026-11-04T14:00:00Z")
    time_ = _Time(start)
    taken: list[list[dict]] = []

    def killed_after_two(rows):
        taken.append(rows)
        if len(taken) == 2:
            raise KeyboardInterrupt  # the step timeout's SIGTERM, near enough

    with pytest.raises(KeyboardInterrupt):
        probe.run_loop(
            tmp_path,
            _loop_fetch(probe, time_),
            time_.clock,
            time_.sleep,
            budget_s=60 * 60,
            on_sample=killed_after_two,
        )
    (path,) = tmp_path.iterdir()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows == [row for sample in taken for row in sample]


@pytest.mark.parametrize("budget_s", [0, -60])
def test_a_loop_needs_a_positive_budget(probe, tmp_path, budget_s):
    time_ = _Time(_ts("2026-11-04T14:00:00Z"))
    with pytest.raises(ValueError, match="positive budget"):
        probe.run_loop(tmp_path, _loop_fetch(probe, time_), time_.clock, time_.sleep, budget_s)
    assert list(tmp_path.iterdir()) == []


def test_the_cli_loop_prints_the_file_and_a_tally_per_sample(probe, tmp_path, capsys):
    start = _ts("2026-11-04T14:00:00Z")
    time_ = _Time(start)

    def handler(request: httpx.Request) -> httpx.Response:
        group = next(g for g, u in SUBWAY_FEED_URLS.items() if u == str(request.url))
        return httpx.Response(200, content=_complete_bodies(time_.now)[group])

    def client_factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    argv = ["--out-dir", str(tmp_path), "--loop-minutes", "20", "--provenance", "dispatched"]
    code = probe.main(argv, client_factory=client_factory, clock=time_.clock, sleep=time_.sleep)
    assert code == 0
    out, err = capsys.readouterr()
    assert out.strip() == str(tmp_path / "20261104T140000Z.jsonl")
    assert err.strip().splitlines() == [
        "2026-11-04T14:00:00Z: 8 of 8 feeds ok",
        "2026-11-04T14:10:00Z: 8 of 8 feeds ok",
        "2 samples in one loop of 20 minutes",
    ]
    rows = [json.loads(line) for line in Path(out.strip()).read_text().splitlines()]
    assert {row["provenance"] for row in rows} == {"dispatched"}


@pytest.mark.parametrize(
    "argv",
    [["--loop-minutes", "0"], ["--loop-minutes", "ten"], ["--provenance", "cron"]],
)
def test_the_cli_refuses_a_budget_below_a_minute_and_an_unknown_provenance(probe, tmp_path, argv):
    def no_client(**kwargs):
        raise AssertionError("a client was built for a refused command line")

    with pytest.raises(SystemExit) as refused:
        probe.main(["--out-dir", str(tmp_path), *argv], client_factory=no_client)
    assert refused.value.code == 2


def test_the_capture_counts_samples_by_provenance_and_reads_old_rows_as_unrecorded(probe):
    at = _ts("2026-11-04T14:00:00Z")
    scheduled = probe.take_sample(
        _fetch_for(probe, _complete_bodies(at)), _Clock(at), provenance="scheduled"
    )
    dispatched = probe.take_sample(
        _fetch_for(probe, _complete_bodies(at + 600)), _Clock(at + 600), provenance="dispatched"
    )
    legacy = [
        {k: v for k, v in row.items() if k != "provenance"}
        for row in _sample(probe, "2026-11-04T14:20:00Z")
    ]
    capture = probe.reduce_samples(scheduled + dispatched + legacy, _archive(probe))
    assert capture["window"]["samples_by_provenance"] == {
        "dispatched": 1,
        "local": 0,
        "scheduled": 1,
        "unrecorded": 1,
    }
    mixed = [dict(row, provenance="local") if i == 3 else row for i, row in enumerate(scheduled)]
    with pytest.raises(ValueError, match="mixes provenances"):
        probe.reduce_samples(mixed, _archive(probe))
    with pytest.raises(ValueError, match="provenance must be one of"):
        probe.take_sample(_fetch_for(probe, _complete_bodies(at)), _Clock(at), provenance="cron")


# ---------------------------------------------------------------------------
# The temporary workflow (deleted, with this section, by the commit that adds the capture)
# ---------------------------------------------------------------------------


def _workflow():
    import yaml

    text = _WORKFLOW.read_text()
    return text, yaml.safe_load(text)


def _steps():
    _, workflow = _workflow()
    (job,) = workflow["jobs"].values()
    return job, job["steps"]


def _step(name_part: str) -> dict:
    _, steps = _steps()
    (step,) = [s for s in steps if name_part in s.get("name", "")]
    return step


def test_the_workflow_keeps_its_schedule_and_dispatches_a_loop():
    _, workflow = _workflow()
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1).
    triggers = workflow[True]
    assert triggers["schedule"] == [{"cron": "*/10 * * * *"}]
    loop_minutes = triggers["workflow_dispatch"]["inputs"]["loop_minutes"]
    assert (loop_minutes["type"], loop_minutes["default"]) == ("number", 340)


def test_each_trigger_queues_in_its_own_group_and_late_runs_wait():
    _, workflow = _workflow()
    concurrency = workflow["concurrency"]
    # A sample must never wait hours behind a loop, so the group is per trigger.
    assert concurrency["group"] == "probe-subway-groups-${{ github.event_name }}"
    # cancel-in-progress false keeps a running loop; queue max keeps every waiting one
    # (the default, single, would let a newer arrival cancel the one already waiting).
    assert concurrency["cancel-in-progress"] is False
    assert concurrency["queue"] == "max"


def test_the_timeouts_nest_budget_inside_step_inside_job():
    """The default loop starts its last sample before minute 340 and ends seconds later;
    the sample step dies at 350; the job at 358, under GitHub's 360. The ten minutes between
    budget and step timeout are one whole interval of slack for a slow last sample."""
    _, workflow = _workflow()
    job, _ = _steps()
    budget = workflow[True]["workflow_dispatch"]["inputs"]["loop_minutes"]["default"]
    step_timeout = _step("Take one sample")["timeout-minutes"]
    assert job["timeout-minutes"] == 358
    assert budget + 10 <= step_timeout < job["timeout-minutes"] < 360


def test_the_workflow_is_read_only_and_no_expression_in_it_reads_a_secret():
    text, workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    expressions = re.findall(r"\$\{\{(.*?)\}\}", text, flags=re.S)
    assert expressions, "the workflow's expressions were not found, so this checks nothing"
    reading = [e.strip() for e in expressions if re.search(r"\bsecrets\b", e)]
    assert reading == [], "a sample is keyless; no secret may reach this job"
    for name, job in workflow["jobs"].items():
        assert "environment" not in job, f"{name} must not reach the monitor environment"
        assert "permissions" not in job, f"{name} must not widen the workflow's permissions"


def test_the_workflow_runs_on_the_lock_and_uploads_whatever_the_sample_step_did():
    _, steps = _steps()
    python = next(s for s in steps if s.get("uses", "").startswith("actions/setup-python@"))
    assert python["with"]["python-version"] == "3.12"
    runs = [s["run"] for s in steps if "run" in s]
    assert "pip install -r backend/requirements.lock" in runs
    sample = _step("Take one sample")
    assert sample["working-directory"] == "backend"
    assert "--reduce" not in sample["run"]
    naming = _step("Name the artifact")
    assert (naming["id"], naming["if"]) == ("sample", "always()")
    (upload,) = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
    # A loop the step timeout kills still uploads every sample it finished.
    assert upload["if"] == "always() && steps.sample.outputs.path != ''"
    assert upload["with"]["name"] == "${{ steps.sample.outputs.name }}"
    assert upload["with"]["path"] == "${{ steps.sample.outputs.path }}"
    assert upload["with"]["if-no-files-found"] == "error"


def _run_step(step: dict, tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    """A step's shell, run as Actions runs it (bash -eo pipefail)."""
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("event", "loop_minutes", "expected"),
    [
        ("schedule", "", "--provenance scheduled"),
        ("workflow_dispatch", "340", "--provenance dispatched --loop-minutes 340"),
    ],
)
def test_the_sample_step_takes_one_sample_on_schedule_and_a_loop_on_dispatch(
    tmp_path, event, loop_minutes, expected
):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_file = tmp_path / "argv"
    fake = bin_dir / "python"
    fake.write_text(f'#!/bin/sh\necho "$@" > {argv_file}\n')
    fake.chmod(0o755)
    runner_temp = tmp_path / "runner"
    result = _run_step(
        _step("Take one sample"),
        tmp_path,
        {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(runner_temp),
            "EVENT": event,
            "LOOP_MINUTES": loop_minutes,
        },
    )
    assert result.returncode == 0, result.stderr
    assert argv_file.read_text().split() == (
        f"scripts/probe_subway_groups.py --out-dir {runner_temp}/samples {expected}".split()
    )


@pytest.mark.parametrize(
    ("event", "prefix"),
    [("schedule", "subway-groups"), ("workflow_dispatch", "subway-groups-loop")],
)
def test_the_artifact_is_named_by_the_files_own_utc_time(tmp_path, event, prefix):
    """What reaches GITHUB_OUTPUT is what the upload step reads: a typo in `name=` or
    `path=` fails here, and so does a directory holding anything but one sample file."""
    runner_temp = tmp_path / "runner"
    (runner_temp / "samples").mkdir(parents=True)
    sample = runner_temp / "samples" / "20261104T140733Z.jsonl"
    sample.write_text("{}\n")
    output = tmp_path / "github_output"
    output.write_text("")
    env = {"RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output), "EVENT": event}
    result = _run_step(_step("Name the artifact"), tmp_path, env)
    assert result.returncode == 0, result.stderr
    written = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert written == {"path": str(sample), "name": f"{prefix}-20261104T140733Z"}

    (runner_temp / "samples" / "20261104T150000Z.jsonl").write_text("{}\n")
    output.write_text("")
    assert _run_step(_step("Name the artifact"), tmp_path, env).returncode != 0
    assert output.read_text() == ""
