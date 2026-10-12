#!/usr/bin/env python3
"""A10 (Low): "Headerless successful LIRR generation inherits an old content clock."

Reproduction and regression check for finding A10 of the 2026-10-12 audit (the report's
"Additional confirmed narrow defects" table), over main at 1e3e73d.

THE AUDIT'S CLAIM, the row's evidence cell verbatim:

    "backend/feeds/railroad.py:1325 omits the system key when the timestamp is null.
    backend/pollers.py:457 interprets absence as retention. Reproduced successful
    generation at poll time 1000 retaining content time 900."

    Recommended correction: "Emit explicit null for a successfully decoded but undated
    generation; preserve prior timestamps only for actual failures. Test timestamped →
    headerless → failed transitions."

    Cited code (where to LOOK; nothing below asserts a line, a name or a comment):
      https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/feeds/railroad.py#L1325
      https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/pollers.py#L457

THE CONVENTION THE ROW IS ABOUT. The per-system block builder in pollers.py reads its
content-clock map by MEMBERSHIP: a system ABSENT from the map did not decode this poll
and keeps its last content time; a system PRESENT with None decoded a generation whose
header carried no timestamp, and publishes None. Contract 6.1's adversarial pass
installed that reader (c3e3091). The subway producer records a key for every group that
decoded, so it obeys. The railroad producer records LIRR's key only when the header
carries a timestamp, so a decoded but undated LIRR generation is indistinguishable, at
the reader, from a failed one.

WHAT EVERY FIX MUST CHANGE, AND HOW THIS SCRIPT MAKES IT VISIBLE. The defect is that the
block serves the PREVIOUS generation's clock, as healthy, for a generation that decoded.
A repair may publish null (the audit's correction), date the generation from something
it carries itself (its entities' own clocks, the HTTP response, the poll), or refuse it
as a failure. So every headerless generation here carries such clocks, all newer than
the previous header: its trip_update (and, in D, every vehicle) is dated, and the fake
upstream answers with Date and Last-Modified at the poll clock. The checks pin the served
block to ok true with the previous header, so any repair, wherever it lives and
whichever of those it chooses, moves at least one of them. Where the key is dropped (the
fetcher, the call site, the reader) is the ledger's mechanism and is printed, never
checked: a refactor that moves the filter without changing what is served stays green.

WHAT THIS SCRIPT MEASURES

  A. What each public fetcher returns for one successful headerless generation. Checked:
     that it decoded (no failed system). Printed, for the ledger: the content-clock map
     each hands on, which is where the two producers differ today.
  B. The audit's own sequence on the railroad, through the real poll and the real app:
     a timestamped LIRR generation at poll time 900, a successful headerless one at 1000
     whose own trip_update is dated 990, a FAILED one at 1100; and, separately,
     timestamped at 900 then failed at 1000. Read off the served JSON: GET /api/railroads
     systems.LIRR, the LIRR station board's content clock, and GET /api/status.
  C. THE CONTROL, which is the row's point: the same two sequences, the same bytes,
     through the SUBWAY producer and the same reader. Its block publishes null at 1000,
     and a real failure retains correctly on both families. C is also the reader's only
     witness here: reverted to a value read (the defect c3e3091 fixed), C goes red while
     the backend suite at 1e3e73d stays green, 1763 passed (measured 2026-10-12).
  D. The same defect on the committed LIRR capture: one poll as captured, then five
     successful polls of the NEXT generation, each a copy whose header carries no
     timestamp and whose every trip_update and vehicle clock is advanced by the poll's
     offset (as a live feed's next generation would be). The block's content clock holds
     the captured header while fetched_at and the generation's own clocks advance, so the
     age the block implies (fetched_at minus feed_timestamp, design 3.5's per-system
     _feed_age) grows by one interval every interval and passes FEED_STALE_AFTER_S on a
     feed that decoded successfully every time.
  Also measured, BEYOND THE AUDIT'S CITATION, in B, C and D: the railroad ENVELOPE's
  feed_timestamp (and so /api/status feeds.railroads.feed_age_s) keeps the old clock on
  the same headerless success, while the subway envelope publishes null.

WHAT IS PRODUCTION CODE HERE (nothing is re-implemented): feeds.fetch_railroad_trains
(parse_feed, _decode_railroad_vehicles, _position_steps, _decode_railroad_feed),
feeds.fetch_subway_trains, pollers._refresh_railroads and pollers._refresh_subways (and
through them the per-system block builder and the retention merge), and the routes
GET /api/railroads, GET /api/subways, GET /api/railroad-arrivals/{system}/{stop_id} and
GET /api/status on main.app, served through httpx.ASGITransport.

WHAT IS INJECTED, all in memory:
  1. The upstream bodies. Sections A to C use quiet feeds built here (a real
     gtfs_realtime_version, header.timestamp set or left unset, and in the headerless
     generation one trip_update dated 990 with no stop_time_update, so it places
     nothing), the shape parse_feed accepts as a successful generation; a failed
     generation is an HTTP 503 that raise_for_status turns into a failed system, as in
     production. Section D uses the committed captures, LIRR's with header.timestamp
     cleared and its entities' own clocks advanced as described above.
  2. HTTP response headers: Date on every answer and Last-Modified on every 200, both at
     the poll clock. Nothing at 1e3e73d reads them; they are there so a repair that
     dated an undated generation by its response could not hide.
  3. time.time pinned to each poll's clock (900, 1000, 1100 in B and C; the capture's
     own header plus multiples of CADENCE_S in D).
  4. app.state primed as a lifespan would leave it (static status ready, the committed
     railroad and subway stops), and re-primed between sequences.

HERMETIC: _hermetic.contain() runs before the first backend import and verify() after it;
the MTA realtime base seams point at the loopback discard port and DATA_DIR at a fresh
temporary directory, both set before the backend reads them; the fake upstream records
and refuses any URL it was not given and any URL naming an NJ Transit host, and both lists
are checked empty. No socket, no credential, no dependence on today's date.

RUN (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a10_headerless_lirr_clock.py

RECORDED DISPOSITION: VERIFIED. Exits 0 while a headerless successful LIRR generation is
served the previous generation's content clock (not null, and not any clock it carries
itself) and the subway control is not; non-zero, naming every check that moved, the
moment either stops being true.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import shutil
import sys
import tempfile
import time
from email.utils import formatdate
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FIXTURES = BACKEND / "tests" / "fixtures"

# Seams the backend reads ONCE, at import, so they are set before the first backend
# import. Assigned rather than defaulted: a developer's shell or .env must not be able to
# point this process at the real MTA or at the repository's data/ directory.
_LOOPBACK = "http://127.0.0.1:9/a10"
os.environ["SUBWAY_RT_BASE"] = f"{_LOOPBACK}/subway"
os.environ["RAILROAD_RT_BASE"] = f"{_LOOPBACK}/railroad"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="a10-data-")
atexit.register(shutil.rmtree, os.environ["DATA_DIR"], ignore_errors=True)

# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT (see the 09-05 README).
sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import cache  # noqa: E402
import feeds  # noqa: E402
import main as app_module  # noqa: E402  (the real FastAPI app)
import pollers  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED.
_hermetic.verify()

APP = app_module.app
LIRR_URL = feeds.RAILROAD_FEED_URLS["LIRR"]
MNR_URL = feeds.RAILROAD_FEED_URLS["MNR"]
SUBWAY_GROUPS = list(feeds.SUBWAY_FEED_URLS)
# The subway group that plays LIRR's part in the control: it is the one that fails in
# the failure steps. Any group would do; every group gets the same bytes otherwise.
SUBJECT_GROUP = "ACE"

LIRR_STOPS = json.loads((FIXTURES / "railroad_lirr_stops.json").read_text())
MNR_STOPS = json.loads((FIXTURES / "railroad_mnr_stops.json").read_text())
SUBWAY_STOPS = json.loads((FIXTURES / "subway_1_7_s_stops.json").read_text())
LIRR_RAW = (FIXTURES / "railroad_lirr.pb").read_bytes()
MNR_RAW = (FIXTURES / "railroad_mnr.pb").read_bytes()
# Any LIRR station serves the board's content clock; the first by id is as good as any.
BOARD_STOP = sorted(LIRR_STOPS)[0]

# Section D's cadence: the production default POLL_INTERVAL_S, written here rather than
# read so an env override cannot move the recorded numbers.
CADENCE_S = 20
HEADERLESS_POLLS = 5

# The audit's three poll clocks.
T_STAMPED, T_HEADERLESS, T_FAILED = 900.0, 1000.0, 1100.0
# The clock the headerless 1000 generation's own trip_update carries: newer than the
# previous header (900) and distinct from the poll clock (1000), so the served value
# says which source a repair used.
T_OWN = 990.0
FAILED = 503  # a generation that fails: served as this status with no body

CLOCK = [0.0]
FORBIDDEN_URLS: list[str] = []
UNEXPECTED_URLS: list[str] = []
FAILURES: list[str] = []


def check(label: str, got, want, audit: str | None = None) -> None:
    """One named expectation. Every check runs; the script fails once at the end."""
    ok = got == want
    if not ok:
        FAILURES.append(f"{label}: measured {got!r}, recorded {want!r}")
    beside = f"   (audit: {audit})" if audit else ""
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {got!r}{beside}")


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def quiet_feed(timestamp: float | None, own_clock: float | None = None) -> bytes:
    """A successful generation with nothing running: a real header carrying the version
    field parse_feed requires, header.timestamp only when given and, when own_clock is
    given, one trip_update dated by it. That trip_update has no stop_time_update, so it
    places no train and indexes no arrival; it is only a clock the generation carries."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    if timestamp is not None:
        feed.header.timestamp = int(timestamp)
    if own_clock is not None:
        entity = feed.entity.add(id="a10-own-clock")
        entity.trip_update.trip.trip_id = "a10-own-clock"
        entity.trip_update.timestamp = int(own_clock)
    return feed.SerializeToString()


def headerless() -> bytes:
    """The successful headerless generation of sections A to C: no header timestamp, its
    own trip_update dated T_OWN. The railroad and every subway group get these bytes."""
    return quiet_feed(None, T_OWN)


def without_header_timestamp(raw: bytes, advance: int = 0) -> bytes:
    """A committed capture with header.timestamp cleared and every entity's own clock
    (trip_update.timestamp, vehicle.timestamp) advanced by `advance` seconds: the NEXT
    generation of a live feed, whose header alone lost its clock."""
    feed = pb.FeedMessage.FromString(raw)
    feed.header.ClearField("timestamp")
    for entity in feed.entity:
        if entity.HasField("trip_update") and entity.trip_update.timestamp:
            entity.trip_update.timestamp += advance
        if entity.HasField("vehicle") and entity.vehicle.timestamp:
            entity.vehicle.timestamp += advance
    return feed.SerializeToString()


def newest_entity_clock(raw: bytes) -> float:
    """The newest clock a generation's own entities carry (trip_update and vehicle
    timestamps), read off the bytes this script serves, to set beside the content clock
    the block serves for that generation."""
    feed = pb.FeedMessage.FromString(raw)
    stamps = [e.trip_update.timestamp for e in feed.entity if e.HasField("trip_update")]
    stamps += [e.vehicle.timestamp for e in feed.entity if e.HasField("vehicle")]
    return float(max(stamps))


class FakeUpstream:
    """Stands in for httpx.AsyncClient inside the REAL fetchers. A body is served as a
    200 carrying Date and Last-Modified at the poll clock (a clock of the response, there
    so a repair reading it shows up in the served value); an int is served as that
    status with no body and a Date. A URL it was not given, or one naming an NJ Transit
    host, is recorded and refused, and the script checks both records empty, because a
    refusal inside the fetchers' gather would otherwise read as an ordinary failed feed."""

    def __init__(self, bodies: dict[str, bytes | int]) -> None:
        self.bodies = bodies

    async def get(self, url: str) -> httpx.Response:
        lowered = url.lower()
        if "njtransit" in lowered or "raildata" in lowered:
            FORBIDDEN_URLS.append(url)
            raise RuntimeError(f"refused: {url} names an NJ Transit host")
        if url not in self.bodies:
            UNEXPECTED_URLS.append(url)
            raise RuntimeError(f"refused: {url} is not part of this world")
        body = self.bodies[url]
        request = httpx.Request("GET", url)
        stamp = formatdate(CLOCK[0], usegmt=True)
        if isinstance(body, int):
            return httpx.Response(body, headers={"Date": stamp}, request=request)
        headers = {"Date": stamp, "Last-Modified": stamp}
        return httpx.Response(200, content=body, headers=headers, request=request)


def prime() -> None:
    """The app.state a lifespan would have built, and the state each poll writes back
    into, reset so no sequence inherits another's last-known values."""
    state = APP.state
    state.feed_cache = {
        "subways": cache._fresh_entry(),
        "railroads": cache._fresh_entry(),
    }
    state.railroad_feed_health = None
    state.railroad_static_status = "ready"
    state.railroad_stops = {"LIRR": LIRR_STOPS, "MNR": MNR_STOPS}
    state.railroad_arrivals = {}
    state.railroad_positions = {}
    state.subway_feed_health = None
    state.subway_static_status = "ready"
    state.subway_stops = SUBWAY_STOPS
    state.subway_arrivals = {}
    state.subway_arrivals_by_system = {}
    state.subway_positions = {}


async def served(*paths: str) -> dict[str, dict]:
    """GET each path from the real app at the current clock, as served JSON."""
    out = {}
    transport = httpx.ASGITransport(app=APP)
    async with httpx.AsyncClient(transport=transport, base_url="http://a10.local") as http:
        for path in paths:
            response = await http.get(path)
            if response.status_code != 200:
                FAILURES.append(f"GET {path} answered {response.status_code}: {response.text}")
                out[path] = {}
            else:
                out[path] = response.json()
    return out


async def poll_railroads(at: float, lirr: bytes | int, mnr: bytes | int) -> dict:
    """One real railroad refresh at clock `at`, then what the app serves."""
    CLOCK[0] = at
    await pollers._refresh_railroads(APP, FakeUpstream({LIRR_URL: lirr, MNR_URL: mnr}))
    got = await served("/api/railroads", "/api/status", f"/api/railroad-arrivals/LIRR/{BOARD_STOP}")
    body = got["/api/railroads"]
    status = got["/api/status"]
    return {
        "lirr": (body.get("systems") or {}).get("LIRR"),
        "mnr": (body.get("systems") or {}).get("MNR"),
        "envelope_feed_timestamp": body.get("feed_timestamp"),
        "lirr_rows": sum(1 for t in body.get("data") or [] if t["system"] == "LIRR"),
        "board_feed_timestamp": got[f"/api/railroad-arrivals/LIRR/{BOARD_STOP}"].get(
            "feed_timestamp"
        ),
        "status_feed_age_s": ((status.get("feeds") or {}).get("railroads") or {}).get(
            "feed_age_s"
        ),
        "status_health": status.get("railroad_feeds"),
    }


async def poll_subways(at: float, bodies: dict[str, bytes | int]) -> dict:
    """One real subway refresh at clock `at`, then what the app serves."""
    CLOCK[0] = at
    upstream = FakeUpstream({feeds.SUBWAY_FEED_URLS[g]: body for g, body in bodies.items()})
    await pollers._refresh_subways(APP, upstream)
    got = await served("/api/subways", "/api/status")
    body = got["/api/subways"]
    status = got["/api/status"]
    return {
        "blocks": body.get("systems") or {},
        "subject": (body.get("systems") or {}).get(SUBJECT_GROUP),
        "envelope_feed_timestamp": body.get("feed_timestamp"),
        "status_feed_age_s": ((status.get("feeds") or {}).get("subways") or {}).get(
            "feed_age_s"
        ),
        "status_health": status.get("subway_feeds"),
    }


def clocks(block: dict | None) -> tuple:
    """The three fields the row is about, from one served block."""
    block = block or {}
    return (block.get("ok"), block.get("fetched_at"), block.get("feed_timestamp"))


def implied_age(block: dict | None) -> float | None:
    """fetched_at minus feed_timestamp off one served block: design 3.5's per-system
    _feed_age. None when the block publishes no content clock."""
    block = block or {}
    if block.get("fetched_at") is None or block.get("feed_timestamp") is None:
        return None
    return block["fetched_at"] - block["feed_timestamp"]


def show(label: str, block: dict | None) -> None:
    print(f"    {label:<34} {json.dumps(block, sort_keys=True)}")


# ---------------------------------------------------------------------------
# A. what the fetchers return
# ---------------------------------------------------------------------------


async def section_a() -> dict:
    rule("A. What each public fetcher returns for one successful headerless generation")
    CLOCK[0] = T_HEADERLESS
    stops = {"LIRR": LIRR_STOPS, "MNR": MNR_STOPS}

    _t, _a, rr_ts, rr_failed, rr_map, _s = await feeds.fetch_railroad_trains(
        FakeUpstream({LIRR_URL: headerless(), MNR_URL: quiet_feed(T_HEADERLESS)}), stops
    )
    _t, _a, _ts, _f, stamped_map, _s = await feeds.fetch_railroad_trains(
        FakeUpstream({LIRR_URL: quiet_feed(T_STAMPED), MNR_URL: quiet_feed(T_HEADERLESS)}), stops
    )
    _t, _a, sw_ts, sw_failed, _tg, _ag, sw_map = await feeds.fetch_subway_trains(
        SUBWAY_STOPS,
        FakeUpstream({url: headerless() for url in feeds.SUBWAY_FEED_URLS.values()}),
    )

    print("  feeds.fetch_railroad_trains, LIRR header WITHOUT a timestamp, MNR stamped:")
    print(f"    failed systems            : {rr_failed}")
    print(f"    content-clock map         : {rr_map}")
    print(f"    envelope feed_timestamp   : {rr_ts}")
    print("  the same, LIRR header stamped 900:")
    print(f"    content-clock map         : {stamped_map}")
    print("  feeds.fetch_subway_trains, every group's header WITHOUT a timestamp:")
    print(f"    failed groups             : {sw_failed}")
    print(f"    content-clock map         : {sw_map}")
    print(f"    envelope feed_timestamp   : {sw_ts}")
    print("  The maps are the ledger's mechanism (audit: \"omits the system key when the")
    print("  timestamp is null\") and are PRINTED, NOT CHECKED: where an undated generation's")
    print("  key is dropped is location, and a refactor that moves the filter to the call site")
    print("  serves the same blocks. B to D check what is served.")

    check("railroad: the headerless LIRR generation decoded (no failed system)", rr_failed, [])
    check("subway: every headerless group decoded", sw_failed, [])
    return {"rr_map": rr_map, "sw_map": sw_map}


# ---------------------------------------------------------------------------
# B. the audit's sequence on the railroad
# ---------------------------------------------------------------------------


async def section_b() -> dict:
    rule("B. The railroad: timestamped 900, headerless 1000, failed 1100 (the audit's sequence)")
    prime()
    at_900 = await poll_railroads(T_STAMPED, quiet_feed(T_STAMPED), quiet_feed(T_STAMPED))
    at_1000 = await poll_railroads(T_HEADERLESS, headerless(), quiet_feed(T_HEADERLESS))
    at_1100 = await poll_railroads(T_FAILED, FAILED, quiet_feed(T_FAILED))
    prime()
    fail_900 = await poll_railroads(T_STAMPED, quiet_feed(T_STAMPED), quiet_feed(T_STAMPED))
    fail_1000 = await poll_railroads(T_HEADERLESS, FAILED, quiet_feed(T_HEADERLESS))

    print("  GET /api/railroads systems.LIRR, as served:")
    show("900  timestamped (header 900)", at_900["lirr"])
    show("1000 headerless, decoded", at_1000["lirr"])
    show("1100 failed (HTTP 503)", at_1100["lirr"])
    print(f"  the 1000 generation's own trip_update is dated {T_OWN:.0f}; its response's"
          f" Last-Modified is {T_HEADERLESS:.0f}")
    print("  the failure-only control, a fresh world:")
    show("900  timestamped (header 900)", fail_900["lirr"])
    show("1000 failed (HTTP 503)", fail_1000["lirr"])
    print()
    print(f"  at 1000: /api/status railroad_feeds   {at_1000['status_health']}")
    print(f"           LIRR board {BOARD_STOP:<4} feed_timestamp {at_1000['board_feed_timestamp']}")
    print(f"           MNR block                    {json.dumps(at_1000['mnr'], sort_keys=True)}")
    print(f"           envelope feed_timestamp      {at_1000['envelope_feed_timestamp']}"
          "   (beyond the audit's citation)")
    print(f"           /api/status feed_age_s       {at_1000['status_feed_age_s']}")

    check("900: the timestamped generation, (ok, fetched_at, feed_timestamp)",
          clocks(at_900["lirr"]), (True, T_STAMPED, T_STAMPED))
    check("1000: the headerless SUCCESS is ok and freshly fetched, yet its content clock is 900",
          clocks(at_1000["lirr"]), (True, T_HEADERLESS, T_STAMPED),
          "successful generation at poll time 1000 retaining content time 900")
    check("1000: the age that block implies (fetched_at - feed_timestamp), for a generation"
          " whose header carried no clock",
          implied_age(at_1000["lirr"]), 100.0)
    check(f"1000: that content clock is older than the generation's own trip_update"
          f" ({T_OWN:.0f}) by",
          None if clocks(at_1000["lirr"])[2] is None else T_OWN - clocks(at_1000["lirr"])[2],
          T_OWN - T_STAMPED)
    check("1000: and nothing in the block marks it as carried over (retained_since)",
          (at_1000["lirr"] or {}).get("retained_since", "absent"), None)
    check("1000: and nothing operational looks wrong", at_1000["status_health"],
          {"total": 2, "ok": 2, "failed": []})
    check("1000: the LIRR station board serves the same inherited clock",
          at_1000["board_feed_timestamp"], T_STAMPED)
    check("1000: Metro-North's block is null throughout, its exclusion unchanged",
          [(b["mnr"] or {}).get("feed_timestamp") for b in (at_900, at_1000, at_1100)],
          [None, None, None])
    check("1100: a failure after the headerless success still reports content time 900,"
          " older than the generation it last decoded at 1000",
          clocks(at_1100["lirr"]), (False, T_HEADERLESS, T_STAMPED))
    check("failure-only control: timestamped 900, failed 1000, retains 900 with ok false"
          " (correct: what absence is for)",
          clocks(fail_1000["lirr"]), (False, T_STAMPED, T_STAMPED))
    check("envelope feed_timestamp at 900, 1000, 1100 (beyond the audit's citation)",
          [b["envelope_feed_timestamp"] for b in (at_900, at_1000, at_1100)],
          [T_STAMPED, T_STAMPED, T_STAMPED])
    check("/api/status feeds.railroads.feed_age_s at 1000 (beyond the audit's citation)",
          at_1000["status_feed_age_s"], 100.0)
    return {"at_1000": at_1000, "at_1100": at_1100, "fail_1000": fail_1000}


# ---------------------------------------------------------------------------
# C. the subway control
# ---------------------------------------------------------------------------


def every_group(body: bytes | int) -> dict[str, bytes | int]:
    return {group: body for group in SUBWAY_GROUPS}


async def section_c() -> dict:
    rule(f"C. THE CONTROL: the subway, same sequences, same reader (subject group {SUBJECT_GROUP})")
    prime()
    at_900 = await poll_subways(T_STAMPED, every_group(quiet_feed(T_STAMPED)))
    at_1000 = await poll_subways(T_HEADERLESS, every_group(headerless()))
    failing = every_group(headerless())
    failing[SUBJECT_GROUP] = FAILED
    at_1100 = await poll_subways(T_FAILED, failing)
    prime()
    fail_900 = await poll_subways(T_STAMPED, every_group(quiet_feed(T_STAMPED)))
    failing = every_group(quiet_feed(T_HEADERLESS))
    failing[SUBJECT_GROUP] = FAILED
    fail_1000 = await poll_subways(T_HEADERLESS, failing)

    print(f"  GET /api/subways systems.{SUBJECT_GROUP}, as served (every group gets the bytes"
          " LIRR got,")
    print(f"  own trip_update dated {T_OWN:.0f} included; {SUBJECT_GROUP} alone fails in the"
          " failure steps):")
    show("900  timestamped (header 900)", at_900["subject"])
    show("1000 headerless, decoded", at_1000["subject"])
    show("1100 failed (HTTP 503)", at_1100["subject"])
    print("  the failure-only control, a fresh world:")
    show("900  timestamped (header 900)", fail_900["subject"])
    show("1000 failed (HTTP 503)", fail_1000["subject"])
    print()
    print(f"  at 1000: /api/status subway_feeds     {at_1000['status_health']}")
    print(f"           envelope feed_timestamp      {at_1000['envelope_feed_timestamp']}")
    print(f"           /api/status feed_age_s       {at_1000['status_feed_age_s']}")

    check("900: the timestamped generation", clocks(at_900["subject"]),
          (True, T_STAMPED, T_STAMPED))
    check("1000: the headerless success publishes NO content clock",
          clocks(at_1000["subject"]), (True, T_HEADERLESS, None))
    check("1000: every one of the eight groups publishes null",
          sorted(g for g, b in at_1000["blocks"].items() if b.get("feed_timestamp") is None),
          sorted(SUBWAY_GROUPS))
    check("1000: all eight ok", at_1000["status_health"], {"total": 8, "ok": 8, "failed": []})
    check("1100: a failure after the headerless success keeps the null it last published",
          clocks(at_1100["subject"]), (False, T_HEADERLESS, None))
    check("failure-only control: timestamped 900, failed 1000, retains 900 with ok false",
          clocks(fail_1000["subject"]), (False, T_STAMPED, T_STAMPED))
    check("envelope feed_timestamp at 900, 1000, 1100", [
        b["envelope_feed_timestamp"] for b in (at_900, at_1000, at_1100)
    ], [T_STAMPED, None, None])
    check("/api/status feeds.subways.feed_age_s at 1000", at_1000["status_feed_age_s"], None)
    return {"at_1000": at_1000, "at_1100": at_1100, "fail_1000": fail_1000}


# ---------------------------------------------------------------------------
# D. the committed capture
# ---------------------------------------------------------------------------


async def section_d() -> dict:
    rule("D. The committed LIRR capture: as captured, then five successful headerless polls")
    header = float(pb.FeedMessage.FromString(LIRR_RAW).header.timestamp)
    prime()
    first = await poll_railroads(header, LIRR_RAW, MNR_RAW)
    polls = []
    own_clocks = []
    for k in range(1, HEADERLESS_POLLS + 1):
        generation = without_header_timestamp(LIRR_RAW, k * CADENCE_S)
        own_clocks.append(newest_entity_clock(generation))
        polls.append(await poll_railroads(header + k * CADENCE_S, generation, MNR_RAW))

    implied = [implied_age(p["lirr"]) for p in polls]
    served = [clocks(p["lirr"])[2] for p in polls]
    print(f"  capture header H = {header:.0f}; polled at H, then H + {CADENCE_S} s x k for"
          f" k = 1..{HEADERLESS_POLLS}, each the NEXT")
    print(f"  generation: header undated, every trip_update and vehicle clock advanced"
          f" {CADENCE_S} s x k")
    print("  ('own clock' is the newest clock the generation's entities carry)")
    print(f"  {'poll':<8}{'ok':>6}{'fetched_at - H':>16}{'own clock - H':>15}"
          f"{'feed_timestamp - H':>20}{'implied age':>13}{'LIRR rows':>11}{'status age':>12}")
    table = [("H", first, newest_entity_clock(LIRR_RAW))]
    table += [(f"H+{k * CADENCE_S}", p, c) for k, (p, c) in enumerate(zip(polls, own_clocks), 1)]
    for label, p, own in table:
        block = p["lirr"] or {}
        content = block.get("feed_timestamp")
        print(f"  {label:<8}{str(block.get('ok')):>6}"
              f"{(block.get('fetched_at') or 0.0) - header:>16.0f}"
              f"{own - header:>15.0f}"
              f"{'null' if content is None else f'{content - header:.0f}':>20}"
              f"{implied_age(block)!s:>13}"
              f"{p['lirr_rows']:>11}{p['status_feed_age_s']!s:>12}")

    check("as captured: the block carries the capture's header",
          clocks(first["lirr"]), (True, header, header))
    check("every headerless poll decoded and is ok",
          [clocks(p["lirr"])[0] for p in polls], [True] * HEADERLESS_POLLS)
    check("every headerless poll served that generation's LIRR rows (more than zero)",
          all(p["lirr_rows"] > 0 for p in polls), True)
    check("the content clock every headerless poll publishes: the captured header, each time",
          served, [header] * HEADERLESS_POLLS)
    check("older than the newest clock that generation's own entities carry, by",
          [None if t is None else own - t for own, t in zip(own_clocks, served)],
          [float(CADENCE_S * k) for k in range(1, HEADERLESS_POLLS + 1)])
    check("the age the block implies grows one interval per interval",
          implied, [float(CADENCE_S * k) for k in range(1, HEADERLESS_POLLS + 1)])
    check(f"and passes FEED_STALE_AFTER_S ({cache.FEED_STALE_AFTER_S} s) on a feed that"
          " decoded successfully every poll",
          implied[-1] is not None and implied[-1] > cache.FEED_STALE_AFTER_S, True)
    check("/api/status feeds.railroads.feed_age_s follows it (beyond the audit's citation)",
          [p["status_feed_age_s"] for p in polls],
          [float(CADENCE_S * k) for k in range(1, HEADERLESS_POLLS + 1)])
    return {"implied": implied, "header": header}


async def run() -> tuple:
    a = await section_a()
    b = await section_b()
    c = await section_c()
    d = await section_d()
    return a, b, c, d


def main_script() -> int:
    print("A10: headerless successful LIRR generation inherits an old content clock")
    print(f"repository : {REPO}")
    print(f"DATA_DIR   : {os.environ['DATA_DIR']} (temporary)")
    print("upstreams  : in-memory fakes only; the MTA realtime seams point at loopback")
    with mock.patch.object(time, "time", lambda: CLOCK[0]):
        _a, b, c, d = asyncio.run(run())

    rule("SIDE BY SIDE: one reader, two producers, the same sequence")
    rows = (
        ("1000, headerless success", b["at_1000"]["lirr"], c["at_1000"]["subject"]),
        ("1100, failed after it", b["at_1100"]["lirr"], c["at_1100"]["subject"]),
        ("1000, failed after 900", b["fail_1000"]["lirr"], c["fail_1000"]["subject"]),
    )
    print(f"  {'step':<28}{'LIRR (ok, fetched, content)':>32}"
          f"{SUBJECT_GROUP + ' (ok, fetched, content)':>32}")
    for label, lirr, group in rows:
        print(f"  {label:<28}{str(clocks(lirr)):>32}{str(clocks(group)):>32}")

    rule("HERMETIC")
    check("URLs naming an NJ Transit host requested", FORBIDDEN_URLS, [])
    check("URLs outside this world requested", UNEXPECTED_URLS, [])

    print()
    if FAILURES:
        print(f"  {len(FAILURES)} check(s) no longer match the record:")
        for line in FAILURES:
            print(f"    - {line}")
        print()
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        return 1
    print("  every check matches the record.")
    print()
    print(
        "DISPOSITION: VERIFIED  A successfully decoded headerless LIRR generation at poll "
        "1000 whose own trip_update is dated 990 is served ok true, fetched_at 1000, "
        "feed_timestamp 900 (audit: 900), while the subway, given the same bytes through the "
        "same reader, publishes null and a real failure retains 900 correctly on both. On the "
        f"committed capture, five headerless next generations whose own clocks advance "
        f"{CADENCE_S} s each are all served the captured header, and the implied age reaches "
        f"{d['implied'][-1]:.0f} s."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_script())
