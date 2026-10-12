#!/usr/bin/env python3
"""A03 (Medium) "Empty arrival boards can hide a failed or stale feed" : reproduction.

THE AUDIT'S CLAIM (the 2026-10-12 report, finding A03), verbatim:

    Failure. Subway station health includes only feeds that still have arrivals
    indexed at that station. When a failed feed's last retained prediction expires,
    its station key disappears and the endpoint falls back to the healthy aggregate
    poll time with systems: null. Separately, frontend board freshness uses poll time
    without preserving availability and provider-content age. Fixing only the backend
    therefore leaves stale-content and never-decoded empty-board cases.

    Evidence. With failed ACE and healthy G, expiring A01's final arrival produced
    empty directions, a current aggregate fetched_at, and no contributor health,
    despite ACE remaining failed. Production frontend helpers also returned no warning
    for an empty board whose content was old but recently fetched, and for a
    never-decoded failed contributor. Both panel and popup can consequently say
    "No trains" without explaining unavailable data.

    Repair. Maintain station-to-feed coverage independently of displayed predictions;
    preserve expected failed contributors after expiry or retention limits. Carry
    availability, unknown clocks and source-content age through the board renderer.
    Regression cases must distinguish genuinely fresh empty service from stale, failed
    and unknown empty service in both surfaces.

    Cited (where to look, never asserted here): backend/cache.py:441,
    backend/feeds/shared.py:519, backend/routes/subway.py:108, frontend/helpers.js:2395,
    frontend/helpers.js:2462, frontend/stations.js:701.

RUN IT (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a03_empty_board_hides_failed_feed.py

THE WORLD IS F03'S, READ THROUGH ITS OWN MODULE RATHER THAN COPIED OR REBUILT.
backend/tests/test_f03_boards.py supplies the committed capture, its stops, the frozen
clock (FROZEN_S of tests/e2e/fixtures/api.js), the station (219, Prospect Av), the
builders (_restamp, _quiet_feed, _Upstream), the poll (_poll_and_serve) and the app.state
a lifespan would have built: that last comes from the acceptance test's own fixture
(f03_world, unwrapped and handed a pytest.MonkeyPatch, which also restores every attribute
it set). So the world is built by whatever production helpers the acceptance test uses,
and a rename of one of them, which must update that test, cannot break this script.
219's platforms are read from the stops data (a platform id is its station's id plus N or
S); a check below confirms the decoder places exactly those calls on the board. The
audit used failed ACE and healthy G at A01; this world's pair is FAILED 1-7+S, the
capture's own group and the one that reaches 219, with G and the six other groups
healthy. A01 is not in this world's stops; 219 is the station the world is built around.

WHAT IS INJECTED (the faults, stated plainly):

  * HTTP 503 on the 1-7+S URL, from a stub client that returns real httpx.Response
    objects, so raise_for_status fails the group the way production fails it. Polls
    run every POLL_S (20 s, the production default cadence).
  * THE LAST TRAINS AT 219. The capture's trips call at 219 up to 97 minutes ahead of
    its header (printed below), and the retention cap empties a failed group's rows
    long before the last of them could expire. So the audit's road, a retained
    prediction EXPIRING, needs a station whose last call falls inside the retention
    window. The world makes one: the capture re-stamped 5 s behind the clock, with
    every trip that calls at 219 more than LAST_CALL_S ahead removed (its vehicle
    entity too). Nothing else in the feed is touched. Production defaults are kept; no
    env seam is moved, and the window is MEASURED by (b) rather than read.
  * (b) runs the cap's road over the UNTRIMMED capture, because the audit's repair
    names both exits ("after expiry or retention limits").
  * Frontend arm, stale content: the same trimmed trips re-stamped 600 s behind the
    clock (F03's "header ten minutes old") and served identically on every poll, so
    every call at 219 is past the decoder's 60 s grace and the board is empty while
    the poll is current. Never decoded: 1-7+S answers 503 from the first poll.
  * THE FRONTEND BOARDS. The served board never carries an empty board's contributor
    (that is the backend half), so the frontend half is tested on the shape a backend
    repair would serve: the REAL served empty board with its `systems` filled by the
    group's REAL block from the same poll's /api/subways, and its two clocks set the
    way the endpoint's own selectors date one contributor (fetched_at its block's, or
    the aggregate when the block has none; feed_timestamp its block's). Each one is
    validated, round trip, by the endpoint's response model, which is found through the
    app's own served /openapi.json rather than by name.
  * app.state is the f03 fixture's, plus the station-route index for 219, taken from
    the routes the capture's trips call there, so /api/subway-stops serves 219 with its
    routes and a repair that reads static station-to-route coverage has something to
    read.
  * THE PAGE'S ALERT STORE is empty and current: the world primes no alert index, so
    /api/alerts is answered with an empty alert list dated at the poll, in the shape the
    /api/alerts response model accepts. This measures the board, not the alert join
    (f03's choice).

WHAT IS PRODUCTION CODE HERE (nothing below is re-implemented): main.fetch_subway_trains
(the real decoder and fan-out) and pollers._refresh_subways (merge, retention, expiry,
per-group health), driven through the world's _poll_and_serve; main.app over
httpx.ASGITransport for GET /api/subway-arrivals/219, /api/subways and /api/status, so
every backend number is read out of SERVED JSON and no private attribute is read.

THE FRONTEND IS THE WHOLE PAGE, booted fresh for each board in node:vm: every script
index.html loads, in index.html's order (helpers, systems/shared.js, every system file,
stations.js, map.js), with only Leaflet, the DOM and fetch stubbed. The stub fetch
answers every path the page asks for with what main.app SERVES in that world at that
poll (each parameterless GET path in the app's /openapi.json, read through ASGI, and the
station's board), so the page's own boot fills the station registry from the served
/api/subway-stops and ingests the served /api/subways through map.js's own source loop.
The POPUP is opened the way a click opens it: the marker the page drew at the station's
served coordinates fires the popupopen handler the page's own loader bound to it, which
runs the station descriptor's real render through the real popup fetch. The PANEL is
opened the way a rider selects a station: the A1 panel's selectStation, for the registry
entry the page built around that same marker, which fetches through the panel's own
path. Then every live interval the page set (its poll, its ticks) runs once more, and
both surfaces are read whole: the popup's last content (its HTML), #stations-detail
(every descendant, with its tag, classes, attributes and text) and the spoken
#stations-announce. So a repair placed in the renderers, in the fetch path, in the
popup's descriptor, in the page's subway source, in markup alone, or in anything else
the page reads from the backend, changes what is measured.

WHAT THE SCRIPT NAMES, and nothing else. It drives the f03 world's own module (its
fixture, builders, constants and poll, as the spec asks), the public HTTP paths, the
app's /openapi.json (which names the response models), Leaflet's marker API, the A1
panel's selectStation and stationRegistry, and the panel's two element ids. It imports
no private production helper and calls no renderer by name, so renaming either, which
must update the acceptance test that builds the world, leaves this script green. A
harness that went blind (a renamed element id, a page that rendered nothing) cannot
pass quietly: the baseline check requires "No trains" on both surfaces and the control
requires a visible difference on both.

"NO WARNING" IS MEASURED AGAINST GENUINELY FRESH EMPTY SERVICE, not against a class
name: a fifth world serves every group a fresh, valid, empty feed, and its board is
rendered the same way. A board "says nothing" when BOTH surfaces (the popup, the panel
and its spoken sentence, markup included) are identical to that fresh board's, so any
repair that adds words, or anything else, for the stale, failed or unknown cases
changes the measurement, which is the regression distinction the audit's repair asks
for. The backend half is measured the same way: the served board after expiry, at the
cap, and in the stale-content and never-decoded worlds, with every clock in it taken
relative to the instant it was served, is compared WHOLE to fresh empty service's
served board, so a repair that carries the contributor in any field of the board
changes the measurement. (A repair that publishes it only on another endpoint leaves
the board as the audit describes it; [rider] sees it the moment the page reads it.) A
control proves the page can show a warning: an empty board whose contributor was last
polled 200 s ago does read differently.

HERMETIC: _hermetic.contain() before the first backend import and verify() after it;
DATA_DIR points at a fresh temporary directory (removed at exit) and data/ is checked
unchanged at the end; the stub client hard-fails on any URL naming njtransit or
raildata; the page's fetch reaches nothing but the served snapshot. No network, no
credential, no mint. The real fetch logs one warning per failed poll; logging at WARNING
and below is disabled because the script reads the same failure off /api/status instead.

RECORDED DISPOSITION: VERIFIED. Exit 0 while the finding behaves as recorded; exit 1
with the failed checks named the moment it stops, including a read that is not served
(HTTP 200), a page that does not boot, and an import that fails. Each check is tagged
[backend], [frontend], [rider] or [world]. [rider] checks are end to end: the board the
backend served, rendered by the page, so a repair of either half that reaches the rider
turns them red, while a [backend] or [frontend] check moves only with its own half.
"""

from __future__ import annotations

import asyncio
import atexit
import inspect
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"

# Before any backend import: static_data resolves DATA_DIR at import time.
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="a03-data-")
os.environ["DATA_DIR"] = _TMP_DATA_DIR
atexit.register(shutil.rmtree, _TMP_DATA_DIR, True)

sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain()
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))
_IMPORT_FAILURE = None
try:
    import httpx  # noqa: E402
    import main  # noqa: E402
    import models  # noqa: E402
    import pytest  # noqa: E402
    import test_f03_boards as world  # noqa: E402  (the acceptance test's world, not a copy)
    from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402
except Exception as exc:  # reported as a named check below, with the DISPOSITION line
    _IMPORT_FAILURE = f"{type(exc).__name__}: {exc}"

try:
    _hermetic.verify()
except (Exception, SystemExit) as exc:  # uncontained: refuse to run, in the record's form
    print(f"  [FAIL] [world] the run is contained  ({type(exc).__name__}: {exc})")
    print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
    raise SystemExit(1)
logging.disable(logging.WARNING)

if _IMPORT_FAILURE is not None:
    print(f"  [FAIL] [world] the backend and the f03 world import  ({_IMPORT_FAILURE})")
    print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
    raise SystemExit(1)

STATION = world.STATION_ID  # "219", Prospect Av
FAILED = "1-7+S"  # the capture's own group, the one that reaches 219
HEALTHY = "G"  # the audit's healthy group; the six others are healthy too
FEED_URLS = world.SUBWAY_FEED_URLS  # the eight group URLs, as the world addresses them
T0 = world.NOW  # FROZEN_S of tests/e2e/fixtures/api.js
FRESH_LAG_S = world.HEALTHY_LAG_S  # 5 s, api.js's "fresh"
STALE_LAG_S = world.AGED_LAG_S  # 600 s, the F03 audit's "header ten minutes old"
LAST_CALL_S = 200.0  # trips calling at 219 later than T0 + this are removed
POLL_S = 20.0  # the production default cadence (pollers.POLL_INTERVAL_S), a world choice
MAX_POLLS = 720  # four hours of polls: the bound on any "poll until the board empties"
BOARD_PATH = f"/api/subway-arrivals/{STATION}"

failures: list[str] = []
BREACHES: list[str] = []


class Halt(Exception):
    """A read the rest of the run depends on did not happen; already a failed check."""


def check(ok: bool, arm: str, label: str, detail: str = "") -> bool:
    """A named assertion, tagged with the half of the finding it records."""
    tag = f"[{arm}] {label}"
    if not ok:
        failures.append(f"{tag}{(': ' + detail) if detail else ''}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {tag}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


def halt(arm: str, label: str, detail: str) -> None:
    check(False, arm, label, detail)
    raise Halt


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


# ---------------------------------------------------------------------------
# the world
# ---------------------------------------------------------------------------

STOPS = json.loads(world._STOPS.read_text())
# A platform id is its parent station's id plus N or S (the world's stops data, and
# the GTFS convention test_f03_boards states). The first failed poll's check confirms
# the decoder places exactly these platforms' calls on the board.
PLATFORMS = {stop for stop in STOPS if stop[:-1] == STATION and stop[-1:] in ("N", "S")}


def _station_calls(trip_update) -> list[float]:
    """Every instant one trip calls at 219, the later of arrival and departure."""
    calls = []
    for stu in trip_update.stop_time_update:
        if stu.stop_id not in PLATFORMS:
            continue
        times = [
            getattr(stu, field).time
            for field in ("arrival", "departure")
            if stu.HasField(field) and getattr(stu, field).time
        ]
        if times:
            calls.append(float(max(times)))
    return calls


def _parse(raw: bytes) -> pb.FeedMessage:
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    return feed


def last_trains(raw: bytes, cutoff: float) -> bytes:
    """The capture with every trip calling at 219 after `cutoff` removed, with its
    vehicle entity. Header, every other trip and every other station are untouched."""
    feed = _parse(raw)
    dropped: set[str] = set()
    keep = []
    for entity in feed.entity:
        if entity.HasField("trip_update") and any(
            t > cutoff for t in _station_calls(entity.trip_update)
        ):
            if entity.trip_update.trip.trip_id:
                dropped.add(entity.trip_update.trip.trip_id)
            continue
        keep.append(entity)
    out = pb.FeedMessage()
    out.header.CopyFrom(feed.header)
    for entity in keep:
        if entity.HasField("vehicle") and entity.vehicle.trip.trip_id in dropped:
            continue
        out.entity.add().CopyFrom(entity)
    return out.SerializeToString()


def station_calls_in(raw: bytes) -> list[float]:
    feed = _parse(raw)
    return sorted(
        t for e in feed.entity if e.HasField("trip_update") for t in _station_calls(e.trip_update)
    )


def station_routes_in(raw: bytes) -> list[str]:
    feed = _parse(raw)
    return sorted(
        {
            e.trip_update.trip.route_id
            for e in feed.entity
            if e.HasField("trip_update")
            and e.trip_update.trip.route_id
            and _station_calls(e.trip_update)
        }
    )


class Upstream(world._Upstream):
    """world._Upstream (real httpx.Response objects per URL), with named groups
    answering HTTP 503, and a hard failure on any NJ Transit host."""

    def __init__(self, bodies: dict[str, bytes], failing: tuple[str, ...] = ()) -> None:
        super().__init__(bodies)
        self.failing = {FEED_URLS[group] for group in failing}

    async def get(self, url: str) -> httpx.Response:
        if "njtransit" in url.lower() or "raildata" in url.lower():
            BREACHES.append(url)
            raise SystemExit(f"containment breach: the stub client was asked for {url}")
        if url in self.failing:
            return httpx.Response(503, content=b"unavailable", request=httpx.Request("GET", url))
        return await super().get(url)


def upstream(at: float, failed_body: bytes | None, failing: bool) -> Upstream:
    """One poll's upstream: every group a fresh, valid, empty feed stamped `at`, and
    1-7+S either `failed_body` or a 503."""
    bodies = {url: world._quiet_feed(at) for url in FEED_URLS.values()}
    if failed_body is not None:
        bodies[FEED_URLS[FAILED]] = failed_body
    return Upstream(bodies, (FAILED,) if failing else ())


def rows(board: dict) -> list[dict]:
    return [row for bucket in board["directions"].values() for row in bucket]


def keys(board: dict) -> list[str]:
    return sorted(board["systems"] or {})


def asgi() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=main.app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://a03")


class Contract:
    """What the app says it serves, from its own /openapi.json: every parameterless GET
    path (what the page may ask for) and the models two of them are served with, found
    by the schema the app names rather than by a name this script holds."""

    paths: list[str] = []
    board_model = None
    alerts_model = None

    @classmethod
    def load(cls) -> None:
        async def fetch() -> httpx.Response:
            async with asgi() as http:
                return await http.get("/openapi.json")

        resp = asyncio.run(fetch())
        if resp.status_code != 200:
            halt("world", "the app serves its /openapi.json", f"HTTP {resp.status_code}")
        spec = resp.json()["paths"]
        cls.paths = sorted(p for p, ops in spec.items() if "get" in ops and "{" not in p)

        def model(path: str):
            ok = spec[path]["get"]["responses"]["200"]["content"]["application/json"]
            return getattr(models, ok["schema"]["$ref"].rsplit("/", 1)[-1])

        cls.board_model = model("/api/subway-arrivals/{station_id}")
        cls.alerts_model = model("/api/alerts")


def body_of(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return resp.text


async def serve(at: float, page: bool) -> dict:
    """The three served reads every check uses and, with `page`, everything the page
    may fetch, as main.app serves it now."""
    async with asgi() as http:
        reads = {
            "board": await http.get(BOARD_PATH),
            "vehicles": await http.get("/api/subways"),
            "status": await http.get("/api/status"),
        }
        snap = {"at": at}
        for name, resp in reads.items():
            if resp.status_code != 200:
                halt(
                    "backend",
                    f"{resp.request.url.path} is served (HTTP 200) at T0{at - T0:+.0f} s",
                    f"HTTP {resp.status_code}: {resp.text[:300]}",
                )
            snap[name] = resp.json()
        if page:
            served = {}
            for path in Contract.paths:
                resp = await http.get(path)
                served[path] = {"status": resp.status_code, "body": body_of(resp)}
            served[BOARD_PATH] = {"status": 200, "body": snap["board"]}
            served["/api/alerts"] = {"status": 200, "body": empty_alerts(at)}
            snap["page"] = served
    return snap


def empty_alerts(at: float) -> dict:
    """An empty, current alert store, in the shape the /api/alerts model accepts."""
    body = {"fetched_at": at, "feed_timestamp": at, "served_at": at, "alerts": [], "systems": None}
    return Contract.alerts_model.model_validate(body).model_dump(mode="json")


def run_world(schedule, station_routes: dict, until_empty: bool = False) -> list[dict]:
    """Real polls at the scheduled (instant, 1-7+S body, 1-7+S failing) entries, each
    followed by the served reads. The page's snapshot is kept for the last two polls
    only. With until_empty, stops at the first poll after the first whose board at 219
    is empty."""
    snaps: list[dict] = []
    with pytest.MonkeyPatch.context() as mp:
        # The acceptance test's own fixture primes app.state and patches the clock, and
        # hands the clock back; the MonkeyPatch restores everything it set.
        clock = inspect.unwrap(world.f03_world)(mp)
        mp.setattr(main.app.state, "subway_station_routes", station_routes, raising=False)

        async def go() -> None:
            for at, body, failing in schedule:
                clock[0] = at
                try:
                    await world._poll_and_serve(upstream(at, body, failing))
                except AssertionError:
                    pass  # a read that is not HTTP 200: serve() re-reads it and names it
                except Exception as exc:
                    halt(
                        "backend",
                        f"the poll at T0{at - T0:+.0f} s and its reads complete",
                        f"{type(exc).__name__}: {exc}",
                    )
                snaps.append(await serve(at, page=True))
                if len(snaps) > 2:
                    snaps[-3].pop("page", None)
                if until_empty and len(snaps) > 1 and not rows(snaps[-1]["board"]):
                    break

        asyncio.run(go())
    if BREACHES:
        halt("world", "the stub client is asked for no NJ Transit host", f"{BREACHES}")
    return snaps


def outage(first_body: bytes) -> list[tuple[float, bytes | None, bool]]:
    """One healthy poll at T0, then 1-7+S failing on every poll for up to MAX_POLLS."""
    return [(T0, first_body, False)] + [
        (T0 + k * POLL_S, None, True) for k in range(1, MAX_POLLS + 1)
    ]


def with_contributor(snap: dict) -> tuple[dict, bool]:
    """The served board with 1-7+S's real block from the same poll, dated the way the
    endpoint's selectors date one contributor, and whether the endpoint's response
    model reproduces it exactly."""
    board = json.loads(json.dumps(snap["board"]))
    block = snap["vehicles"]["systems"][FAILED]
    board["systems"] = {FAILED: block}
    if block["fetched_at"] is not None:
        board["fetched_at"] = block["fetched_at"]
    board["feed_timestamp"] = block["feed_timestamp"]
    round_trip = Contract.board_model.model_validate(board).model_dump(mode="json")
    return board, round_trip == board


def relative(value, at: float):
    """A served body with every epoch-scale number taken relative to `at`, the instant
    it was served, so two boards served at different instants compare on content."""
    if isinstance(value, dict):
        return {k: relative(v, at) for k, v in value.items()}
    if isinstance(value, list):
        return [relative(v, at) for v in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) >= 1e9:
        return value - at
    return value


def differences(a, b, where: str = "") -> list[str]:
    """Where two JSON values differ, as paths."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{where}.{k} (only in {'one' if k in a else 'the other'})")
            else:
                out.extend(differences(a[k], b[k], f"{where}.{k}"))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in differences(x, y, f"{where}[{i}]")]
    return [] if a == b else [where or "(the whole body)"]


def data_snapshot() -> list[tuple]:
    root = REPO / "data"
    if not root.exists():
        return []
    return sorted(
        (str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    )


# ---------------------------------------------------------------------------
# the frontend: the whole page, executed rather than read
# ---------------------------------------------------------------------------

PAGE_DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const frontend = process.argv[2];
const input = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

// index.html's own script list, in its own order; the vendored Leaflet is a stub.
const html = fs.readFileSync(path.join(frontend, "index.html"), "utf8");
const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)]
  .map((m) => m[1]).filter((src) => !src.startsWith("vendor/"));
const sources = scripts.map((rel) => [rel, fs.readFileSync(path.join(frontend, rel), "utf8")]);

const unescape = (s) => s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
  .replace(/&#39;/g, "'").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&");
const htmlLines = (h) => String(h).replace(/<[^>]*>/g, "\n").split("\n")
  .map((s) => unescape(s.trim())).filter(Boolean);

// A DOM element whose textContent is its own text plus every descendant's, and whose
// textContent and innerHTML setters replace its children, as a browser's do.
function makeDocument() {
  function makeEl(tag) {
    const classes = new Set();
    return {
      tagName: String(tag).toLowerCase(), id: "", hidden: false, value: "", checked: false,
      children: [], dataset: {}, attributes: {}, parentElement: null, _text: "", _html: null,
      style: { setProperty() {}, removeProperty() {}, getPropertyValue() { return ""; } },
      classList: {
        add: (...c) => c.forEach((x) => classes.add(x)),
        remove: (...c) => c.forEach((x) => classes.delete(x)),
        toggle: (c, on) => {
          const want = on === undefined ? !classes.has(c) : !!on;
          if (want) classes.add(c); else classes.delete(c);
          return want;
        },
        contains: (c) => classes.has(c),
      },
      get className() { return [...classes].join(" "); },
      set className(v) {
        classes.clear();
        String(v).split(/\s+/).filter(Boolean).forEach((c) => classes.add(c));
      },
      get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
      set textContent(v) {
        this._text = v == null ? "" : String(v);
        this._html = null;
        this.children = [];
      },
      get innerHTML() { return this._html ?? ""; },
      set innerHTML(v) {
        this._html = String(v);
        this._text = htmlLines(v).join(" ");
        this.children = [];
      },
      append(...n) { for (const c of n) this.appendChild(node(c)); },
      prepend(...n) {
        for (const c of n.reverse()) {
          const k = node(c);
          this.children.unshift(k);
          k.parentElement = this;
        }
      },
      appendChild(n) {
        this.children.push(n);
        if (n && typeof n === "object") n.parentElement = this;
        return n;
      },
      insertBefore(n, ref) {
        const i = this.children.indexOf(ref);
        if (i < 0) return this.appendChild(n);
        this.children.splice(i, 0, n);
        n.parentElement = this;
        return n;
      },
      removeChild(n) { this.children = this.children.filter((c) => c !== n); return n; },
      replaceChildren(...n) {
        this._text = "";
        this._html = null;
        this.children = [];
        this.append(...n);
      },
      replaceWith() {},
      remove() { if (this.parentElement) this.parentElement.removeChild(this); },
      contains(n) {
        for (let x = n; x; x = x.parentElement) if (x === this) return true;
        return false;
      },
      addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
      setAttribute(k, v) { this.attributes[k] = String(v); },
      getAttribute(k) { return this.attributes[k] ?? null; },
      removeAttribute(k) { delete this.attributes[k]; },
      hasAttribute(k) { return k in this.attributes; },
      toggleAttribute(k, on) { if (on) this.attributes[k] = ""; else delete this.attributes[k]; },
      focus() { doc.activeElement = this; }, blur() {}, click() {}, scrollIntoView() {},
      closest() { return null; }, matches() { return false; },
      querySelector() { return makeEl("div"); }, querySelectorAll() { return []; },
      getBoundingClientRect() {
        return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
      },
    };
  }
  function textNode(t) { const n = makeEl("#text"); n.textContent = t; return n; }
  const node = (c) => (typeof c === "string" ? textNode(c) : c);
  const byId = new Map();
  const doc = {
    getElementById(id) {
      if (!byId.has(id)) { const el = makeEl("div"); el.id = id; byId.set(id, el); }
      return byId.get(id);
    },
    createElement: makeEl, createTextNode: textNode,
    createDocumentFragment: () => makeEl("#fragment"),
    body: makeEl("body"), documentElement: makeEl("html"), head: makeEl("head"),
    activeElement: null, hidden: false, visibilityState: "visible",
    addEventListener() {}, removeEventListener() {},
    querySelector() { return makeEl("div"); }, querySelectorAll() { return []; },
  };
  return doc;
}

// Leaflet: every factory on L makes a new layer (recorded with the factory's name and its
// arguments), every method on a layer is fluent, and a layer records its event handlers,
// so the popupopen handler the page's own loader bound to a station's marker is what
// opens that station's popup here.
function makeLeaflet(created) {
  function layer(kind, args) {
    const own = { kind, args, handlers: {}, contents: [], open: false, props: {} };
    const self = new Proxy(function () {}, {
      get(_t, p) {
        if (typeof p === "symbol" || p === "then") return undefined;
        if (p === "__own") return own;
        if (p in own.props) return own.props[p];
        if (p === "toString") return () => "";
        if (p === "valueOf") return () => 0;
        if (p === "on" || p === "once") return (evt, fn) => {
          if (typeof evt !== "string") return self;
          for (const e of evt.split(/\s+/)) (own.handlers[e] ??= []).push(fn);
          return self;
        };
        if (p === "setPopupContent") return (h) => { own.contents.push(String(h)); return self; };
        if (p === "isPopupOpen") return () => own.open;
        if (p === "openPopup") return () => {
          own.open = true;
          for (const fn of own.handlers.popupopen ?? []) fn.call(self, { target: self });
          return self;
        };
        if (p === "hasLayer") return () => true;
        return function () { return self; };
      },
      set(_t, p, v) { own.props[p] = v; return true; },
      apply() { return self; },
      construct() { return layer(kind, []); },
    });
    created.push(self);
    return self;
  }
  const plain = (p) => typeof p !== "symbol" && p !== "then";
  const factory = (name) => new Proxy(function () {}, {
    get(_u, q) { return plain(q) ? factory(`${name}.${String(q)}`) : undefined; },
    apply(_u, _this, args) { return layer(name, args); },
    construct(_u, args) { return layer(name, args); },
  });
  return new Proxy({}, { get(_t, p) { return plain(p) ? factory(String(p)) : undefined; } });
}

// The DOM a rider's assistive technology and eyes both read: every element with its tag,
// classes, attributes, hidden flag, inline style and text, children in order.
function tree(el) {
  if (!el || typeof el !== "object") return String(el ?? "");
  const attrs = Object.entries(el.attributes || {}).map(([k, v]) => ` ${k}="${v}"`).join("");
  const style = Object.entries(el.style || {}).filter(([, v]) => typeof v !== "function")
    .map(([k, v]) => `${k}:${v}`).join(";");
  return `<${el.tagName}${el.className ? ` class="${el.className}"` : ""}${attrs}` +
    `${el.hidden ? " hidden" : ""}${style ? ` style="${style}"` : ""}>` +
    `${el._text ?? ""}${(el.children || []).map(tree).join("")}</${el.tagName}>`;
}

const settle = async () => {
  for (let i = 0; i < 25; i += 1) await new Promise((r) => setImmediate(r));
};

async function page(item) {
  const doc = makeDocument();
  const intervals = new Map();
  let timerId = 0;
  const requested = new Set();
  const created = [];
  const respond = (status, body) => {
    const text = typeof body === "string" ? body : JSON.stringify(body);
    return {
      ok: status >= 200 && status < 300, status,
      json: async () => JSON.parse(text),
      text: async () => text,
    };
  };
  const sandbox = {
    console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
    URL, URLSearchParams, TextEncoder, TextDecoder, structuredClone, queueMicrotask,
    document: doc, L: makeLeaflet(created),
    location: { search: "", hash: "", href: "http://page/", pathname: "/", origin: "http://page" },
    history: { replaceState() {}, pushState() {} },
    navigator: { userAgent: "node", language: "en-US", languages: ["en-US"] },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    fetch: async (url) => {
      const p = new URL(String(url), "http://page").pathname;
      requested.add(p);
      const hit = item.responses[p];
      return hit ? respond(hit.status, hit.body) : respond(404, { detail: "Not Found" });
    },
    AbortSignal: { timeout: () => ({}) },
    AbortController: class { constructor() { this.signal = {}; } abort() {} },
    // The page's intervals are recorded and run once, below; its timeouts never fire,
    // so a static loader that failed waits rather than retrying into the measurement.
    setInterval: (fn) => { timerId += 1; intervals.set(timerId, fn); return timerId; },
    clearInterval: (id) => { intervals.delete(id); },
    setTimeout: () => { timerId += 1; return timerId; }, clearTimeout: () => {},
    requestAnimationFrame: () => 0, cancelAnimationFrame: () => {},
    matchMedia: (q) => ({
      matches: false, media: q,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    }),
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    addEventListener() {}, removeEventListener() {},
    performance: { now: () => 0 },
    innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1,
    __nowMs: item.at * 1000,
  };
  sandbox.window = sandbox; sandbox.globalThis = sandbox; sandbox.self = sandbox;
  vm.createContext(sandbox);
  // The browser's clock, frozen at the instant the board was served.
  vm.runInContext(
    `(() => { const Real = Date; class Frozen extends Real {
       constructor(...a) { if (a.length === 0) super(globalThis.__nowMs); else super(...a); }
       static now() { return globalThis.__nowMs; } } globalThis.Date = Frozen; })();`,
    sandbox,
  );
  for (const [rel, src] of sources) vm.runInContext(src, sandbox, { filename: rel });
  await settle(); // the page's own boot: its static loaders, its first poll, its alerts
  // THE POPUP, opened the way a click opens it: the marker the page drew at the station's
  // served coordinates fires its own popupopen handler.
  const stops = item.responses["/api/subway-stops"].body || [];
  const stop = stops.find((s) => s.id === input.station_id);
  const at = (layer) => {
    const ll = layer.__own.args[0];
    return Array.isArray(ll) && ll[0] === stop.lat && ll[1] === stop.lon;
  };
  const markers = created.filter((l) => at(l) && (l.__own.handlers.popupopen ?? []).length);
  if (markers.length !== 1) {
    throw new Error(`${markers.length} popup markers at ${input.station_id}`);
  }
  const [marker] = markers;
  marker.openPopup();
  await settle();
  // THE PANEL, selected the way a rider selects a station: the A1 panel's entry point,
  // for the registry entry the page built for that same marker.
  sandbox.__marker = marker;
  vm.runInContext(
    "selectStation(stationRegistry.find((e) => e.marker === __marker).key)",
    sandbox,
  );
  await settle();
  // One more turn of the page's own clocks: every live interval (its poll, its ticks).
  for (const fn of [...intervals.values()]) fn();
  await settle();
  const contents = marker.__own.contents;
  const popupHtml = contents[contents.length - 1] ?? "";
  const detail = doc.getElementById("stations-detail");
  const panel = detail.children.flatMap((kid) => {
    if (kid.tagName === "ul") return kid.children.map((li) => `li: ${li.textContent}`);
    const cls = kid.className ? "." + kid.className.split(" ").join(".") : "";
    return [`${kid.tagName}${cls}: ${kid.textContent}`];
  });
  const announce = doc.getElementById("stations-announce");
  return {
    popup: htmlLines(popupHtml),
    panel,
    spoken: announce.textContent,
    // What is COMPARED: each surface whole, markup included.
    surfaces: { popup: popupHtml, panel: tree(detail), spoken: tree(announce) },
    requested: [...requested].sort(),
  };
}

(async () => {
  const out = [];
  for (const item of input.cases) out.push(await page(item));
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { process.stderr.write(String((e && e.stack) || e)); process.exit(1); });
"""


def render(cases: dict[str, tuple[float, dict]]) -> dict[str, dict]:
    """Each case (served instant, everything the app serves then) on a fresh page."""
    names = list(cases)
    payload = {
        "station_id": STATION,
        "cases": [{"at": cases[n][0], "responses": cases[n][1]} for n in names],
    }
    with tempfile.TemporaryDirectory() as tmp:
        driver = Path(tmp) / "a03_page.js"
        driver.write_text(PAGE_DRIVER)
        data = Path(tmp) / "cases.json"
        data.write_text(json.dumps(payload))
        proc = subprocess.run(
            ["node", str(driver), str(FRONTEND), str(data)], capture_output=True, text=True
        )
    if proc.returncode != 0:
        head = (proc.stderr or proc.stdout).strip().splitlines()[:3]
        halt("frontend", "the page boots and renders every board", " | ".join(head))
    return dict(zip(names, json.loads(proc.stdout)))


def rider_text(view: dict) -> dict:
    """Everything a rider reads, hears or is shown, per surface, markup included: the
    comparison unit. (The text alone is what show() prints.)"""
    return view["surfaces"]


def show(name: str, view: dict) -> None:
    print(f"  {name}")
    print(f"      popup : {' | '.join(view['popup'])}")
    print(f"      panel : {' | '.join(view['panel'])}")
    print(f"      spoken: {view['spoken']}")


def moved(view: dict, baseline: dict) -> list[str]:
    """The surfaces whose rider text differs from the baseline's."""
    return [k for k, v in rider_text(view).items() if v != rider_text(baseline)[k]]


def extra_lines(view: dict, baseline: dict) -> list[str]:
    """The lines a rider reads on this board that the baseline board does not carry."""
    base = set(baseline["popup"]) | set(baseline["panel"])
    return [line for line in view["popup"] + view["panel"] if line not in base]


def age(board: dict) -> float:
    return board["served_at"] - board["fetched_at"]


def page_at(snap: dict, board: dict | None = None) -> tuple[float, dict]:
    """The page's view of one poll, with the board replaced when one is given."""
    served = dict(snap["page"])
    if board is not None:
        served[BOARD_PATH] = {"status": 200, "body": board}
    return snap["at"], served


# ---------------------------------------------------------------------------


def main_script() -> None:
    data_before = data_snapshot()
    Contract.load()
    raw = world._CAPTURE.read_bytes()
    fresh_full = world._restamp(raw, T0, FRESH_LAG_S)
    fresh_last = last_trains(fresh_full, T0 + LAST_CALL_S)
    stale_last = world._restamp(fresh_last, T0, STALE_LAG_S)
    routes_219 = {STATION: station_routes_in(raw)}
    full_calls = station_calls_in(fresh_full)
    kept_calls = station_calls_in(fresh_last)
    last_call = max(kept_calls)

    # The five worlds. (b) first, because it measures the retention window (a) needs.
    capped = run_world(outage(fresh_full), routes_219, until_empty=True)
    expiry = run_world(outage(fresh_last), routes_219, until_empty=True)
    stale = run_world([(T0, stale_last, False), (T0 + POLL_S, stale_last, False)], routes_219)
    never = run_world([(T0, None, True), (T0 + POLL_S, None, True)], routes_219)
    fresh = run_world([(T0, None, False), (T0 + POLL_S, None, False)], routes_219)
    cap_before, cap_after = capped[-2], capped[-1]
    failed_from = T0 + POLL_S

    rule("SETUP: f03's world (backend/tests/test_f03_boards.py), one group failing")
    print(
        f"  capture / stops               : {world._CAPTURE.relative_to(REPO)}, {world._STOPS.name}"
    )
    print(
        f"  station                       : {STATION} (platforms {sorted(PLATFORMS)}), "
        f"static routes {routes_219[STATION]}"
    )
    print(
        f"  failed group                  : {FAILED} (HTTP 503 from T0+{POLL_S:.0f} s); healthy: "
        f"{HEALTHY} and the six others"
    )
    print(f"  clock T0                      : {T0:.0f}; a poll every {POLL_S:.0f} s")
    print(
        f"  calls at {STATION}, full capture   : {len(full_calls)}, "
        f"the last at T0{full_calls[-1] - T0:+.0f} s"
    )
    print(
        f"  calls at {STATION}, last trains    : {len(kept_calls)}, at "
        + ", ".join(f"T0{t - T0:+.0f} s" for t in kept_calls)
    )
    print(
        f"  retention window, measured (b): from T0+{failed_from - T0:.0f} s "
        f"to T0{cap_after['at'] - T0:+.0f} s ({cap_after['at'] - failed_from:.0f} s)"
    )
    check(
        rows(cap_after["board"]) == [] and full_calls[-1] > cap_after["at"],
        "world",
        f"on the untrimmed capture the cap empties {STATION} long before its last call could "
        "expire (why the expiry road needs the last trains)",
        f"emptied at T0{cap_after['at'] - T0:+.0f} s, last call T0{full_calls[-1] - T0:+.0f} s",
    )
    check(
        failed_from < last_call < cap_after["at"],
        "world",
        f"{STATION}'s last call in the last-trains feed lies after the failure begins and inside "
        "the measured retention window",
        f"last call T0{last_call - T0:+.0f} s",
    )

    # ---- (a) backend: the expiry road -------------------------------------
    rule(f"(a) BACKEND: {FAILED} fails, its last retained prediction at {STATION} expires")
    print(
        f"  {'poll':>7}  {FAILED:<6} {'rows':>4}  {'provenance':<10} {'systems keys':<14} "
        f"{'fetched_at age':>14}  feed_timestamp"
    )
    for snap in expiry:
        b = snap["board"]
        block = snap["vehicles"]["systems"][FAILED]
        prov = ",".join(sorted({r["provenance"] for r in rows(b)})) or "-"
        ft = "null" if b["feed_timestamp"] is None else f"T0{b['feed_timestamp'] - T0:+.0f}"
        health = "ok" if block["ok"] else "FAIL"
        print(
            f"  T0{snap['at'] - T0:+5.0f}  {health:<6} {len(rows(b)):>4}  {prov:<10} "
            f"{','.join(keys(b)) or 'null':<14} {age(b):>12.0f} s  {ft}"
        )
    first, failed1, before, after = expiry[0], expiry[1], expiry[-2], expiry[-1]
    ab = after["board"]
    check(
        len(rows(first["board"])) == sum(t >= T0 - 60 for t in kept_calls)
        and {r["provenance"] for r in rows(first["board"])} == {"reported"}
        and keys(first["board"]) == [FAILED],
        "backend",
        f"healthy poll: {STATION} serves {FAILED}'s last trains, and names {FAILED}",
        f"{len(rows(first['board']))} rows, systems {keys(first['board'])}",
    )
    served_failed = rows(failed1["board"])
    last_served = max((r["arrival"] for r in served_failed), default=float("nan"))
    check(
        failed1["status"]["subway_feeds"]["failed"] == [FAILED]
        and {r["provenance"] for r in served_failed} == {"retained"}
        and sorted(r["arrival"] for r in served_failed)
        == [t for t in kept_calls if t >= failed1["at"]],
        "backend",
        f"first failed poll: every row is retained and the board shows every one of {FAILED}'s "
        f"calls at {STATION}, so its last arrival IS the last retained prediction",
        f"{len(served_failed)} of {len(kept_calls)} calls, the last T0{last_served - T0:+.0f} s",
    )
    check(
        before["at"] <= last_call < after["at"]
        and bool(rows(before["board"]))
        and keys(before["board"]) == [FAILED]
        and before["board"]["systems"][FAILED]["ok"] is False,
        "backend",
        f"BEFORE expiry (T0{before['at'] - T0:+.0f} s): the last retained row is on the board and "
        f"the station's health map names {FAILED}, failed",
        f"keys {keys(before['board'])}, {len(rows(before['board']))} row, "
        f"fetched_at {age(before['board']):.0f} s old",
    )
    check(
        rows(ab) == [] and ab["systems"] is None and ab["feed_timestamp"] is None,
        "backend",
        f"AFTER expiry (T0{after['at'] - T0:+.0f} s, the first poll past it): empty directions, "
        "systems: null, no content clock: the failed contributor dropped out of the map rather "
        "than being reported failed",
        f"keys {keys(ab) or 'none (systems null)'}",
    )
    check(
        ab["fetched_at"] == after["at"] == after["vehicles"]["fetched_at"],
        "backend",
        "AFTER expiry the board's fetched_at is the healthy aggregate's, current",
        f"{age(ab):.0f} s old, was {age(before['board']):.0f} s one poll earlier",
    )
    blk = after["vehicles"]["systems"][FAILED]
    check(
        after["status"]["subway_feeds"]
        == {"total": len(FEED_URLS), "ok": len(FEED_URLS) - 1, "failed": [FAILED]}
        and blk["ok"] is False
        and blk["retained_since"] == failed_from
        and after["vehicles"]["systems"][HEALTHY]["ok"] is True
        and after["at"] < cap_after["at"],
        "backend",
        f"AFTER expiry {FAILED} is still failing and still inside its retention window: "
        "/api/status lists it, its /api/subways block says ok false, retained since "
        f"T0+{POLL_S:.0f} s; {HEALTHY} is ok",
        f"subway_feeds {after['status']['subway_feeds']}, "
        f"{cap_after['at'] - after['at']:.0f} s before the cap",
    )
    fresh_rel = relative(fresh[-1]["board"], fresh[-1]["at"])
    after_diff = differences(relative(ab, after["at"]), fresh_rel)
    check(
        not after_diff,
        "backend",
        "AFTER expiry the WHOLE served board, every clock relative to the instant it was "
        "served, is identical to genuinely fresh empty service's: nothing in it says a "
        "contributor is failing",
        f"differs at {after_diff}" if after_diff else "identical",
    )

    # ---- (b) backend: the cap road ----------------------------------------
    rule("(b) BACKEND: the same exit by the retention cap, untrimmed capture")
    for snap in (cap_before, cap_after):
        b = snap["board"]
        print(
            f"  T0{snap['at'] - T0:+5.0f}  rows {len(rows(b)):>2}  "
            f"systems {','.join(keys(b)) or 'null':<6}  fetched_at {age(b):.0f} s old  "
            f"failed {snap['status']['subway_feeds']['failed']}"
        )
    check(
        keys(cap_before["board"]) == [FAILED]
        and bool(rows(cap_before["board"]))
        and max(r["arrival"] for r in rows(cap_before["board"])) > cap_after["at"],
        "backend",
        "one poll before the cap: retained rows still ahead of the clock, and the health map "
        f"names {FAILED}",
        f"{len(rows(cap_before['board']))} rows",
    )
    cb = cap_after["board"]
    check(
        rows(cb) == []
        and cb["systems"] is None
        and cb["fetched_at"] == cap_after["at"]
        and cap_after["status"]["subway_feeds"]["failed"] == [FAILED],
        "backend",
        f"at the cap: empty directions, systems: null, a current fetched_at, {FAILED} still failed",
        f"keys {keys(cb) or 'none'}",
    )
    cap_diff = differences(relative(cb, cap_after["at"]), fresh_rel)
    check(
        not cap_diff,
        "backend",
        "at the cap the WHOLE served board is identical to fresh empty service's",
        f"differs at {cap_diff}" if cap_diff else "identical",
    )
    # The frontend arm's two worlds reach the rider through the same backend rule: the
    # stale group decodes, but none of its calls at 219 survives the decoder's grace, and
    # the never-decoded group has nothing indexed anywhere, so neither board names it.
    for snap, road in (
        (stale[-1], f"stale content ({FAILED} ok, content {STALE_LAG_S + POLL_S:.0f} s old)"),
        (never[-1], f"never decoded ({FAILED} failing from the first poll)"),
    ):
        diff = differences(relative(snap["board"], snap["at"]), fresh_rel)
        check(
            not diff,
            "backend",
            f"{road}: the WHOLE served board is identical to fresh empty service's as well",
            f"differs at {diff}" if diff else "identical",
        )

    # ---- the boards, on the page ------------------------------------------
    fresh_name = f"fresh empty: every group ok and current, nothing running at {STATION}"
    built = {
        fresh_name: with_contributor(fresh[-1]),
        "A: stale content behind a current poll": with_contributor(stale[-1]),
        "B: never-decoded failed contributor": with_contributor(never[-1]),
        "C: failed after decoding (control)": with_contributor(after),
    }
    world_of = {
        fresh_name: fresh[-1],
        "A: stale content behind a current poll": stale[-1],
        "B: never-decoded failed contributor": never[-1],
        "C: failed after decoding (control)": after,
    }
    served = {
        "fresh empty, as served": fresh[-1],
        "before expiry, as served": before,
        "after expiry, as served": after,
        "at the cap, as served": cap_after,
        "A (stale content), as served": stale[-1],
        "B (never decoded), as served": never[-1],
    }
    views = render(
        {
            **{n: page_at(world_of[n], board) for n, (board, _) in built.items()},
            **{n: page_at(snap) for n, snap in served.items()},
        }
    )
    fresh_view = views[fresh_name]
    served_fresh = views["fresh empty, as served"]

    rule("(c) THE RIDER, on the boards the backend served, through the real page")
    print(f"  the page fetched: {', '.join(views['after expiry, as served']['requested'])}")
    for name in served:
        show(name, views[name])
    for name, road in (
        ("after expiry, as served", f"after expiry, with {FAILED} still failing"),
        ("at the cap, as served", f"at the cap, with {FAILED} still failing"),
        (
            "A (stale content), as served",
            f"with {FAILED}'s content {STALE_LAG_S + POLL_S:.0f} s old",
        ),
        ("B (never decoded), as served", f"with {FAILED} never decoded"),
    ):
        check(
            rider_text(views[name]) == rider_text(served_fresh),
            "rider",
            f"{road}, both surfaces read exactly as genuinely fresh empty service: "
            '"No trains" and nothing else',
            f"differs on {moved(views[name], served_fresh)}"
            if moved(views[name], served_fresh)
            else "identical on popup, panel and spoken",
        )

    rule("(d) FRONTEND: empty boards that DO carry the contributor, through the real page")
    sb = stale[-1]
    stale_block = sb["vehicles"]["systems"][FAILED]
    content_age = sb["board"]["served_at"] - stale_block["feed_timestamp"]
    poll_age = sb["board"]["served_at"] - stale_block["fetched_at"]
    never_block = never[-1]["vehicles"]["systems"][FAILED]
    print(f"  A's {FAILED} block, served : {json.dumps(stale_block)}")
    print(f"  B's {FAILED} block, served : {json.dumps(never_block)}")
    check(
        all(rows(snap["board"]) == [] for snap in (fresh[-1], sb, never[-1]))
        and all(ok for _, ok in built.values()),
        "frontend",
        "the fresh, A and B boards are empty as served, and all four boards built from them "
        "are bodies the endpoint's response model reproduces exactly",
    )
    check(
        stale_block["ok"] is True and poll_age == 0.0 and content_age == STALE_LAG_S + POLL_S,
        "frontend",
        f"A: {FAILED} ok, polled 0 s ago, content {content_age:.0f} s old "
        "(old but recently fetched)",
        f"poll {poll_age:.0f} s, content {content_age:.0f} s",
    )
    check(
        never_block["ok"] is False
        and never_block["fetched_at"] is None
        and never_block["feed_timestamp"] is None
        and never[-1]["status"]["subway_feeds"]["failed"] == [FAILED],
        "frontend",
        f"B: {FAILED} failing and never decoded (no poll clock, no content clock)",
    )
    for name in built:
        show(name, views[name])
    check(
        fresh_view["popup"].count("No trains") == 2
        and any(p.endswith(": No trains.") for p in fresh_view["panel"])
        and rider_text(fresh_view) == rider_text(served_fresh),
        "frontend",
        'the fresh empty board reads "No trains" on both surfaces, with its contributor named '
        "or not (the baseline)",
    )
    for name, label in (
        (
            "A: stale content behind a current poll",
            f"A (content {content_age:.0f} s old, poll 0 s old)",
        ),
        ("B: never-decoded failed contributor", f"B ({FAILED} failing, never decoded)"),
    ):
        check(
            rider_text(views[name]) == rider_text(fresh_view),
            "frontend",
            f"{label} reads exactly as fresh empty service on the popup, the panel and the "
            "spoken board: no warning",
            f"differs on {moved(views[name], fresh_view)}: {extra_lines(views[name], fresh_view)}"
            if moved(views[name], fresh_view)
            else "identical on popup, panel and spoken",
        )
    control = views["C: failed after decoding (control)"]
    control_age = age(built["C: failed after decoding (control)"][0])
    check(
        {"popup", "panel"} <= set(moved(control, fresh_view)),
        "frontend",
        f"CONTROL: an empty board whose one contributor was last polled {control_age:.0f} s ago "
        "does read differently from fresh empty service on the popup and on the panel, so "
        "this page shows a warning when the code raises one",
        f"adds {extra_lines(control, fresh_view)}",
    )

    data_after = data_snapshot()
    check(
        data_after == data_before,
        "world",
        "data/ in the repository is untouched by the run (DATA_DIR is a fresh temporary directory)",
        f"{len(data_after)} files, unchanged" if data_after == data_before else "changed",
    )

    rule("MEASURED SUMMARY")
    print(
        f"  {STATION}'s last retained prediction      : T0{last_call - T0:+.0f} s "
        f"({FAILED} failing from T0+{POLL_S:.0f} s)"
    )
    print(
        f"  health-map keys before / after expiry: {keys(before['board'])} "
        f"at T0{before['at'] - T0:+.0f} s / {keys(ab) or 'none (systems null)'} "
        f"at T0{after['at'] - T0:+.0f} s"
    )
    print(
        f"  board fetched_at age before / after  : {age(before['board']):.0f} s / {age(ab):.0f} s"
    )
    print(f"  /api/status subway failed after      : {after['status']['subway_feeds']['failed']}")
    print(
        f"  cap road keys at T0{cap_before['at'] - T0:+.0f} / T0{cap_after['at'] - T0:+.0f} s : "
        f"{keys(cap_before['board'])} / {keys(cb) or 'none'}"
    )
    print(
        f"  A: poll / content age, warning       : {poll_age:.0f} s / {content_age:.0f} s, "
        f"{extra_lines(views['A: stale content behind a current poll'], fresh_view) or 'none'}"
    )
    print(
        f"  B: never decoded, warning            : "
        f"{extra_lines(views['B: never-decoded failed contributor'], fresh_view) or 'none'}"
    )
    print(
        f"  C (control): poll age, warning       : {control_age:.0f} s, "
        f"{extra_lines(control, fresh_view)}"
    )
    print()
    if failures:
        raise Halt
    print(
        f"DISPOSITION: VERIFIED  With {FAILED} failing (HTTP 503) and {HEALTHY} and six other "
        f"groups healthy, Prospect Av ({STATION})'s last retained prediction "
        f"(T0{last_call - T0:+.0f} s) expired by the T0{after['at'] - T0:+.0f} s poll and its "
        f"board went from health map [{FAILED}] and a {age(before['board']):.0f} s old fetched_at "
        f"to empty directions, systems: null and a {age(ab):.0f} s old fetched_at, identical to "
        f"fresh empty service's board, while /api/status still listed {FAILED} failed (the "
        f"retention cap does the same at T0{cap_after['at'] - T0:+.0f} s). The real page renders "
        f"an empty board with {content_age:.0f} s old content behind a {poll_age:.0f} s old "
        "poll, and one for a never-decoded failed contributor, exactly as genuinely fresh empty "
        'service: "No trains" with no warning on the popup or the panel.'
    )


if __name__ == "__main__":
    try:
        main_script()
    except Halt:
        pass
    except (Exception, SystemExit) as exc:  # anything else the run did not expect, named
        check(False, "world", "the run completes", f"{type(exc).__name__}: {exc}")
    if failures:
        print("REALITY HAS CHANGED. Failed checks:")
        for item in failures:
            print(f"  - {item}")
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        raise SystemExit(1)
