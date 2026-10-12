#!/usr/bin/env python3
"""A13 (Low): "Timing settings accept zero, negative and nonfinite values."

Reproduction and regression check for finding A13 of the 2026-10-12 audit (the report's
"Additional confirmed narrow defects" table), over main at 1e3e73d.

THE AUDIT'S CLAIM, the row's evidence cell verbatim:

    "backend/env_seams.py:130 accepts 0, -1, nan and inf. This requires operator
    misconfiguration; it is not a public input path."

    Recommended correction: "Reject nonfinite or nonpositive polling/retry settings at
    startup, with explicit exceptions only where zero has documented semantics."

    Cited code (where to LOOK; nothing below asserts a line, a name or a comment):
      https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/backend/env_seams.py#L130

WHAT THE CITATION IS. env_seams.seconds is the parser the cadence, retention and retry
seams are read through, once, at import. It returns float(raw) for any text float()
accepts, and float() accepts "0", "-1", "nan" and "inf". Its "fail loudly" contract
covers MALFORMED text (the ValueError float() raises), not the range of a well formed
number.

WHAT THIS SCRIPT MEASURES

  A. WHICH PRODUCTION SEAMS ROUTE THROUGH THE PARSER, observed rather than read: the
     parser and its tuple sibling are wrapped in a recorder for the length of one
     `import main` (the module uvicorn loads), so every call a production module makes
     at import is seen with its seam name, its default and whatever else it passed.
     Each is checked against the module's own registry, env_seams.DEFAULTS, and so is
     the set section C sets: every registry seam whose default is a plain number.
  B. THE PARSER ITSELF over "0", "-1", "nan", "inf" and the well formed control "30":
     each production call A recorded is REPLAYED exactly as made (same arguments, same
     keywords) with its seam set to the value, so a per-seam option a call site passes
     is honored. Accepted or raised, and the value returned. Plus the control that the
     refusal path exists: malformed text ("2s") raises. Also measured, BEYOND THE
     AUDIT'S CITATION: the tuple sibling (the static retry schedule) over
     "0,-1,nan,inf".
  C. THE RUNNING APP. One fresh interpreter per value, with every timing seam set to
     that value before the backend is imported, imports main and starts main.app the
     way `uvicorn main:app` does: the ASGI lifespan protocol on the app object, so
     whatever the app runs at startup runs here, wherever in the app it is declared
     (the lifespan it was built with, a wrapper around that, a router's lifespan that
     FastAPI merges in, an ASGI middleware). lifespan.startup.failed is a refusal.
     Measured: whether it started; what the parser handed each production call site;
     once every loop has run once, how many further feed-poll cycles, alert-poll
     cycles and failing subway static load attempts complete in WINDOW_S of wall
     time; the first six delays the failing static warmup actually waits; and, through
     the public /api/railroads endpoint, whether a failed system's trains are still
     served on the poll it fails and a day later. Those are the outcomes a fix
     anywhere in the app the server loads must change (in the parser, at a call site,
     at startup, or where a value is used), which is why they are measured and not
     only the parser. At 'nan' the loops' cadence is undefined (a nan deadline defeats
     the ordering of asyncio's timer heap, so a sleeper wakes when some unrelated timer
     does); those counts are printed and not recorded.

  OUT OF REACH, by construction: a refusal that lives outside the app object, such as a
  preflight in the deploy start command or a different server entry point. No
  in-process script observes those.

  ONE REFACTOR THIS SCRIPT DOES NOT SURVIVE, and it fails closed: the retry delays are
  read through the asyncio.sleep observer in injection 4 below, so a warmup backoff
  rewritten to wait some other way (asyncio.wait_for on an event, say) leaves C with
  no recorded delays and turns this script red, never green. A02 and A02b share the
  same dependency; the directory README says so.

WHAT IS PRODUCTION CODE HERE (nothing is re-implemented): env_seams.seconds and
env_seams.seconds_tuple; their production call sites, reached by importing main;
main.app and everything its lifespan starts, including the feed and alert poll loops
with the real alert refresher, and the subway static warmup with its real retry
schedule, backoff and jitter; the railroad refresher (found through the feed
refresher registry), its retention merge, and the /api/railroads route that serves
the result.

WHAT IS INJECTED, only through public names the backend documents as its monkeypatch
surface, stdlib hooks, or the environment (no private helper is called or replaced, so
renaming one cannot turn this red):
  1. The seam values, as environment variables, which is the operator's own lever.
  2. A recorder around the two parsers for the length of `import main` (A), then removed.
  3. In C only: every feed refresher the registry pollers.FEED_REFRESHERS lists becomes
     a counting no-op (the poll loop resolves them per cycle for exactly this);
     main.fetch_service_alerts counts and raises (an alert upstream that fails, without
     an upstream), so the real alert refresher runs its failure path each cycle;
     main.load_subway_stops counts and raises; the other static loaders and the bus
     index wait forever, so they neither succeed nor reach anything.
  4. In C only, an observer on asyncio.sleep, installed before the backend imports: it
     records the delays requested by the task that called the failing subway loader
     (the warmup, identified by what it does, not by name), and compresses that task's
     first five waits to zero so six retry delays are seen in a fraction of a second.
     Every other task's sleeps pass through untouched. The sixth wait is real.
  5. The retention probe (after the window, inside the running app): main's
     fetch_railroad_trains returns one train per system on a healthy poll, then
     reports one system failed; time.time is a hand-set clock for the probe's length
     (a healthy poll, the failing poll one second later, and a day after that).
  6. A socket guard in every interpreter: getaddrinfo, create_connection, connect and
     connect_ex record the attempt and refuse it. Checked empty.

HERMETIC: _hermetic.contain() runs before the first backend import and verify() after it,
in the parent and in each child, although no seam here reaches the network: the rule is
every backend import. DATA_DIR points at a fresh temporary directory in each interpreter,
so nothing under data/ is written (the lifespan reads the committed AirTrain fixture, as
every start does); no NJ Transit credential survives; nothing resolves a host or opens a
connection (the endpoint is reached through httpx's in-process ASGI transport). The only
wall-clock dependence is C's iteration counts over WINDOW_S, and their checks are a floor
(at least BUSY_FLOOR) for a loop that never waits and exactly 0 for one that does; the
DISPOSITION line states the floor, not a count. The warmup's jitter draws from the
module-level random, seeded per child. A .env that set one of these seams would reach
the children through load_dotenv; .env.example sets none.

WHERE ZERO HAS DOCUMENTED SEMANTICS (the correction's exception), found by reading and
not asserted here: none of the seams that route through seconds() documents zero, a
negative or a nonfinite value as meaningful. The one degenerate input with documented
semantics is the tuple sibling's EMPTY string, which yields an empty schedule that the
warmup reads as "no schedule" and replaces with the steady-state retry interval.

RUN (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a13_env_seams_accepts_nonpositive.py

RECORDED DISPOSITION: VERIFIED. Exits 0 while the parser accepts all four values and the
app starts and runs on them as measured; non-zero, naming every check that moved, the
moment either stops being true.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import math
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"

# The script runs itself once per value for section C: a seam is read once, at import,
# so a value can only be observed through the production call sites in a fresh process.
CHILD_FLAG = "--a13-child"
CHILD_SEAMS_VAR = "A13_TIMING_SEAMS"  # the seams C sets, handed to each child
CHILD_RESULT = "A13-CHILD-RESULT "
IS_CHILD = len(sys.argv) == 3 and sys.argv[1] == CHILD_FLAG

BAD_VALUES = ("0", "-1", "nan", "inf")  # the audit's four
CONTROL = "30"  # well formed, positive and finite
MALFORMED = "2s"  # the existing refusal path
WINDOW_S = 0.5  # wall time each child counts loop iterations over, once every loop has run
SETTLE_S = 20.0  # the most a child waits for every loop's first iteration
BUSY_FLOOR = 20  # a loop that does not sleep completes at least this many in WINDOW_S
RETRIES_SEEN = 6  # retry delays observed from the failing warmup
DAY_S = 86_400.0

# A fresh data root, set before the backend reads it, so nothing under data/ is written.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="a13-data-")
atexit.register(shutil.rmtree, os.environ["DATA_DIR"], ignore_errors=True)

# THE WARMUP OBSERVER (C, item 4 of WHAT IS INJECTED). Installed before the backend is
# imported, so it is the sleep production code finds however it refers to it.
WARMUP_TASKS: set = set()  # tasks that called the failing subway loader
WARMUP_DELAYS: list[float] = []  # what those tasks asked asyncio.sleep for, in order
SLEEP_HOOKS: list = []  # called after each recorded delay (the child's progress check)

if IS_CHILD:
    # Assigned, never defaulted: this IS the configuration under test, and load_dotenv
    # (which runs at the backend's import) never overrides a key that is already set.
    CHILD_VALUE = sys.argv[2]
    for _seam in filter(None, os.environ[CHILD_SEAMS_VAR].split(",")):
        os.environ[_seam] = CHILD_VALUE

    _real_sleep = asyncio.sleep

    async def _observed_sleep(delay, result=None):
        task = asyncio.current_task()
        if task is not None and task in WARMUP_TASKS:
            WARMUP_DELAYS.append(delay)
            if len(WARMUP_DELAYS) < RETRIES_SEEN:
                delay = 0  # compressed; the delay it asked for is what is recorded
            for hook in SLEEP_HOOKS:
                hook()
        return await _real_sleep(delay, result)

    asyncio.sleep = asyncio.tasks.sleep = _observed_sleep


class _WarmupWarnings(logging.Handler):
    """Counts WARNING and above logged by the failing warmup's own task: what an
    operator's log shows while it retries. Installed on the root logger BEFORE main
    imports, so main's basicConfig (which it installs only when the root has no
    handler) never attaches a stream and a child's stderr stays empty."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            task = asyncio.current_task()
        except RuntimeError:  # no running loop in this thread
            return
        if task is not None and task in WARMUP_TASKS:
            self.count += 1


WARNINGS = _WarmupWarnings()
logging.getLogger().addHandler(WARNINGS)

# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT (see the 09-05 README).
sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(BACKEND))

import env_seams  # noqa: E402

# THE RECORDER (section A). Installed between env_seams' import and main's, so every
# production module that declares a seam while main imports calls through it. Removed
# straight after: sections B and C drive the parsers themselves. It takes any
# signature and passes it through, so a call site that hands the parser an extra
# keyword (a per-seam option) reaches the real parser rather than dying here.
PARSER_CALLS: list[dict] = []
_REAL_PARSERS = {"seconds": env_seams.seconds, "seconds_tuple": env_seams.seconds_tuple}


def _recording(kind: str, real):
    def recorded(*args, **kwargs):
        entry = {
            "parser": kind,
            "name": args[0] if args else kwargs.get("name"),
            "default": args[1] if len(args) > 1 else kwargs.get("default"),
            "args": args,
            "kwargs": kwargs,
        }
        PARSER_CALLS.append(entry)
        try:
            entry["returned"] = real(*args, **kwargs)
        except Exception as exc:
            entry["raised"] = f"{type(exc).__name__}: {exc}"
            raise
        return entry["returned"]

    return recorded


for _kind, _real in _REAL_PARSERS.items():
    setattr(env_seams, _kind, _recording(_kind, _real))

IMPORT_ERROR: str | None = None
try:
    import bus_static  # noqa: E402
    import ferry_static  # noqa: E402
    import main  # noqa: E402
    import njt_static  # noqa: E402
    import path_static  # noqa: E402
    import pollers  # noqa: E402
    import railroad_static  # noqa: E402
except Exception as exc:  # a child whose configuration production refuses at import
    if not IS_CHILD:
        raise
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
finally:
    for _kind, _real in _REAL_PARSERS.items():
        setattr(env_seams, _kind, _real)

import httpx  # noqa: E402  (the in-process ASGI transport for /api/railroads)

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. A child keeps its stdout
# for its one result line; the parent prints the containment line for the run.
_hermetic.verify(announce=not IS_CHILD)

# THE SOCKET GUARD. Nothing here should resolve a host or open a connection; anything
# that tries is recorded, refused, and fails the HERMETIC check.
SOCKET_ATTEMPTS: list[str] = []


def _refuse(what: str):
    def refuse(*args, **kwargs):
        SOCKET_ATTEMPTS.append(f"{what} {args[1:2] if what.startswith('connect') else args[:2]!r}")
        raise OSError(f"a13 socket guard: {what} refused, this script opens no connection")

    return refuse


socket.getaddrinfo = _refuse("getaddrinfo")
socket.create_connection = _refuse("create_connection")
socket.socket.connect = _refuse("connect")
socket.socket.connect_ex = _refuse("connect_ex")


def outcome(fn, *args, **kwargs) -> str:
    """repr of what a parser returned, or 'raised <Type>'. repr, not ==, because the
    values under test include nan, which equals nothing, itself included."""
    try:
        return repr(fn(*args, **kwargs))
    except Exception as exc:
        return f"raised {type(exc).__name__}"


# ---------------------------------------------------------------------------
# The child: one configuration, the app as the server starts it, WINDOW_S of wall time
# ---------------------------------------------------------------------------


async def wall_wait(event: asyncio.Event, seconds: float) -> bool:
    """Wait for `event` for at most `seconds` of WALL time, kept by a thread rather than
    an asyncio timer. Not a nicety: asyncio orders its timers with `<`, which a nan
    deadline defeats, so in the nan configuration an ordinary timer can be held behind
    one (measured while this script was written: a 0.5 s window kept by asyncio.sleep
    closed after about fourteen seconds). The window here does not depend on the loop's
    timers at all."""
    loop = asyncio.get_running_loop()
    expired = asyncio.Event()
    timer = threading.Timer(seconds, loop.call_soon_threadsafe, args=(expired.set,))
    timer.start()
    waiters = [asyncio.ensure_future(event.wait()), asyncio.ensure_future(expired.wait())]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        timer.cancel()
        for waiter in waiters:
            waiter.cancel()
    return event.is_set()


class StartupRefused(Exception):
    """The app answered lifespan.startup with anything but lifespan.startup.complete."""


@contextlib.asynccontextmanager
async def served(app):
    """Start `app` the way the server does: the ASGI lifespan protocol on the app object
    `uvicorn main:app` loads, not a named lifespan function. startup.complete is a
    start; startup.failed (Starlette sends it when anything in the app's startup
    raises) or an app that ends without replying is a refusal."""
    inbox: asyncio.Queue = asyncio.Queue()
    outbox: asyncio.Queue = asyncio.Queue()
    scope = {"type": "lifespan", "asgi": {"version": "3.0", "spec_version": "2.0"}, "state": {}}
    runner = asyncio.ensure_future(app(scope, inbox.get, outbox.put))
    await inbox.put({"type": "lifespan.startup"})
    replied = asyncio.ensure_future(outbox.get())
    await asyncio.wait([replied, runner], return_when=asyncio.FIRST_COMPLETED)
    reply = replied.result() if replied.done() else None
    if reply is None:
        replied.cancel()
    if reply is None or reply.get("type") != "lifespan.startup.complete":
        why = (reply or {}).get("message") or "the app ended its lifespan without replying"
        try:
            await runner
        except Exception as exc:
            why = f"{type(exc).__name__}: {exc}"
        raise StartupRefused(why)
    try:
        yield
    finally:
        await inbox.put({"type": "lifespan.shutdown"})
        await outbox.get()
        await runner


def _train(system: str) -> dict:
    """One GPS train for `system`, every field the railroad model declares."""
    return {
        "system": system, "trip_id": f"a13-{system}", "route_id": None,
        "latitude": 40.75, "longitude": -73.99, "bearing": None, "train_num": None,
        "stop_id": None, "stop_name": None, "direction": None, "prev_lat": None,
        "prev_lon": None, "prev_time": None, "next_time": None,
    }


async def retention_probe(refresh) -> str:
    """Whether a failed railroad system's trains are still SERVED under the retention
    cap this process resolved: the real railroad refresher (from the registry) over
    three polls (both systems healthy; one failed a second later; still failed a day
    after that), each followed by GET /api/railroads through the app itself. Only the
    upstream fetch and the clock are injected."""
    failing, healthy = "LIRR", "MNR"
    t0 = time.time()
    poll = {"now": t0, "failed": []}

    async def fetch_railroad_trains(*args, **kwargs):
        failed = list(poll["failed"])
        trains = [_train(s) for s in (failing, healthy) if s not in failed]
        # (trains, arrivals by system, feed timestamp, failed systems, per-system feed
        # timestamps, per-system position steps): the shape the refresher unpacks.
        return trains, {}, poll["now"], failed, {}, {}

    real_clock, real_fetch = time.time, main.fetch_railroad_trains
    main.fetch_railroad_trains = fetch_railroad_trains
    time.time = lambda: poll["now"]
    served_rows: list[str] = []
    try:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://a13.test") as api:
            for offset, failed in ((0.0, []), (1.0, [failing]), (1.0 + DAY_S, [failing])):
                poll["now"], poll["failed"] = t0 + offset, failed
                await refresh(main.app, None)
                response = await api.get("/api/railroads")
                if response.status_code != 200:
                    served_rows.append(f"HTTP {response.status_code}")
                    continue
                systems = {row["system"] for row in response.json()["data"]}
                served_rows.append("carried" if failing in systems else "dropped")
    finally:
        time.time, main.fetch_railroad_trains = real_clock, real_fetch
    healthy_poll, failing_poll, day_later = served_rows
    if healthy_poll != "carried":
        return f"probe broken: the healthy poll served {healthy_poll}"
    return f"{failing_poll} on the failing poll, {day_later} a day later"


async def run_app() -> dict:
    feed_calls: dict[str, int] = {}
    counts = {"alerts": 0, "static": 0}
    saved: list[tuple[object, str, object]] = []
    # Set once every loop has finished its FIRST iteration and the warmup has asked for
    # its sixth delay. The window is counted from here, not from startup: start-up work
    # (each poll loop builds its httpx client as it starts) runs on the event loop for
    # a while that varies with machine load, and a window opened before it measured the
    # machine: one early run counted a single feed poll at '0'.
    settled = asyncio.Event()

    def snapshot() -> dict[str, int]:
        feeds = min(feed_calls.values()) if len(feed_calls) == len(pollers.FEED_REFRESHERS) else 0
        return {"feed": feeds, "alerts": counts["alerts"], "static": counts["static"]}

    def note() -> None:
        now = snapshot()
        if now["feed"] and now["alerts"] and len(WARMUP_DELAYS) >= RETRIES_SEEN:
            settled.set()

    def swap(module: object, name: str, value: object) -> None:
        saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    def feed_stub(feed: str):
        async def refresher(app, client):
            feed_calls[feed] = feed_calls.get(feed, 0) + 1
            note()

        return refresher

    real_refreshers = {feed: getattr(pollers, attr) for feed, attr in pollers.FEED_REFRESHERS}
    for feed, attr in pollers.FEED_REFRESHERS:
        swap(pollers, attr, feed_stub(feed))

    async def failing_alert_fetch(*args, **kwargs):
        counts["alerts"] += 1
        note()
        raise RuntimeError("a13: injected alert failure, no upstream contacted")

    async def failing_static_load(*args, **kwargs):
        WARMUP_TASKS.add(asyncio.current_task())
        counts["static"] += 1
        note()
        raise RuntimeError("a13: injected static failure, no upstream contacted")

    async def parked(*args, **kwargs):
        await asyncio.Event().wait()

    swap(main, "fetch_service_alerts", failing_alert_fetch)
    swap(main, "load_subway_stops", failing_static_load)
    swap(railroad_static, "load_railroad_static", parked)
    swap(path_static, "load_path_static", parked)
    swap(ferry_static, "load_ferry_static", parked)
    swap(njt_static, "load_njt_static", parked)
    swap(bus_static, "ensure_index", parked)
    SLEEP_HOOKS.append(note)

    result: dict = {"started": False, "startup_error": None, "settled": False}
    base = after = snapshot()
    try:
        async with served(main.app):
            result["started"] = True
            result["settled"] = await wall_wait(settled, SETTLE_S)
            base = snapshot()
            await wall_wait(asyncio.Event(), WINDOW_S)  # nothing sets it: the full window
            after = snapshot()
            result["retry_delays"] = WARMUP_DELAYS[:RETRIES_SEEN]
            result["retention"] = await retention_probe(real_refreshers["railroads"])
    except StartupRefused as exc:
        result["startup_error"] = f"StartupRefused: {exc}"
    finally:
        SLEEP_HOOKS.clear()
        for module, name, value in reversed(saved):
            setattr(module, name, value)
    result["first"] = base
    result["window"] = {key: after[key] - base[key] for key in after}
    return result


def child_main() -> int:
    random.seed(13)  # the warmup's backoff jitter draws from the module-level random
    result: dict = {
        "value": CHILD_VALUE,
        "import_error": IMPORT_ERROR,
        "parsed": {
            c["name"]: c.get("raised") or repr(c.get("returned"))
            for c in PARSER_CALLS
            if c["parser"] == "seconds"
        },
    }
    if IMPORT_ERROR is None:
        result.update(asyncio.run(run_app()))
    else:
        result.update({"started": False, "startup_error": None})
    result["warmup_warnings"] = WARNINGS.count
    result["socket_attempts"] = SOCKET_ATTEMPTS
    print(CHILD_RESULT + json.dumps(result))
    return 0


# ---------------------------------------------------------------------------
# The parent
# ---------------------------------------------------------------------------

FAILURES: list[str] = []


def check(label: str, measured, recorded, ok: bool | None = None, audit: str = "not stated"):
    """One named expectation: the measured value beside the recorded one and the audit's."""
    ok = (measured == recorded) if ok is None else ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: measured {measured}, recorded {recorded}"
          f" (audit: {audit})")
    if not ok:
        FAILURES.append(f"{label}: measured {measured}, recorded {recorded}")
    return ok


def rule(title: str) -> None:
    print()
    print("-" * 78)
    print(title)
    print("-" * 78)


def summarize(outcomes: dict[str, str]) -> str:
    """One string when every seam agrees, else each seam's own outcome."""
    distinct = sorted(set(outcomes.values()))
    if len(distinct) == 1:
        return distinct[0]
    if not distinct:
        return "(no calls)"
    return "; ".join(f"{name}={value}" for name, value in sorted(outcomes.items()))


def replay(call: dict, raw: str) -> str:
    """Re-run one recorded production call, exactly as it was made, with its seam set to
    `raw`, then restore the environment."""
    name = call["name"]
    before = os.environ.get(name)
    os.environ[name] = raw
    try:
        return outcome(_REAL_PARSERS[call["parser"]], *call["args"], **call["kwargs"])
    finally:
        if before is None:
            del os.environ[name]
        else:
            os.environ[name] = before


def fmt_delay(delay: float) -> str:
    return repr(delay) if not math.isfinite(delay) else f"{delay:.2f}"


def on_rung(delay: float, rung: float) -> bool:
    """`delay` is `rung` under the warmup's documented +-10% jitter."""
    if math.isnan(rung):
        return math.isnan(delay)
    if math.isinf(rung) or rung == 0:
        return delay == rung
    low, high = sorted((0.9 * rung, 1.1 * rung))
    return low <= delay <= high


def spawn_children(seams: list[str]) -> dict[str, dict]:
    """One child per value, in parallel. Each gets the parent's (contained) environment
    with every seam removed except the contained NJ Transit URLs, and sets the timing
    seams itself before its first import."""
    env = {k: v for k, v in os.environ.items() if k not in env_seams.SEAM_NAMES or
           k in _hermetic.NJT_URL_SEAMS}
    env.pop("DATA_DIR", None)  # each child makes its own
    env[CHILD_SEAMS_VAR] = ",".join(seams)
    procs = {
        value: subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), CHILD_FLAG, value],
            cwd=REPO,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for value in (*BAD_VALUES, CONTROL)
    }
    results: dict[str, dict] = {}
    for value, proc in procs.items():
        try:
            out, err = proc.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            err = "TIMED OUT\n" + err
        line = next((ln for ln in out.splitlines() if ln.startswith(CHILD_RESULT)), None)
        if proc.returncode != 0 or line is None:
            results[value] = {"crashed": f"exit {proc.returncode}: {err.strip()[-600:]}"}
        else:
            results[value] = json.loads(line[len(CHILD_RESULT):])
    return results


def parent_main() -> int:
    t0 = time.monotonic()
    print("=" * 78)
    print("A13  Timing settings accept zero, negative and nonfinite values")
    print("=" * 78)
    print(f"repo     : {REPO}")
    print(f"DATA_DIR : {os.environ['DATA_DIR']}")

    # ---- A: which production seams route through the parser -------------------
    rule("A. WHICH PRODUCTION SEAMS ROUTE THROUGH env_seams.seconds, observed while main imports")
    routed = [c for c in PARSER_CALLS if c["parser"] == "seconds"]
    routed_tuple = [c for c in PARSER_CALLS if c["parser"] == "seconds_tuple"]
    seams = {c["name"]: c["default"] for c in routed}
    tuple_seams = {c["name"]: c["default"] for c in routed_tuple}
    print(f"  {'seam':<26}{'parser':<15}{'call-site default':>18}{'registry default':>18}")
    for c in routed + routed_tuple:
        registry = env_seams.DEFAULTS.get(c["name"], "(absent)")
        extra = f"  (also passed {c['kwargs']!r})" if c["kwargs"] else ""
        print(f"  {c['name']:<26}{c['parser']:<15}{c['default']!r:>18}{registry!r:>18}{extra}")
    check("production seams declared through seconds() while main imports", len(seams), 4)
    check("production seams declared through seconds_tuple() while main imports",
          len(tuple_seams), 1)
    mismatched = sorted(
        name for name, default in {**seams, **tuple_seams}.items()
        if env_seams.DEFAULTS.get(name, object()) != default
    )
    check("routed seams whose default disagrees with env_seams.DEFAULTS", mismatched, [])
    # The seams section C sets: every registry entry with a plain-number default. Taken
    # from the registry, not from the recorder, so C still sets them if a change moves a
    # call site to a different parser.
    timing = sorted(name for name, default in env_seams.DEFAULTS.items()
                    if isinstance(default, (int, float)) and not isinstance(default, bool))
    check("registry seams with a numeric default (the ones C sets) NOT read through seconds()",
          sorted(set(timing) - set(seams)), [])

    # ---- B: the parser itself ----------------------------------------------
    rule("B. THE PARSER: each recorded production call to env_seams.seconds, replayed as made,\n"
         "   with its seam set to each value")
    print(f"  {'value':<8}{'outcome for every routed seam':<40}")
    parsed: dict[str, str] = {}
    for raw in (*BAD_VALUES, CONTROL, MALFORMED):
        per_seam = {c["name"]: replay(c, raw) for c in routed}
        parsed[raw] = summarize(per_seam)
        print(f"  {raw!r:<8}{parsed[raw]:<40}({len(per_seam)} seams)")
    print()
    for raw, want in (("0", "0.0"), ("-1", "-1.0"), ("nan", "nan"), ("inf", "inf")):
        check(f"seconds() on {raw!r}, every routed seam", parsed[raw], want, audit="accepts")
    check(f"seconds() on the control {CONTROL!r}", parsed[CONTROL], "30.0")
    check(f"seconds() on malformed {MALFORMED!r} (the refusal path exists)", parsed[MALFORMED],
          "raised ValueError")
    schedule_raw = ",".join(BAD_VALUES)
    tuple_parsed = summarize({c["name"]: replay(c, schedule_raw) for c in routed_tuple})
    print()
    print("  BEYOND THE AUDIT'S CITATION, the tuple sibling (the static retry schedule):")
    check(f"seconds_tuple() on {schedule_raw!r}", tuple_parsed, "(0.0, -1.0, nan, inf)")

    # ---- C: the running app --------------------------------------------------
    rule(f"C. THE RUNNING APP: every timing seam set to the value, main imported, main.app\n"
         f"   started through the ASGI lifespan as uvicorn starts it, {WINDOW_S}s of wall time\n"
         f"   (one fresh interpreter per value)")
    children = spawn_children(timing)
    print("  Counts are iterations completed in the window that opens once every loop has run")
    print("  once: further feed-poll cycles, alert-poll cycles, and static load attempts by a")
    print("  subway warmup whose load fails every time. 'warnings' is what that warmup logged.")
    print()
    print(f"  {'value':<8}{'started':<9}{'parser returned':<17}{'feed polls':>11}"
          f"{'alert polls':>12}{'static loads':>13}{'warnings':>10}")
    for value, r in children.items():
        if "crashed" in r:
            print(f"  {value!r:<8}CHILD FAILED: {r['crashed']}")
            continue
        window = r.get("window", {})
        print(f"  {value!r:<8}{'yes' if r['started'] else 'NO':<9}"
              f"{summarize(r['parsed']) if r['parsed'] else '-':<17}"
              f"{window.get('feed', '-'):>11}{window.get('alerts', '-'):>12}"
              f"{window.get('static', '-'):>13}{r.get('warmup_warnings', '-'):>10}")
        refusal = r.get("import_error") or r.get("startup_error")
        if refusal:
            print(f"  {'':<8}refused: {refusal}")
    print()
    print("  And where the other two seams are consumed, in the same running apps:")
    print(f"  {'value':<8}{'/api/railroads: a failed system':<52}{'first six retry delays, s'}")
    for value, r in children.items():
        if "retention" in r:
            delays = ", ".join(fmt_delay(d) for d in r["retry_delays"])
            print(f"  {value!r:<8}{r['retention']:<52}{delays}")
    print()
    expected_parse = {"0": "0.0", "-1": "-1.0", "nan": "nan", "inf": "inf", CONTROL: "30.0"}
    # Retention is capped by `now - started >= cap`: true at once for a cap of zero or
    # below, never true for nan (every comparison with nan is false) or inf.
    expected_retention = {
        "0": "dropped on the failing poll, dropped a day later",
        "-1": "dropped on the failing poll, dropped a day later",
        "nan": "carried on the failing poll, carried a day later",
        "inf": "carried on the failing poll, carried a day later",
        CONTROL: "carried on the failing poll, dropped a day later",
    }
    # Each rung is min(schedule rung, steady-state seam), and the default schedule's
    # last rung IS the steady-state seam: min(15, nan) is 15, min(nan, nan) is nan.
    nan, inf = math.nan, math.inf
    expected_rungs = {
        "0": (0, 0, 0, 0, 0, 0),
        "-1": (-1, -1, -1, -1, -1, -1),
        "nan": (15, 30, 60, nan, nan, nan),
        "inf": (15, 30, 60, inf, inf, inf),
        CONTROL: (15, 30, 30, 30, 30, 30),
    }
    for value, r in children.items():
        if "crashed" in r:
            check(f"{value!r}: the child ran to completion", r["crashed"], "exit 0")
            continue
        check(f"{value!r}: the app imported and started as the server starts it", r["started"],
              True, audit="accepts" if value in BAD_VALUES else "not stated")
        check(f"{value!r}: what the parser handed every production call site",
              summarize(r["parsed"]) if r["parsed"] else "(nothing)", expected_parse[value])
        if not r["started"]:
            continue
        check(f"{value!r}: every loop completed a first iteration", r["settled"], True)
        check(f"{value!r}: /api/railroads, a failed system's trains under the resolved cap",
              r["retention"], expected_retention[value])
        rungs = expected_rungs[value]
        delays = r["retry_delays"]
        check(f"{value!r}: the failing warmup's first six retry delays",
              ", ".join(fmt_delay(d) for d in delays),
              ", ".join(fmt_delay(float(x)).replace(".00", "") for x in rungs)
              + " (each within the +-10% jitter)",
              ok=len(delays) == len(rungs) and all(map(on_rung, delays, rungs)))
        window = r["window"]
        span = f"in {WINDOW_S}s"
        whats = (("feed", "feed polls"), ("alerts", "alert polls"),
                 ("static", "static load retries"))
        if value in ("0", "-1"):
            # Zero or below: asyncio.sleep returns at once, so no loop ever waits.
            for key, what in whats:
                check(f"{value!r}: {what} {span}", window[key], f">= {BUSY_FLOOR}",
                      ok=window[key] >= BUSY_FLOOR)
        elif value == "nan":
            # A nan deadline compares false against every other in asyncio's timer heap,
            # so a loop sleeping on one wakes when some UNRELATED timer is popped near
            # it: its cadence is undefined rather than any number. The warmup's sixth
            # wait is nan too. Printed above and not recorded.
            print(f"  [ -- ] {value!r}: feed polls {window['feed']}, alert polls "
                  f"{window['alerts']}, static load retries {window['static']} {span} "
                  f"(printed, not recorded: an undefined cadence)")
        else:
            for key, what in whats:
                check(f"{value!r}: {what} {span}", window[key], 0)

    # ---- hermetic ------------------------------------------------------------
    rule("HERMETIC")
    check("socket attempts in this interpreter", SOCKET_ATTEMPTS, [])
    child_sockets = sorted({a for r in children.values() for a in r.get("socket_attempts", [])})
    check("socket attempts in the children", child_sockets, [])

    print()
    print(f"  wall time {time.monotonic() - t0:.1f}s")
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
        f"DISPOSITION: VERIFIED  env_seams.seconds accepts '0', '-1', 'nan' and 'inf', "
        f"returning 0.0, -1.0, nan and inf, for all {len(seams)} production seams routed "
        f"through it ({', '.join(sorted(seams))}), and the app starts on each as the server "
        f"starts it. At '0' and '-1' nothing waits (feed polls, alert polls and failing "
        f"static load retries each at least {BUSY_FLOOR} in {WINDOW_S}s, against 0 at "
        f"'{CONTROL}') and a failed system's trains are dropped on the poll it fails, while "
        f"at 'nan' and 'inf' they are still served a day later and the static retry "
        f"schedule ends in nan or inf."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(child_main() if IS_CHILD else parent_main())
