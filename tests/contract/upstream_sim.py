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
  stale    a successful 200 carrying a body whose CONTENT CLOCK is already
           STALE_CONTENT_BY_S behind. Distinct from `frozen`, which starts at age
           zero and only becomes stale after 90 seconds of waiting that this
           tier's budget cannot afford: `stale` is the same end state reached
           immediately. The fetch succeeds and the poll advances, so every
           liveness signal stays green and only the content age shows it.
Static archives take a publication NAME instead (good, headers-only-stops,
missing-member, corrupt-zip), because "what did upstream publish" is the question
there, not "is it up".

NJ TRANSIT IS THE ONE UPSTREAM HERE THAT IS NOT A GET (15a). Every RailData
endpoint is POST multipart/form-data with a token as a form field, and the token
is minted by POSTing credentials to getToken. So the simulator grows two POST
routes and a third axis of control beside MODE and PUBLICATION: a TOKEN MODE
(`ok`, `reject-first`, `server-error`) that decides what getGTFS does with the
token it is handed. That axis exists for one fact the probes pinned and nothing
else in this repo has to survive: NJ TRANSIT ANSWERS A DEAD TOKEN WITH HTTP 500
AND {"errorMessage":"Invalid token."}, not 401 or 403, so a poller that reads it
as a server error backs off forever while the fix is a re-mint. `reject-first`
reproduces exactly that; `server-error` is the same-class control that must NOT
be mistaken for it.
"""

from __future__ import annotations

import csv
import io
import json
import re
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

# The six borough zips bus_static.BUS_GTFS_URLS asks for, keyed by the tail it
# appends to BUS_STATIC_BASE. One simulator archive each; see _build_state.
BUS_BOROUGHS = {
    "manhattan": "gtfs_m",
    "brooklyn": "gtfs_b",
    "bronx": "gtfs_bx",
    "queens": "gtfs_q",
    "staten_island": "gtfs_si",
    "mta_bus_co": "gtfs_busco",
}

# Every mode a feed may be put into, and every publication an archive may serve.
# Enumerated so set_mode/set_publication can REJECT a name instead of storing it:
# an unknown publication used to surface as a KeyError inside a handler thread,
# which the app sees as a connection reset, i.e. a plausible transport failure
# rather than the control-plane typo it actually is.
MODES = ("live", "frozen", "empty", "error", "stale")

# How far behind `stale` backdates a feed's content clock. Must exceed the app's
# FEED_STALE_AFTER_S (90, cache.py), and that constant is deliberately NOT
# overridable, so unlike every other cadence this tier compresses, this one cannot
# be shrunk: a scenario has to arrive already stale rather than wait to become so.
# 300 matches the hermetic suite's own _stale helper.
STALE_CONTENT_BY_S = 300.0
PUBLICATIONS = ("good", "headers-only-stops", "missing-member", "corrupt-zip")

# What NJ Transit's getGTFS does with the token it is handed. Enumerated and
# validated for the same reason MODES and PUBLICATIONS are: an unknown name must
# be a 400 from the control endpoint, never a mystery failure the app reports as a
# bad upstream.
#
#   ok            every token this simulator minted is accepted.
#   reject-first  THE PROBE'S MOST DANGEROUS FACT, reproduced. The first getGTFS
#                 after a mint answers HTTP 500 with {"errorMessage":"Invalid
#                 token."}, the exact body NJ Transit serves for a dead token, and
#                 accepts everything after. A loader that re-mints once recovers
#                 inside one attempt; one that classifies 500 as an outage never
#                 does.
#   server-error  THE CONTROL. A genuine HTTP 500 with a DIFFERENT body, forever.
#                 It must not provoke a mint and must classify as an attempt
#                 failure. Without it, "re-mint on invalid token" and "re-mint on
#                 any 500" are indistinguishable, and the second one spends mints
#                 against an unpublished rate cap on every real NJT outage.
TOKEN_MODES = ("ok", "reject-first", "server-error")

# Byte-for-byte what the 2026-08-05 probes recorded for a rejected token. Written
# as one literal so a test can assert against the same string the app sniffs for.
NJT_INVALID_TOKEN_BODY = b'{"errorMessage":"Invalid token."}'

# A genuine NJT server error: same status, different body. The whole point is that
# only the BODY tells them apart.
NJT_SERVER_ERROR_BODY = b'{"errorMessage":"An unexpected error occurred."}'


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


@dataclass
class NjtApi:
    """NJ Transit's credentialed API surface: the mint counter and the token mode.

    SEPARATE FROM Archive rather than folded into it, because the two answer
    different questions and a scenario asserts on both at once: the Archive says
    what NJ Transit PUBLISHED (good, headers-only, ...), this says what it did with
    the TOKEN. The token-expiry scenario needs a good publication AND a rejected
    token simultaneously, which one field could not express.

    `mints` is the number the conservation claim is made against: "the loader
    succeeded with exactly two mints" is an assertion about this counter, not about
    a log line.
    """

    token_mode: str = "ok"
    # TWO COUNTERS, for the reason njt_auth.TokenCache carries two: `mints` counts
    # tokens ISSUED, `mint_requests` counts getToken POSTS RECEIVED. A request that
    # this simulator refuses (missing credentials) never becomes a token, so a
    # scenario asserting only on `mints` is blind to exactly the traffic that spends
    # a real rate cap. Every conservation claim here reads BOTH.
    mints: int = 0
    mint_requests: int = 0
    gtfs_requests: int = 0
    # Tokens this simulator has issued, in order. reject-first keys on POSITION:
    # the FIRST token ever minted is the dead one, everything after it is live,
    # which is what models "the token we were holding expired" rather than "the
    # endpoint is flaky".
    issued: list[str] = field(default_factory=list)


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

    WHAT IS DELIBERATELY NOT SHIFTED, and the consequence: trip.start_date and
    trip.start_time. Those two feed feeds.shared._trip_start_ts, which is the sole
    input to the TRIP_START_GRACE_S "has this trip departed yet" gate in the subway
    and railroad decoders. Left at capture values they sit weeks in the past, so the
    gate never fires and IS INERT THROUGHOUT THIS TIER -- deleting it would not fail
    a single scenario here. It cannot be fixed by shifting: _trip_start_ts derives
    the start from service-day midnight plus a prefix of the TRIP ID (centiminutes
    after midnight), so making the gate live would mean rewriting trip ids, and trip
    ids are the identity every matcher and every dedup in the app keys on. Rebasing
    only start_date onto today was measured and is worse: it moves every trip to the
    capture's time of day, which at most run times filters all 160 subway trips and
    empties the map. The gate stays a hermetic-tier claim; see tests/contract/README.md.
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


def _rows(table: str) -> list[dict]:
    """A committed .txt member as dicts. By header NAME, never by column index:
    the fixtures' column orders differ (ferry routes.txt leads with route_id,
    path trips.txt does not), and a positional read would silently pick up the
    wrong field."""
    return list(csv.DictReader(io.StringIO(table)))


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


def _ferry_members() -> dict[str, str]:
    """The committed ferry GTFS trim, plus the one member it does not carry.

    ferry_static._REQUIRED_MEMBERS includes stop_times.txt and the committed trim
    omits it (the real download's copy is enormous and the routes-per-station work
    that needed it used a synthetic table). So one is synthesized here -- but over
    the REAL dock ids from the trim's own stops.txt, which is the whole point: the
    realtime fixture's stop ids join against real docks or against nothing, never
    against another system's stations by numeric coincidence.

    WHAT THE SYNTHESIS IS NOT GOOD FOR, stated because the failure it would cause is
    the quiet kind: one trip per route calling at every dock means the derived
    routes-per-station index gives all 50 docks all 9 routes. Real service does not
    look like that, and no ferry routes-per-station claim can be made at this tier
    against it. Inventing a per-route dock subset would be no more true and would
    read as though it were, which is the trap the Metro-North substitution fell into;
    a correct table would have to come from the real stop_times.txt.
    """
    members = _fixture_members("ferry_gtfs")
    dock_ids = [row["stop_id"] for row in _rows(members["stops.txt"]) if row.get("stop_id")]
    # One REAL trip id per route, read from the trim's own trips.txt. Synthetic trip
    # ids would parse fine and then fold to nothing: static_routes.fold_stop_routes
    # joins stop_times to routes THROUGH trips.txt, so a trip id that is not in
    # trips.txt contributes no route to any dock.
    first_trip_per_route: dict[str, str] = {}
    for row in _rows(members["trips.txt"]):
        route, trip = row.get("route_id"), row.get("trip_id")
        if route and trip:
            first_trip_per_route.setdefault(route, trip)
    rows = [
        f"{trip},{dock},{seq + 1}"
        for trip in first_trip_per_route.values()
        for seq, dock in enumerate(dock_ids)
    ]
    members["stop_times.txt"] = _csv("trip_id,stop_id,stop_sequence", rows)
    return members


def _njt_members(now: float) -> dict[str, str]:
    """NJ Transit archive members, SYNTHESIZED rather than taken from a fixture.

    THE OPPOSITE CALL FROM PATH AND THE FERRY, and the reason is specific rather
    than convenient. Those two borrow committed GTFS fixtures because their
    REALTIME feeds join to the archive by id, and a synthesized id space either
    matches nothing or (worse) matches by accident. NJ Transit has no realtime in
    this phase at all: 15a is static plus the token plumbing, so there is no join
    for a wrong id to corrupt. What the NJT scenarios assert is the AUTH DANCE and
    the validation pipeline, and neither reads a stop name.

    It also keeps this tier independent of a fixture that cannot be generated
    without credentials. backend/tests/fixtures/njt_gtfs/ is produced by
    backend/scripts/gen_njt_fixture.py against the live credentialed API; until it
    is committed the hermetic goldens are dormant, and the contract tier must not
    be dormant with them.

    THE SHAPE IS THE PROBED SHAPE, though, in every way a validator can see:
      * NO calendar.txt AND NO feed_info.txt. Both are absent from the real feed by
        design, so an archive carrying either would test a loader that does not
        exist. This is the member set njt_static._REQUIRED_MEMBERS was written for.
      * route_type=113 on every route, the GTFS extended "Rail Service" type that
        anything switch-casing 0-7 falls through, with route_text_color EMPTY.
      * calendar_dates.txt carrying ADDED (exception_type=1) rows only, which is
        what the real 8,697-row table is.

    THE SERVICE DATES ARE WRITTEN RELATIVE TO NOW, which is the same rewrite
    _restamp performs on the realtime captures and for the same reason: NJ
    Transit's service-date guard rejects a publication whose schedule has entirely
    expired, so a table of fixed dates would quietly turn every NJT scenario red on
    whatever day it aged past. Only the dates move; nothing else does.
    """
    day = 86400.0
    dates = [
        time.strftime("%Y%m%d", time.localtime(now + offset * day))
        # A week behind through six months ahead: comfortably inside the guard, and
        # wide enough that a scenario running across local midnight cannot land on
        # an empty span.
        for offset in (-7, 0, 1, 30, 180)
    ]
    return {
        "agency.txt": _csv(
            "agency_id,agency_name,agency_url,agency_timezone",
            ["NJT,NJ TRANSIT,https://www.njtransit.com,America/New_York"],
        ),
        # route_type 113 and an EMPTY route_text_color on both, matching the real
        # feed's 12 routes. NEC and PASC are used because the probe named Pascack
        # Valley as the one first-class west-of-Hudson route.
        "routes.txt": _csv(
            "route_id,route_short_name,route_long_name,route_type,route_color,route_text_color",
            ["1,NEC,Northeast Corridor,113,EF3E42,", "13,PASC,Pascack Valley,113,A2A4A3,"],
        ),
        "stops.txt": _csv(
            "stop_id,stop_code,stop_name,stop_lat,stop_lon",
            [
                "109,NY,New York Penn Station,40.750568,-73.993519",
                "112,NP,Newark Penn Station,40.734924,-74.164581",
                "38,HB,Hoboken,40.734984,-74.027683",
            ],
        ),
        "trips.txt": _csv(
            "route_id,service_id,trip_id,trip_headsign,direction_id,trip_short_name",
            ["1,SVC1,T1,New York,0,3800", "13,SVC1,T2,Hoboken,1,1600"],
        ),
        "stop_times.txt": _csv(
            "trip_id,arrival_time,departure_time,stop_id,stop_sequence",
            [
                "T1,07:00:00,07:00:00,112,1",
                "T1,07:20:00,07:20:00,109,2",
                "T2,08:00:00,08:00:00,38,1",
                "T2,08:25:00,08:25:00,112,2",
            ],
        ),
        "calendar_dates.txt": _csv(
            "service_id,date,exception_type",
            [f"SVC1,{date},1" for date in dates],
        ),
        # Present and never parsed, exactly as in the real feed (15a defers
        # shapes.txt). Carried so a publication that DROPS it is expressible if a
        # later phase ever requires it.
        "shapes.txt": _csv(
            "shape_id,shape_pt_sequence,shape_pt_lat,shape_pt_lon",
            ["s1,1,40.73,-74.16", "s1,2,40.75,-73.99"],
        ),
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
        self.njt = NjtApi()
        self.not_found: list[str] = []
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
            # EVERY GROUP SERVES THE SAME CAPTURE, and the consequence is stronger
            # than "their contents do not differ". subway.combine_group_trains and
            # combine_group_arrivals dedupe by trip_id ACROSS groups, and eight
            # identical captures carry identical trip ids, so exactly one group ever
            # contributes a row: /api/subways serves 148 trains, not eight times
            # that. What this tier can therefore observe about subway groups is
            # which one FAILS and which keep advancing (the per-system blocks, which
            # are per-group and real); what it cannot observe is any claim about a
            # specific group's DATA, including a failed group's retained arrivals.
            # A scenario needing that would need eight distinct captures.
            self.feeds[f"subway:{group}"] = Feed(f"subway:{group}", subway, drift_deg=0.0002)
        self.feeds["LIRR"] = Feed("LIRR", lirr, drift_deg=0.0002)
        self.feeds["MNR"] = Feed("MNR", mnr, drift_deg=0.0002)
        # ONE MNR CAPTURE SERVED AS ALL FIVE ALERT SYSTEMS, and the limitation that
        # buys is worth naming because it is the same shape as the ferry/Metro-North
        # collision this simulator fixed elsewhere. The capture's informed_entity
        # rows carry route ids 1-6 (Metro-North's Hudson/Harlem/New Haven branches),
        # and 1-6 are also SUBWAY route ids -- so an alert served on the subway feed
        # joins onto subway routes by numeric coincidence. The per-system HEALTH
        # claims this tier makes are unaffected (a feed's system comes from which
        # feed it was, never from the alert's contents), but no alert-to-ROUTE join
        # can be asserted here, including H5's routes-per-station union. Fixing it
        # needs one capture per system; only alerts_mnr.pb is committed.
        for system in ALERT_FEEDS:
            self.feeds[f"alerts:{system}"] = Feed(f"alerts:{system}", alerts)
        # THE BUS FEED BORROWS THE FERRY VEHICLE CAPTURE, which is the least wrong
        # option available and worth stating plainly. buses.fetch_vehicle_positions
        # skips every entity without a position and then drops anything outside the
        # NYC box; the subway capture that used to sit here has 98 vehicles and ZERO
        # positions (correct for NYCT, which publishes none), so /api/buses served an
        # empty list forever while every liveness signal stayed green and no test
        # noticed. The ferry capture's 28 vehicles all carry positions in New York
        # harbour, inside the bus box, so the decode path actually runs. What it does
        # NOT make realistic is route identity: the capture's route_id is blank, so
        # every simulated bus has route_id None and no route-keyed bus behavior is
        # exercised here. test_smoke asserts the endpoint is non-empty so this cannot
        # silently regress to the old state.
        self.feeds["buses"] = Feed("buses", ferry_vp, drift_deg=0.0002)
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
        ):
            self.archives[key] = Archive(
                key, bodies=_publications(_archive_members(stop_table, shape))
            )
        # PATH and ferry take their archives from the committed GTFS fixtures, not
        # from a synthesized table, because both join realtime to static by ID and a
        # synthesized id space either matches nothing or, worse, matches by accident.
        # The ferry archive USED to borrow the Metro-North stops table on the stated
        # grounds that "the ferry realtime fixture carries no stops"; ferry_tu_a.pb
        # carries 24 stop ids, both id spaces are bare integers, and 21 of those 24
        # collided with MNR station ids, so ferry arrivals resolved successfully to
        # Metro-North stations in the Hudson Valley. A wrong-but-plausible join is
        # worse than an empty one, and it is exactly what this tier exists to catch.
        self.archives["path"] = Archive("path", bodies=_publications(_fixture_members("path_gtfs")))
        # The archive fixes the STOP join and can do nothing about the ROUTE one:
        # both committed ferry captures carry a blank route_id on every entity, so
        # every simulated boat and every ferry arrival is route-unknown here whatever
        # routes.txt says. Freshness, alerts and dock resolution are exercised; route
        # colouring and route-keyed ferry behavior are not.
        self.archives["ferry"] = Archive("ferry", bodies=_publications(_ferry_members()))
        # One Archive PER BOROUGH ZIP. They could share one (the app treats them as
        # one index), but then a single warmup cycle would bump one counter six
        # times and await_polls' contract -- "the app has fetched this upstream N
        # more times" -- would silently mean something else for that key. Separate
        # keys also make the partial shape expressible: one borough corrupt, five
        # fine, which is the failure bus_static is most likely to meet in production.
        for borough in BUS_BOROUGHS:
            self.archives[f"bus:{borough}"] = Archive(
                f"bus:{borough}", bodies=_publications(_archive_members(subway_stops, "platforms"))
            )
        # NJ Transit (15a). Synthesized rather than fixture-derived, for the reason
        # _njt_members states at length; served over POST behind a token, which is
        # what the two routes and the token mode below are for.
        self.archives["njt"] = Archive("njt", bodies=_publications(_njt_members(time.time())))

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
        """live | frozen | empty | error. Freezing captures the CURRENT body.

        Both arguments are validated. An unknown MODE used to fall through
        serve_feed's if-chain to the healthy body, so a typo silently did nothing
        and the scenario failed much later blaming the app for not noticing an
        outage that never happened."""
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {MODES}")
        with self._lock:
            feed = self.feeds[key]
            if mode == "frozen" and feed.mode != "frozen":
                feed.frozen_body = self._body_for(feed)
            if mode != "frozen":
                feed.frozen_body = None
            feed.mode = mode

    def set_publication(self, key: str, publication: str) -> None:
        """Validated for the same reason set_mode is, and a sharper one: an unknown
        name reached serve_archive as a KeyError inside a handler thread, which
        socketserver turns into a closed socket with no response, which the app
        records as a DOWNLOAD FAILURE. A control-plane typo was thereby disguised
        as the product behavior a C5 scenario is trying to distinguish from it."""
        if publication not in PUBLICATIONS:
            raise ValueError(f"unknown publication {publication!r}; expected one of {PUBLICATIONS}")
        with self._lock:
            self.archives[key].publication = publication

    def set_token_mode(self, mode: str) -> None:
        """ok | reject-first | server-error. Validated like set_mode and
        set_publication, and for the sharper of the two reasons: an unknown token
        mode falling through to the healthy body would make a scenario about token
        expiry pass without ever expiring a token."""
        if mode not in TOKEN_MODES:
            raise ValueError(f"unknown token mode {mode!r}; expected one of {TOKEN_MODES}")
        with self._lock:
            self.njt.token_mode = mode

    def mints(self) -> int:
        """How many tokens NJ Transit has ISSUED in this scenario."""
        with self._lock:
            return self.njt.mints

    def mint_requests(self) -> int:
        """How many getToken POSTs NJ Transit has RECEIVED, issued or not. THE
        number a conservation claim should be made against: a refused mint costs
        the same against a rate limit as a successful one, and `mints` cannot see
        it."""
        with self._lock:
            return self.njt.mint_requests

    def gtfs_requests(self) -> int:
        """How many getGTFS POSTs NJ Transit has received, accepted or rejected."""
        with self._lock:
            return self.njt.gtfs_requests

    def await_mints(self, count: int, deadline_s: float = 30.0) -> int:
        """Block until at least `count` getToken POSTs have arrived, then return the
        total. Used to reach a known point before asserting NO FURTHER mints
        happened: an assertion that a counter stayed at N is only meaningful once
        it has definitely reached N, and sleeping to find out would break rule 2."""
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if self.mint_requests() >= count:
                return self.mint_requests()
            time.sleep(0.05)
        raise AssertionError(
            f"NJ Transit received {self.mint_requests()} getToken POSTs in {deadline_s}s, "
            f"expected at least {count}. The app may not be reaching getToken at all."
        )

    def fetches(self, key: str) -> int:
        with self._lock:
            if key in self.feeds:
                return self.feeds[key].fetches
            return self.archives[key].fetches

    def await_fetched(self, key: str, count: int = 1, deadline_s: float = 60.0) -> int:
        """Block until `key` has been fetched `count` times IN TOTAL.

        Absolute, where await_polls is relative, and the distinction matters for the
        static archives: a loader fetches its archive once at warmup and then never
        again while it stays valid, so `await_polls(key, 1)` -- which waits for one
        MORE fetch -- would wait out its whole deadline against a perfectly healthy
        app."""
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            if self.fetches(key) >= count:
                return self.fetches(key)
            time.sleep(0.05)
        raise AssertionError(
            f"upstream {key} was fetched {self.fetches(key)} times in {deadline_s}s, expected "
            f"{count}. The app may never ask for it at all."
        )

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

    def _body_for(self, feed: Feed, age_s: float = 0.0) -> bytes:
        # age_s backdates the whole body, header and entity times together, which
        # is what _restamp's single-delta rule already guarantees. An upstream
        # publishing late is late about everything, not just its header.
        now = time.time() - age_s
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
            if mode == "stale":
                return 200, self._body_for(feed, age_s=STALE_CONTENT_BY_S)
            return 200, self._body_for(feed)

    def serve_archive(self, key: str) -> tuple[int, bytes]:
        with self._lock:
            archive = self.archives[key]
            archive.fetches += 1
            return 200, archive.bodies[archive.publication]

    def serve_njt_token(self, fields: dict[str, str]) -> tuple[int, bytes]:
        """getToken: hand out a token, permissively.

        PERMISSIVE ABOUT CREDENTIALS on purpose. What this tier tests is the app's
        behavior around a token (single-flight minting, one re-mint on the probe's
        500, never a mint on a real 500), not NJ Transit's password check, and a
        credential check here would only add a way for the harness to fail that
        looks like a product bug. Any non-empty username and password work.

        NOT A FIXED TOKEN, which is the one deliberate deviation from "the sim
        returns a fixed token". Numbered tokens are what make the expiry scenario
        expressible at all: reject-first has to distinguish the token that died
        from the one that replaced it, and identical strings cannot.
        """
        with self._lock:
            # Counted BEFORE the guard: the POST was made whether or not it yields
            # a token, and it is the POST that costs.
            self.njt.mint_requests += 1
            if not fields.get("username") or not fields.get("password"):
                return 400, b'{"errorMessage":"Missing credentials."}'
            self.njt.mints += 1
            token = f"njt-token-{self.njt.mints}"
            self.njt.issued.append(token)
            return 200, json.dumps({"UserToken": token}).encode()

    def serve_njt_gtfs(self, fields: dict[str, str]) -> tuple[int, bytes]:
        """getGTFS: the archive, behind whatever the token mode says about tokens.

        THE 500s HERE ARE THE POINT OF THE WHOLE ROUTE. NJ Transit answers a dead
        token with HTTP 500 and {"errorMessage":"Invalid token."}, and answers a
        real fault with HTTP 500 and something else. Both shapes are served here,
        under `reject-first` and `server-error`, so a scenario can prove the app
        tells them apart rather than asserting it reads the code that does.

        The archive fetch counter advances on every request INCLUDING a rejected
        one, because upstream really was asked; await_fetched therefore means "the
        app reached getGTFS", which is what the hermeticity smoke test needs it to
        mean.
        """
        with self._lock:
            archive = self.archives["njt"]
            archive.fetches += 1
            self.njt.gtfs_requests += 1
            token = fields.get("token") or ""
            mode = self.njt.token_mode
            if mode == "server-error":
                # A GENUINE fault: same status, different body, and it never ends.
                # An app that re-mints on this is spending mints on an outage.
                return 500, NJT_SERVER_ERROR_BODY
            if token not in self.njt.issued:
                # A token this simulator never issued (or none at all) is dead by
                # definition, and gets the real upstream's answer for that.
                return 500, NJT_INVALID_TOKEN_BODY
            if mode == "reject-first" and token == self.njt.issued[0]:
                # THE EXPIRY. The first token ever minted is the one that died; the
                # re-mint that follows works. Keyed on the token rather than on a
                # request counter so a loader that retried with the SAME token
                # still fails, which is the behavior the scenario is about.
                return 500, NJT_INVALID_TOKEN_BODY
            return 200, archive.bodies[archive.publication]

    def record_not_found(self, path: str) -> None:
        """Remember a path the app asked for that no route matched.

        THE OTHER HALF OF HERMETICITY. Comparing seam NAMES proves a seam exists and
        is pointed here; it says nothing about whether this simulator can answer what
        the app actually builds from it. A base seam with no matching route, or a
        path scheme that drifts (a filename change, a new query suffix), shows up as
        a 404 -- which the app records as an ordinary download failure and no
        scenario looks at. Collected here so a smoke test can assert there were none,
        and so the failure names the exact path."""
        with self._lock:
            self.not_found.append(path)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "feeds": {k: {"mode": f.mode, "fetches": f.fetches} for k, f in self.feeds.items()},
                "archives": {
                    k: {"publication": a.publication, "fetches": a.fetches}
                    for k, a in self.archives.items()
                },
                "njt": {
                    "token_mode": self.njt.token_mode,
                    "mints": self.njt.mints,
                    "mint_requests": self.njt.mint_requests,
                    "gtfs_requests": self.njt.gtfs_requests,
                },
                "not_found": list(self.not_found),
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
            # BOTH NJT seams, together. Pointing only the archive here would leave
            # the MINT aimed at raildata.njtransit.com, so every contract run would
            # spend a real token against an unpublished rate cap and the tier would
            # stop being hermetic in the one place it is hardest to notice.
            "NJT_TOKEN_URL": f"{base}/njt/getToken",
            "NJT_STATIC_URL": f"{base}/njt/getGTFS",
            "DATA_DIR": str(data_dir),
            "BUS_TIME_API_KEY": "contract-tier-not-a-real-key",
            # Credentials, not seams (the monitor needs the real ones set, so they
            # are deliberately outside SEAM_NAMES). Present by default because the
            # DEFAULT contract app is a configured one; the not-configured scenario
            # launches with them emptied instead.
            "NJT_USERNAME": "contract-tier-not-a-real-user",
            "NJT_PASSWORD": "contract-tier-not-a-real-password",
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
    for borough, stem in BUS_BOROUGHS.items():
        if path == f"/static/bus/{stem}.zip":
            return "archive", f"bus:{borough}"
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


# NJ Transit's two POST routes. Kept out of _resolve, which answers for GETs: the
# app never GETs these and a GET that reached one would be a real defect worth the
# 404 (and worth appearing in the not_found list the hermeticity smoke test reads).
_NJT_ROUTES = ("/njt/getToken", "/njt/getGTFS")


def _multipart_fields(body: bytes, content_type: str) -> dict[str, str]:
    """Field name -> value out of a multipart/form-data body.

    Hand-rolled rather than reached for from the standard library, because the
    obvious tool (cgi.FieldStorage) is gone in modern Python and the alternatives
    want a full email message. The bodies here are a handful of short text fields
    with no filenames and no nested parts, which is exactly the case a dozen lines
    covers correctly.

    A body this cannot parse yields an EMPTY mapping, which makes the request look
    tokenless and therefore gets NJ Transit's invalid-token answer. That is the
    right failure: a scenario fails visibly rather than a harness bug being served
    as a healthy archive.
    """
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match:
        return {}
    boundary = b"--" + match.group(1).encode()
    fields: dict[str, str] = {}
    for part in body.split(boundary):
        head, sep, value = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        name = re.search(rb'name="([^"]*)"', head)
        if not name:
            continue
        # split() consumed the delimiter, so each part still carries the CRLF that
        # preceded it. The final part is the closing "--\r\n" and has no header,
        # so it never reaches here.
        if value.endswith(b"\r\n"):
            value = value[:-2]
        fields[name.group(1).decode()] = value.decode("utf-8", errors="replace")
    return fields


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
                sim.record_not_found(path)
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
            path = urlparse(self.path).path
            if path in _NJT_ROUTES:
                length = int(self.headers.get("Content-Length", 0))
                fields = _multipart_fields(
                    self.rfile.read(length), self.headers.get("Content-Type", "")
                )
                if path == "/njt/getToken":
                    status, body = sim.serve_njt_token(fields)
                    self._send(status, body, "application/json")
                else:
                    status, body = sim.serve_njt_gtfs(fields)
                    # An error body is JSON, a success body is the zip. Sending the
                    # right content type for each keeps the app's own handling
                    # honest rather than letting it succeed on a mislabeled body.
                    kind = "application/zip" if status == 200 else "application/json"
                    self._send(status, body, kind)
                return
            if path != "/__control":
                sim.record_not_found(path)
                self._send(404, b"", "text/plain")
                return
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            # 400 ON ANYTHING NOT ACTED ON. The browser tier's only check on a
            # control call is response.ok(), so a 200 for an ignored payload made
            # that assertion meaningless: a renamed field or a mistyped key would
            # sail through and the spec would fail a minute later blaming the
            # frontend for not reacting to an outage nobody caused.
            try:
                if "mode" in payload:
                    sim.set_mode(payload["key"], payload["mode"])
                elif "publication" in payload:
                    sim.set_publication(payload["key"], payload["publication"])
                elif "token_mode" in payload:
                    sim.set_token_mode(payload["token_mode"])
                elif "alerts_end_in_s" in payload:
                    sim.alerts_end_in_s = payload["alerts_end_in_s"]
                else:
                    raise ValueError(
                        f"control payload names none of "
                        f"mode/publication/token_mode/alerts_end_in_s: {sorted(payload)}"
                    )
            except (ValueError, KeyError) as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")
                return
            self._send(200, b'{"ok":true}', "application/json")

    return Handler
