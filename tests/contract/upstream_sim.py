"""A controllable stand-in for every upstream the app polls (C6).

WHY THIS EXISTS. The hermetic suites test each layer against a stub of its
neighbour: pytest injects an httpx client, Playwright serves mock.js. Both are
fast and neither can catch the failure the third audit's closing diagnosis names,
where the backend, the envelopes and the frontend are each locally correct and the
composite a rider sees still lies. Catching that needs the REAL app polling a real
socket, with the thing on the other end under a test's control.

WHAT IT SERVES. One HTTP server, one path per upstream, matching the shapes PR 1's
env seams point at. GTFS-RT bodies are built FROM the committed golden fixtures
rather than handwritten, so entity counts, id formats and field population stay
realistic; the only thing rewritten is time. Static archives are derived from the
committed stops fixtures for the same reason, and PATH's is the committed GTFS
fixture verbatim. Poisoned bodies are raw bytes.

HOW A TEST DRIVES IT. Not by sleeping. Every feed carries a MODE and a FETCH
COUNT, both readable and writable over a control endpoint, so a scenario reads

    sim.set_mode("MNR", "frozen")
    sim.await_polls("MNR", 2)          # the app has now polled it twice, frozen

instead of guessing how long two polls take. That is the whole determinism story:
the observable a test waits on is the app's own behavior, never the clock. See
tests/contract/README.md for the rules this suite holds itself to.

THE MODES, and what each one models:
  live     the upstream is healthy and publishing; every fetch gets a body stamped
           NOW, so the app sees genuinely fresh data.
  frozen   the upstream is up but STUCK: byte-identical bodies forever, with the
           timestamp frozen at the moment of freezing. Deliberately NOT the same as
           an outage, and not a substitute for one: the fetch still succeeds, so
           every per-system health signal stays green and nothing is retained. The
           C2 scenarios therefore use `error`; `frozen` is what
           test_a_frozen_upstream_leaves_every_liveness_signal_green pins.
  empty    a successful 200 carrying b"". The C3 premise: ParseFromString(b"")
           SUCCEEDS, so this is the silent-failure shape a lenient decoder reports
           as a healthy feed with zero vehicles.
  error    a 503, for the ordinary transport failure.
Static archives take a publication NAME instead (good, headers-only-stops,
missing-member, corrupt-zip), because "what did upstream publish" is the question
there, not "is it up".
"""

from __future__ import annotations

import json
import threading
import time
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from google.transit import gtfs_realtime_pb2 as pb

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "backend" / "tests" / "fixtures"

# The eight subway line groups, keyed as feeds/subway.py keys them. The URL suffix
# is the tail the app appends to SUBWAY_RT_BASE, and the empty string for "1-7+S"
# is not a typo: that group fetches the bare base, which is the one shape a
# simulator serving only suffixed paths would leave 404ing.
SUBWAY_GROUPS = {
    "1-7+S": "",
    "ACE": "-ace",
    "BDFM": "-bdfm",
    "G": "-g",
    "JZ": "-jz",
    "NQRW": "-nqrw",
    "L": "-l",
    "SIR": "-si",
}

ALERT_FEEDS = ("subway", "bus", "LIRR", "MNR", "ferry")


@dataclass
class Feed:
    """One realtime upstream: how to build its body, and what it is doing now."""

    key: str
    template: bytes  # the golden fixture this feed's bodies are derived from
    mode: str = "live"
    fetches: int = 0
    frozen_body: bytes | None = None
    # Vehicles drift this far east per generation so the frontend has real
    # movement to interpolate; 0 for feeds with no vehicles.
    drift_deg: float = 0.0
    generation: int = 0


@dataclass
class Archive:
    """One static GTFS upstream: which publication it is currently serving."""

    key: str
    publication: str = "good"
    fetches: int = 0
    bodies: dict[str, bytes] = field(default_factory=dict)


def _restamp(raw: bytes, now: float, drift_deg: float = 0.0, generation: int = 0) -> bytes:
    """Rewrite a golden fixture's clock to `now`, optionally moving its vehicles.

    The captures are months old, so serving them verbatim would have the app
    correctly judge every feed stale before a scenario even started. Only time and
    position are touched; entity ids, trip descriptors, stop sequences and every
    other field stay exactly as the real upstream published them, which is the
    point of building on captures rather than handwriting protobufs.

    THE SHIFT IS ONE DELTA FOR THE WHOLE FEED, never a per-field stamp. The
    capture's internal spacing is data: PATH's identity matcher discriminates
    trains sharing a (route, direction, stop) slot BY their predicted arrival
    times, so collapsing every arrival onto a single value would leave two trains
    at one platform indistinguishable, fail the matcher's bilateral-uniqueness
    check, and churn every id on every poll. Shifting preserves it exactly.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    # A capture with no header timestamp has no origin to shift from; stamping the
    # header and leaving the entity times alone is the only honest thing left, and
    # every committed capture does carry one, so this is a guard rather than a path.
    delta = int(now) - feed.header.timestamp if feed.header.timestamp else 0
    feed.header.timestamp = int(now)
    for entity in feed.entity:
        if entity.HasField("vehicle"):
            vehicle = entity.vehicle
            if vehicle.timestamp:
                vehicle.timestamp += delta
            if drift_deg and vehicle.HasField("position"):
                vehicle.position.longitude += drift_deg * generation
        if entity.HasField("trip_update"):
            if entity.trip_update.timestamp:
                entity.trip_update.timestamp += delta
            for stop_time in entity.trip_update.stop_time_update:
                for when in (stop_time.arrival, stop_time.departure):
                    if when.time:
                        when.time += delta
    return feed.SerializeToString()


def _alerts_body(raw: bytes, now: float, ends_in_s: float | None) -> bytes:
    """The alerts fixture, restamped, with every alert's active window rewritten.

    `ends_in_s` is what makes C1's expiry observable inside a short scenario: set
    it to a few seconds and every alert in the feed goes inactive at a moment the
    test chose, while the feed itself stays frozen. That is the exact shape C1 is
    about, an alert that must disappear because its window closed rather than
    because a poll succeeded.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    feed.header.timestamp = int(now)
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        del alert.active_period[:]
        period = alert.active_period.add()
        period.start = int(now) - 3600
        if ends_in_s is not None:
            period.end = int(now + ends_in_s)
    return feed.SerializeToString()


def _csv(header: str, rows: list[str]) -> str:
    return header + "\r\n" + "".join(row + "\r\n" for row in rows)


_STOPS_HEADER = (
    "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station,wheelchair_boarding"
)


def _stops_table(stops: dict, shape: str) -> str:
    """stops.txt rows for one system, derived from its committed stops fixture.

    THE ARCHIVES ARE BUILT FROM THE SAME CAPTURES AS THE FEEDS, which is what makes
    the sim's two halves agree: the subway golden references platform ids like
    "103N", so an archive of invented stops would place no trains at all and every
    scenario would assert against an empty map. Deriving both from one fixture
    means a train in the feed has a stop in the archive by construction.

    `shape` picks the id convention the loader expects:
      platforms  the subway: fixture ids are platforms (103N/103S), and the parent
                 station is the id minus its trailing direction letter. Both rows
                 are emitted, because C5's gate requires a nonempty PARENT table.
      flat       railroad and ferry: no parent/child split exists in those feeds.

    PATH is deliberately absent from this list: its archive is the committed
    path_gtfs fixture rather than anything synthesized here. See _fixture_members.
    """
    rows: list[str] = []
    if shape == "platforms":
        seen: dict[str, dict] = {}
        for sid, stop in stops.items():
            seen.setdefault(sid[:-1] if sid[-1:] in ("N", "S") else sid, stop)
        for pid, stop in sorted(seen.items()):
            rows.append(f"{pid},{stop['name']},{stop['lat']},{stop['lon']},1,,1")
        for sid, stop in stops.items():
            parent = sid[:-1] if sid[-1:] in ("N", "S") else ""
            rows.append(f"{sid},{stop['name']},{stop['lat']},{stop['lon']},0,{parent},")
    else:
        for sid, stop in stops.items():
            rows.append(f"{sid},{stop['name']},{stop['lat']},{stop['lon']},,,1")
    return _csv(_STOPS_HEADER, rows)


def _archive_members(stops: dict, shape: str) -> dict[str, str]:
    child = next(iter(stops), "1")
    return {
        "stops.txt": _stops_table(stops, shape),
        "routes.txt": _csv(
            "route_id,route_short_name,route_long_name,route_color",
            ["R1,R1,Contract Line,00839C", "R2,R2,Contract Branch,EE352E"],
        ),
        "trips.txt": _csv(
            "route_id,service_id,trip_id,trip_headsign,direction_id,shape_id",
            ["R1,wk,t1,Uptown,0,s1..N01R", "R2,wk,t2,Downtown,1,s2..N01R"],
        ),
        "shapes.txt": _csv(
            "shape_id,shape_pt_sequence,shape_pt_lat,shape_pt_lon",
            [
                "s1..N01R,1,40.70,-74.00",
                "s1..N01R,2,40.71,-74.01",
                "s2..N01R,1,40.72,-74.02",
                "s2..N01R,2,40.73,-74.03",
            ],
        ),
        "stop_times.txt": _csv("trip_id,stop_id,stop_sequence", [f"t1,{child},1", f"t2,{child},1"]),
    }


def _zip_of(members: dict[str, str], drop: tuple[str, ...] = ()) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            if name not in drop:
                zf.writestr(name, body)
    return buf.getvalue()


def _fixture_members(dirname: str) -> dict[str, str]:
    """A committed GTFS fixture directory, as archive members.

    PATH uses this instead of _archive_members because its realtime feed is joined
    to the archive by ROUTE as well as by stop: build_path_station_order keys the
    13d matcher's successor relation on (route_id, direction_id), and the bridge
    feed names routes 859-862. A synthesized routes.txt cannot name those without
    reinventing the fixture that already sits in backend/tests/fixtures/path_gtfs,
    so PATH borrows the real one and its station order comes out real too.
    """
    return {
        member.name: member.read_text(encoding="utf-8")
        for member in sorted((FIXTURES / dirname).iterdir())
        if member.suffix == ".txt"
    }


def _publications(members: dict[str, str]) -> dict[str, bytes]:
    """The four publications a static upstream can serve.

    Written here rather than imported from backend/tests/test_static_shared.py on
    purpose: that module belongs to the C5 hermetic suite and imports the backend's
    loaders, and a contract test depending on it would couple two tiers that are
    supposed to be able to fail independently. The shapes are the ones C5 named,
    and the loaders' own validators are what judge them either way.
    """
    return {
        "good": _zip_of(members),
        # THE FINDING-4 SHAPE: structurally perfect, stops.txt has a header and no
        # data rows, so it parses cleanly to nothing. The header is taken from the
        # publication's own stops.txt rather than a constant, so this stays the
        # finding-4 shape for a fixture-derived archive too.
        "headers-only-stops": _zip_of(
            {**members, "stops.txt": members["stops.txt"].splitlines()[0] + "\r\n"}
        ),
        "missing-member": _zip_of(members, drop=("stops.txt",)),
        "corrupt-zip": b"<!doctype html><html><body>503 from the origin</body></html>",
    }


class UpstreamSim:
    """The server plus its state. Start it, point the app's env at it, drive it."""

    def __init__(self) -> None:
        self.feeds: dict[str, Feed] = {}
        self.archives: dict[str, Archive] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._build_state()

    # -- state -------------------------------------------------------------

    def _build_state(self) -> None:
        subway = (FIXTURES / "subway_1_7_s.pb").read_bytes()
        lirr = (FIXTURES / "railroad_lirr.pb").read_bytes()
        mnr = (FIXTURES / "railroad_mnr.pb").read_bytes()
        alerts = (FIXTURES / "alerts_mnr.pb").read_bytes()
        path = (FIXTURES / "path_rt_gen_a.pb").read_bytes()
        ferry_vp = (FIXTURES / "ferry_vp_a.pb").read_bytes()
        ferry_tu = (FIXTURES / "ferry_tu_a.pb").read_bytes()

        for group in SUBWAY_GROUPS:
            # Every group serves the same capture. The scenarios care about which
            # group FAILS versus which keep advancing, not about their contents
            # differing, and one capture keeps the sim honest about entity shape.
            self.feeds[f"subway:{group}"] = Feed(f"subway:{group}", subway, drift_deg=0.0002)
        self.feeds["LIRR"] = Feed("LIRR", lirr, drift_deg=0.0002)
        self.feeds["MNR"] = Feed("MNR", mnr, drift_deg=0.0002)
        for system in ALERT_FEEDS:
            self.feeds[f"alerts:{system}"] = Feed(f"alerts:{system}", alerts)
        self.feeds["buses"] = Feed("buses", subway, drift_deg=0.0002)
        self.feeds["PATH"] = Feed("PATH", path)
        self.feeds["ferry:vehicle"] = Feed("ferry:vehicle", ferry_vp, drift_deg=0.0002)
        self.feeds["ferry:tripupdate"] = Feed("ferry:tripupdate", ferry_tu)

        def load(name: str) -> dict:
            return json.loads((FIXTURES / name).read_text())

        subway_stops = load("subway_1_7_s_stops.json")
        for key, stop_table, shape in (
            ("subway", subway_stops, "platforms"),
            ("lirr", load("railroad_lirr_stops.json"), "flat"),
            ("mnr", load("railroad_mnr_stops.json"), "flat"),
            # The ferry realtime fixture carries routes and trips but no stops, so
            # its docks borrow the railroad table: the ferry scenarios are about
            # feed freshness and alerts, never about a specific dock.
            ("ferry", load("railroad_mnr_stops.json"), "flat"),
            ("bus", subway_stops, "platforms"),
        ):
            self.archives[key] = Archive(
                key, bodies=_publications(_archive_members(stop_table, shape))
            )
        self.archives["path"] = Archive("path", bodies=_publications(_fixture_members("path_gtfs")))

        # Alerts end this far out by default: comfortably beyond any scenario, so
        # nothing expires unless a test asks for it.
        self.alerts_end_in_s: float | None = 3600.0

    # -- lifecycle ---------------------------------------------------------

    def start(self, port: int = 0) -> str:
        """Bind and serve. Port 0 picks a free one, which is what the pytest
        fixture wants; the browser tier passes a fixed port because a spec running
        in a page cannot be handed one at runtime."""
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- control -----------------------------------------------------------

    def set_mode(self, key: str, mode: str) -> None:
        """live | frozen | empty | error. Freezing captures the CURRENT body."""
        with self._lock:
            feed = self.feeds[key]
            if mode == "frozen" and feed.mode != "frozen":
                feed.frozen_body = self._body_for(feed)
            if mode != "frozen":
                feed.frozen_body = None
            feed.mode = mode

    def set_publication(self, key: str, publication: str) -> None:
        with self._lock:
            self.archives[key].publication = publication

    def fetches(self, key: str) -> int:
        with self._lock:
            if key in self.feeds:
                return self.feeds[key].fetches
            return self.archives[key].fetches

    def await_polls(self, key: str, count: int, deadline_s: float = 60.0) -> int:
        """Block until the app has fetched `key` `count` more times. THE waiting
        primitive of this suite: it waits on the app's own behavior, so it is
        correct whatever the poll interval happens to be, and it fails loudly
        rather than proceeding early."""
        start = self.fetches(key)
        target = start + count
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if self.fetches(key) >= target:
                return self.fetches(key)
            time.sleep(0.05)
        raise AssertionError(
            f"upstream {key} was fetched {self.fetches(key) - start} times in {deadline_s}s, "
            f"expected {count}. The app may not be polling it at all."
        )

    # -- bodies ------------------------------------------------------------

    def _body_for(self, feed: Feed) -> bytes:
        now = time.time()
        if feed.key.startswith("alerts:"):
            return _alerts_body(feed.template, now, self.alerts_end_in_s)
        return _restamp(feed.template, now, feed.drift_deg, feed.generation)

    def serve_feed(self, key: str) -> tuple[int, bytes]:
        with self._lock:
            feed = self.feeds[key]
            feed.fetches += 1
            feed.generation += 1
            mode = feed.mode
            if mode == "error":
                return 503, b""
            if mode == "empty":
                return 200, b""
            if mode == "frozen":
                assert feed.frozen_body is not None
                return 200, feed.frozen_body
            return 200, self._body_for(feed)

    def serve_archive(self, key: str) -> tuple[int, bytes]:
        with self._lock:
            archive = self.archives[key]
            archive.fetches += 1
            return 200, archive.bodies[archive.publication]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "feeds": {k: {"mode": f.mode, "fetches": f.fetches} for k, f in self.feeds.items()},
                "archives": {
                    k: {"publication": a.publication, "fetches": a.fetches}
                    for k, a in self.archives.items()
                },
            }

    # -- the env the app runs with -----------------------------------------

    def env(self, base: str, data_dir: Path) -> dict[str, str]:
        """Every PR 1 seam, pointed here. Nothing is left aimed at a real host, so
        a scenario that somehow escapes this map fails on a refused connection
        rather than quietly reaching the internet."""
        return {
            "SUBWAY_RT_BASE": f"{base}/rt/subway",
            "RAILROAD_RT_BASE": f"{base}/rt/rail",
            "BUS_RT_URL": f"{base}/rt/bus",
            "ALERTS_RT_BASE": f"{base}/rt/alerts",
            "FERRY_ALERTS_URL": f"{base}/rt/alerts/ferry",
            "PATH_RT_URL": f"{base}/rt/path",
            "FERRY_RT_BASE": f"{base}/rt/ferry",
            "SUBWAY_GTFS_URL": f"{base}/static/subway.zip",
            "RAILROAD_STATIC_BASE": f"{base}/static/rail",
            "PATH_STATIC_URL": f"{base}/static/path.zip",
            "FERRY_STATIC_URL": f"{base}/static/ferry.zip",
            "BUS_STATIC_BASE": f"{base}/static/bus",
            "DATA_DIR": str(data_dir),
            "BUS_TIME_API_KEY": "contract-tier-not-a-real-key",
        }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
#
# Paths are matched on the RAW request path, percent-encoding included, because
# that is what the app sends: the MTA Dataservice feed names carry a literal %2F
# that must not be decoded (the decoded form is a different, 403ing route
# upstream), and the sim has to accept exactly what the app asks for.

_STATIC_ROUTES = {
    "/static/subway.zip": "subway",
    "/static/rail/gtfslirr.zip": "lirr",
    "/static/rail/gtfsmnr.zip": "mnr",
    "/static/path.zip": "path",
    "/static/ferry.zip": "ferry",
}


def _resolve(path: str) -> tuple[str, str] | None:
    """(kind, key) for a request path, or None for 404."""
    if path in _STATIC_ROUTES:
        return "archive", _STATIC_ROUTES[path]
    if path.startswith("/static/bus/"):
        return "archive", "bus"
    for group, suffix in SUBWAY_GROUPS.items():
        if path == f"/rt/subway{suffix}":
            return "feed", f"subway:{group}"
    if path == "/rt/rail/lirr%2Fgtfs-lirr":
        return "feed", "LIRR"
    if path == "/rt/rail/mnr%2Fgtfs-mnr":
        return "feed", "MNR"
    if path == "/rt/alerts/ferry":
        return "feed", "alerts:ferry"
    for system in ("subway", "bus", "lirr", "mnr"):
        if path == f"/rt/alerts/camsys%2F{system}-alerts":
            key = {"lirr": "LIRR", "mnr": "MNR"}.get(system, system)
            return "feed", f"alerts:{key}"
    if path == "/rt/bus":
        return "feed", "buses"
    if path == "/rt/path":
        return "feed", "PATH"
    if path == "/rt/ferry/vehicleposition":
        return "feed", "ferry:vehicle"
    if path == "/rt/ferry/tripupdate":
        return "feed", "ferry:tripupdate"
    return None


def _make_handler(sim: UpstreamSim):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:  # noqa: A003
            pass  # the app's own logs are the interesting ones

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/__control":
                body = json.dumps(sim.snapshot()).encode()
                self._send(200, body, "application/json")
                return
            resolved = _resolve(path)
            if resolved is None:
                self._send(404, b"no such upstream", "text/plain")
                return
            kind, key = resolved
            if kind == "archive":
                status, body = sim.serve_archive(key)
                self._send(status, body, "application/zip")
            else:
                status, body = sim.serve_feed(key)
                self._send(status, body, "application/x-protobuf")

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/__control":
                self._send(404, b"", "text/plain")
                return
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if "mode" in payload:
                sim.set_mode(payload["key"], payload["mode"])
            elif "publication" in payload:
                sim.set_publication(payload["key"], payload["publication"])
            elif "alerts_end_in_s" in payload:
                sim.alerts_end_in_s = payload["alerts_end_in_s"]
            self._send(200, b'{"ok":true}', "application/json")

    return Handler
