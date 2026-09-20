#!/usr/bin/env python3
"""F03 (P1) "Station countdowns lose upstream content freshness" : reproduction.

THE AUDIT'S CLAIM (section 1 of docs/reviews/audit-2026-09-05.md, finding F03):

    "A valid subway feed with a header ten minutes old and a prediction two
    minutes ahead was passed through the real decoder, refresh path and endpoint.
    The vehicle response exposed approximately 600 seconds of content lag. The
    station-arrivals response instead exposed a newly stamped `fetched_at`, the
    prediction, and no content-age field. The popup and station panel derive
    freshness from that acquisition timestamp. The existing per-contributing-group
    logic correctly handles failed fetches. It does not handle repeatedly
    successful retrieval of old information. Similar arrivals envelopes for other
    modes also omit content freshness."

    Cited: backend/routes/subway.py L85 (arrivals response), backend/models.py
    L191 (arrival model), frontend/helpers.js L713 (popup age calculation).

    Acceptance: "repeatedly returning an old, valid HTTP-200 feed makes its
    countdowns visibly qualified as stale; other healthy contributors remain
    distinguishable."

FIXED ON claude/freshness-6-2-boards, so this script now checks the FIX. Every check
that moved carries the audit's value in its label ("was: ..."), in the F02/F05/F10/F12
shape: it exits 0 while the repaired behaviour holds and non-zero the moment it comes
back. The before-values are the audit's own measurement of this world: 599.7 s of
content lag on /api/subways; /api/subway-arrivals/219 serving a freshly stamped
fetched_at and no content clock, with 8 of 8 groups ok; a popup that rendered no
qualifier and a panel whose age was now - fetched_at, so both read a ten minute old
prediction as a live two minute countdown. Contract 6.1 put every missing number in
the payload and this script recorded, until 6.2, that no rider surface read any of it.

RUN IT (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-09-05/f03_arrivals_content_freshness.py

THE WORLD IS THE ACCEPTANCE TEST'S, IMPORTED RATHER THAN COPIED. The acceptance has
two clauses, and the second ("other healthy contributors remain distinguishable")
needs a healthy contributor on the same board, which the original reproduction did
not have: its seven other groups served header-only feeds that contribute nothing at
219. So the world comes from backend/tests/test_f03_boards.py (its _restamp,
_upstream, _poll_and_serve and constants), the module that asserts the backend half,
and the served board is compared to tests/e2e/fixtures/f03_board_219.json, the body
the browser half renders. One world, three readers, and each fails if it drifts.

WHAT IS INJECTED (the fault, stated plainly):

  * 1-7+S is served the COMMITTED capture backend/tests/fixtures/subway_1_7_s.pb
    (real MTA content) with ONE constant integer delta added to every timestamp it
    carries, so its header sits exactly 600 s before the poll clock: the audit's
    "header ten minutes old". The capture's internal structure is preserved, so the
    "prediction two minutes ahead" is the capture's own stop at header + 720 s. The
    SAME BYTES are served on both polls: repeatedly returning an old, valid 200.
  * ACE is served a copy of the same capture stamped 5 s behind each poll, with every
    trip_id renamed "h-" so the cross-group trip dedup keeps it as a distinct
    contributor. Station 219 (Prospect Av) then carries three rows from each group
    in each direction, interleaved under the cap of six.
  * The six other groups get a valid, fresh, header-only feed, so every poll is a
    fully successful eight-group poll: no failure anywhere.
  * The clock is pinned at FROZEN_S of tests/e2e/fixtures/api.js (2026-07-02T12:00Z)
    so the served body can be compared to the golden to the bit. 600 s is therefore
    the INJECTED condition, not a measurement; what is measured is which surfaces
    expose it and on which rows.

WHAT IS PRODUCTION CODE HERE (nothing below is re-implemented):

  * main.fetch_subway_trains (the real decoder and fan-out) through a stub
    AsyncClient returning real httpx.Response objects; pollers._refresh_subways, the
    real refresh path; main.app over httpx.ASGITransport for GET /api/subways and
    GET /api/subway-arrivals/219, so every number is read out of SERVED JSON.
  * frontend/helpers.js, frontend/systems/subway.js and frontend/stations.js loaded
    whole into one node:vm in index.html order over a minimal DOM: the popup is the
    real subwayArrivalsHtml and the panel is the real renderStationDetail, writing the
    real #stations-detail and #stations-announce.
  * backend/models.py for the arrivals-model table; the model -> endpoint map is read
    out of backend/routes/*.py source.

  No network is used. No NJ Transit host is contacted and no token is minted.

WHAT IT MEASURES

  a) content lag on /api/subways: fetched_at - feed_timestamp. The fix leaves it be.
  b) the served board at 219: which contributor each row came from, which clock it
     carries, the systems block naming both, and equality with the golden.
  c) the real frontend on that body, ROW BY ROW, popup and panel: FIXED here.
  d) a second successful poll of the same old bytes, 15 s later: the aged rows' age
     grows and they stay qualified; the healthy rows stay fresh and silent.
  e) every arrivals response model, per mode (contract 6.1).

EXIT STATUS: 0 while the fix holds (DISPOSITION: FIXED); non-zero with a named
failure when reality has changed.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"
# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT. env_seams calls load_dotenv
# when it is imported, so a credential scrub that runs before that import is undone by
# it; the addresses set here are what make this process unable to reach NJ Transit at
# all. See _hermetic for the leak this closes and the measurement behind it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import main  # noqa: E402
import models  # noqa: E402
import test_f03_boards as world  # noqa: E402  (the acceptance test's world, not a copy)
from feeds.subway import SUBWAY_FEED_URLS, _platform_direction  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()

STATION_ID = world.STATION_ID  # 219, Prospect Av (2/5), reached by the 1-7+S capture
QUALIFIER = "as of 10m ago"  # what a 600 s old prediction reads at the frozen instant

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    """Record a named assertion; the script exits non-zero if any one fails."""
    if not ok:
        failures.append(f"{label}{(': ' + detail) if detail else ''}")
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def prime_app() -> None:
    """The app.state a lifespan would have built, minus anything needing the network:
    the committed platform stops and the parent-station index derived from them with the
    production _platform_direction. The same state test_f03_boards' fixture primes."""
    stops = json.loads(world._STOPS.read_text())
    stations: dict[str, dict] = {}
    for stop_id, stop in stops.items():
        _, station_id = _platform_direction(stop_id)
        stations.setdefault(
            station_id, {"name": stop["name"], "lat": stop["lat"], "lon": stop["lon"]}
        )
    state = main.app.state
    state.feed_cache = {"subways": main._fresh_entry()}
    state.subway_feed_health = None
    state.subway_static_status = "ready"
    state.subway_stops = stops
    state.subway_stations = stations
    state.subway_station_routes = {}
    state.subway_arrivals = {}
    state.subway_arrivals_by_system = {}
    state.subway_positions = {}


def run_world() -> tuple[dict, dict, dict, dict]:
    """Two real polls of the world, 15 s apart, the aged group serving identical bytes."""
    clock = [world.NOW]

    async def polls() -> tuple[dict, dict, dict, dict]:
        raw = world._CAPTURE.read_bytes()
        aged = world._restamp(raw, world.NOW, world.AGED_LAG_S)
        board_1, vehicles_1 = await world._poll_and_serve(world._upstream(aged, raw, world.NOW))
        clock[0] = world.NOW + world.REPOLL_S
        board_2, vehicles_2 = await world._poll_and_serve(world._upstream(aged, raw, clock[0]))
        return board_1, vehicles_1, board_2, vehicles_2

    with mock.patch.object(main.time, "time", lambda: clock[0]):
        return asyncio.run(polls())


def contributor(row: dict) -> str:
    """Which group a served row came from. The "h-" prefix is this world's label for
    the healthy copy; test_f03_boards also proves each labelled row really came out of
    its own group's per-group index."""
    return world.HEALTHY_GROUP if row["trip_id"].startswith(world.HEALTHY_PREFIX) else world.AGED_GROUP


# ---------------------------------------------------------------------------
# (c) the frontend, executed rather than read
# ---------------------------------------------------------------------------

NODE_DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const path = require("path");
const frontend = process.argv[2];
const input = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));

// Just enough of an element for stations.js, which builds with createElement, sets
// textContent and className, and appends. The panel is read back as the code built it.
function makeEl(tag) {
  return {
    tagName: tag, className: "", textContent: "", hidden: false, children: [], style: {},
    classList: { contains: () => false, toggle: () => {}, add: () => {}, remove: () => {} },
    append(...n) { this.children.push(...n); },
    appendChild(n) { this.children.push(n); return n; },
    replaceChildren(...n) { this.children = n.slice(); },
    contains: () => false, addEventListener() {}, removeEventListener() {},
    setAttribute() {}, removeAttribute() {}, focus() {},
  };
}
const elements = Object.create(null);
const byId = (id) => (elements[id] ??= Object.assign(makeEl("div"), { id }));
const sandbox = {
  console, URLSearchParams,
  document: { getElementById: byId, createElement: makeEl, body: makeEl("body"), activeElement: null },
  L: { canvas: () => ({}) },
  staleTreatments: [],
  // MR4's canvas-theme registry, which systems/subway.js joins at load (defined in
  // systems/shared.js, which this harness does not load). No theme is swapped here.
  registerCanvasFamily: (_name, paint) => paint,
  // An empty, current alert store: this measures the board, not the alert join.
  alertsSystems: {}, alertsFetchedAt: null, alertsFirstAttemptAt: null,
  alertsClockNow: () => sandbox.__nowMs / 1000,
  __nowMs: 0,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// The browser's clock, set per board to the instant it was served, so the countdowns
// and ages are exactly what a rider sees on receiving it.
vm.runInContext(
  `(() => { const Real = Date; class Frozen extends Real {
     constructor(...a) { if (a.length === 0) super(globalThis.__nowMs); else super(...a); }
     static now() { return globalThis.__nowMs; } } globalThis.Date = Frozen; })();`,
  sandbox,
);
for (const rel of ["helpers.js", "systems/subway.js", "stations.js"]) {
  vm.runInContext(fs.readFileSync(path.join(frontend, rel), "utf8"), sandbox, { filename: rel });
}
vm.runInContext("var alertsIndex = indexAlerts([]);", sandbox);

/* The popup's rows, per direction, as {html, qualifier}.

   MAP REDESIGN STAGE MR5 CHANGED THE MARKUP THIS READS, and the record it belongs to is
   unchanged: a lagging contributor's rows still say how old they are and a current one's still say
   nothing. What moved is where the words sit. subwayArrivalsHtml used to write a heading in
   `.arr-dir` and then its rows joined by `<br>`; it writes section 5's vocabulary now, a heading in
   `.dir` and a `.arr` grid of three `<span>` cells per row. So a direction's rows are the cells of
   the `.arr` that follows its heading, taken three at a time, and the qualifier is still read by
   its own class. */
function popupRows(html) {
  const out = {};
  for (const section of html.split('<div class="dir">').slice(1)) {
    const [name, rest] = section.split("</div>");
    const grid = rest.slice(rest.indexOf('<div class="arr">'));
    const cells = grid.split("\n").filter((cell) => cell.includes("<span"));
    const rows = [];
    for (let i = 0; i < cells.length; i += 3) {
      const row = cells.slice(i, i + 3).join(" ");
      const m = row.match(/<span class="arr-qualifier">([^<]*)<\/span>/);
      rows.push({ html: row, qualifier: m ? m[1] : "" });
    }
    out[name] = rows;
  }
  return out;
}

const results = [];
for (const board of input.boards) {
  sandbox.__nowMs = board.served_at * 1000;
  sandbox.alertsFetchedAt = board.served_at;
  sandbox.alertsFirstAttemptAt = board.served_at;
  const station = { id: board.station_id, name: board.station_name, routes: [] };
  const popupHtml = sandbox.subwayArrivalsHtml(station, board);
  sandbox.__entry = {
    key: `subway|${board.station_id}`, kind: "subway", systemLabel: "Subway", noun: "train",
    id: board.station_id, name: board.station_name, routes: [], wheelchair: false,
    arrivalsUrl: `/api/subway-arrivals/${board.station_id}`,
  };
  sandbox.__body = board;
  vm.runInContext(
    "panelStation = __entry; panelBody = __body; panelError = null;" +
      " panelAnnounced = null; panelAlertsAnnounced = null; renderStationDetail();",
    sandbox,
  );
  const detail = byId("stations-detail");
  const panelRows = [];
  const panelLines = [];
  for (const kid of detail.children) {
    if (kid.tagName === "ul") for (const li of kid.children) panelRows.push(li.textContent);
    else if (kid.className === "station-detail-stale") panelLines.push(kid.textContent);
  }
  results.push({
    popupHtml,
    popupRows: popupRows(popupHtml),
    panelRows,
    panelLines,
    spoken: byId("stations-announce").textContent,
  });
}
process.stdout.write(JSON.stringify(results));
"""


def spoken_sentences(spoken: str, station: str) -> list[str]:
    """The row sentences of one spoken board, in order. The panel speaks "{station},
    Subway. Northbound: s1. s2. Southbound: s3. s4", so this takes the station off the
    front and each direction label off its first sentence. A row sentence holds no ". "
    of its own (its clock label reads "8:02 AM"), so splitting there is exact, and a
    board-wide line spoken ahead of the rows would come back as a sentence of its own."""
    prefix = f"{station}, Subway. "
    if not spoken.startswith(prefix):
        return []
    return [
        re.sub(r"^(?:Northbound|Southbound): ", "", part).rstrip(".")
        for part in spoken[len(prefix) :].split(". ")
    ]


def run_frontend(boards: list[dict]) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        driver = tmpdir / "f03_frontend.js"
        driver.write_text(NODE_DRIVER)
        payload = tmpdir / "payload.json"
        payload.write_text(json.dumps({"boards": boards}))
        proc = subprocess.run(
            ["node", str(driver), str(FRONTEND), str(payload)],
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        raise SystemExit(f"node driver failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# (e) every arrivals response model, per mode
# ---------------------------------------------------------------------------

# Any field that would let a client date the CONTENT rather than the fetch.
CONTENT_CLOCK_NAMES = {
    "feed_timestamp",
    "served_at",
    "content_age",
    "content_time",
    "source_time",
    "source_content_time",
    "generated_at",
    "observed_at",
    "updated_at",
}

MODE_OF_MODEL = {
    "StationArrivals": "Subway",
    "RailroadStationArrivals": "LIRR / Metro-North",
    "PathStationArrivals": "PATH",
    "NjtStationArrivals": "NJ Transit Rail",
    "FerryStationArrivals": "NYC Ferry",
}

ROUTE_DECORATOR_RE = re.compile(
    r'@router\.get\(\s*"(?P<path>[^"]+)"\s*,\s*response_model=(?P<model>\w+)\s*\)'
)


def arrivals_model_table() -> list[dict]:
    endpoints: dict[str, str] = {}
    for source in sorted((BACKEND / "routes").glob("*.py")):
        for match in ROUTE_DECORATOR_RE.finditer(source.read_text()):
            endpoints.setdefault(match.group("model"), match.group("path"))
    rows = []
    for name, obj in vars(models).items():
        if not (isinstance(obj, type) and name.endswith("StationArrivals")):
            continue
        fields = list(obj.model_fields)
        rows.append(
            {
                "model": name,
                "mode": MODE_OF_MODEL.get(name, "?"),
                "endpoint": endpoints.get(name, "(none)"),
                "fields": fields,
                "content_clock": sorted(set(fields) & CONTENT_CLOCK_NAMES),
            }
        )
    rows.sort(key=lambda r: r["mode"])
    return rows


# ---------------------------------------------------------------------------


def main_script() -> None:
    prime_app()
    board_1, vehicles_1, board_2, vehicles_2 = run_world()
    now = world.NOW

    rule("SETUP: the acceptance world (backend/tests/test_f03_boards.py)")
    print(f"  capture                       : {world._CAPTURE.relative_to(REPO)}")
    print(f"  aged group                    : {world.AGED_GROUP}, header {world.AGED_LAG_S:.0f} s "
          "before the poll clock, identical bytes on both polls")
    print(f"  healthy group                 : {world.HEALTHY_GROUP}, the same capture "
          f"{world.HEALTHY_LAG_S:.0f} s behind each poll, trips renamed '{world.HEALTHY_PREFIX}'")
    print("  other 6 groups                : valid header-only feeds, fresh")
    print(f"  clock                         : {now:.0f} (FROZEN_S of tests/e2e/fixtures/api.js)")
    print(f"  station under test            : {STATION_ID} ({board_1['station_name']})")

    # ---- (a) ---------------------------------------------------------------
    rule("(a) SERVED /api/subways : the content lag, which the fix leaves alone")
    lag = vehicles_1["fetched_at"] - vehicles_1["feed_timestamp"]
    print(f"  fetched_at - feed_timestamp   : {lag:.1f} s")
    check(
        lag == world.AGED_LAG_S,
        "the vehicles envelope exposes the injected 600 s of content lag (the audit measured "
        "599.7 s on a live clock)",
        f"{lag:.1f} s",
    )
    check(
        all(block["ok"] for block in vehicles_1["systems"].values())
        and len(vehicles_1["systems"]) == len(SUBWAY_FEED_URLS),
        "every one of the 8 groups reports ok while one serves 600 s old content (the trap)",
        f"{sum(1 for b in vehicles_1['systems'].values() if b['ok'])}/8 ok",
    )

    # ---- (b) ---------------------------------------------------------------
    rule(f"(b) SERVED /api/subway-arrivals/{STATION_ID} : two contributors, each dated")
    golden = json.loads(world.GOLDEN.read_text())
    check(
        board_1 == golden,
        "the served board is tests/e2e/fixtures/f03_board_219.json, the body the browser "
        "half renders",
    )
    print(f"  {'direction':<11} {'contributor':<8} {'route':<6} {'arrives in':>10} "
          f"{'observed':>10}  trip")
    for direction, rows in board_1["directions"].items():
        for row in rows:
            print(f"  {direction:<11} {contributor(row):<8} {row['route_id']:<6} "
                  f"{row['arrival'] - now:>9.0f}s {row['observed_at'] - now:>9.0f}s  "
                  f"{row['trip_id']}")
    for direction, rows in board_1["directions"].items():
        aged = [r for r in rows if contributor(r) == world.AGED_GROUP]
        healthy = [r for r in rows if contributor(r) == world.HEALTHY_GROUP]
        check(
            len(aged) == 3 and len(healthy) == 3,
            f"{direction}: three rows from each contributor share the board",
            f"{len(aged)} aged, {len(healthy)} healthy",
        )
        check(
            {r["observed_at"] for r in aged} == {now - world.AGED_LAG_S}
            and {r["observed_at"] for r in healthy} == {now - world.HEALTHY_LAG_S},
            f"{direction}: each row carries its own contributor's content clock",
        )
    systems = board_1["systems"]
    check(
        sorted(systems) == sorted([world.AGED_GROUP, world.HEALTHY_GROUP])
        and systems[world.HEALTHY_GROUP]["feed_timestamp"]
        - systems[world.AGED_GROUP]["feed_timestamp"]
        == world.AGED_LAG_S - world.HEALTHY_LAG_S,
        "systems names both contributors, each with its own clock, 595 s apart (6.1)",
        json.dumps({k: v["feed_timestamp"] - now for k, v in systems.items()}),
    )
    (prediction,) = [r for r in board_1["directions"]["Northbound"] if r["arrival"] == now + 120]
    check(
        contributor(prediction) == world.AGED_GROUP
        and prediction["arrival"] - prediction["observed_at"] == 720.0,
        "the audit's two-minute countdown is on the board, from a ten-minute-old header",
        f"arrives +120 s, observed {prediction['observed_at'] - now:.0f} s",
    )
    check(
        board_1["fetched_at"] == now and board_1["served_at"] == now,
        "the board's poll clock is fresh, which is why it could never be the rider's age",
        f"fetched_at {board_1['fetched_at'] - now:+.0f} s",
    )

    # ---- (c) ---------------------------------------------------------------
    fe_1, fe_2 = run_frontend([board_1, board_2])
    rule("(c) THE REAL FRONTEND on the served board, row by row : FIXED")
    rows_1 = [row for rows in board_1["directions"].values() for row in rows]
    expected = [QUALIFIER if contributor(r) == world.AGED_GROUP else "" for r in rows_1]
    popup = [row["qualifier"] for d in ("Northbound", "Southbound") for row in fe_1["popupRows"][d]]
    for served, shown, sentence in zip(rows_1, popup, fe_1["panelRows"]):
        print(f"  {contributor(served):<6} popup {shown or '(nothing)':<14}  panel {sentence}")
    check(
        popup == expected,
        "FIXED: the popup qualifies exactly the aged group's rows, 'as of 10m ago' beside each "
        "countdown (was: no qualifier on any row, feedAgeLine(body.fetched_at) returned '')",
        f"{sum(1 for q in popup if q)} of {len(popup)} rows qualified",
    )
    panel = [QUALIFIER if s.endswith(f", {QUALIFIER}") else "" for s in fe_1["panelRows"]]
    check(
        panel == expected and not any("as of" in s for s, e in zip(fe_1["panelRows"], expected) if not e),
        "FIXED: the panel qualifies the same rows in their sentences (was: ageSeconds 0.5 s, "
        "read as fresh, no line at all)",
        f"{sum(1 for q in panel if q)} of {len(panel)} rows qualified",
    )
    check(
        popup.count("") == 6 and panel.count("") == 6,
        "FIXED: the healthy contributor's six rows stay silent on both surfaces, so it remains "
        "distinguishable (was: nothing distinguished anything)",
    )
    spoken_rows = spoken_sentences(fe_1["spoken"], board_1["station_name"])
    check(
        spoken_rows == fe_1["panelRows"],
        "FIXED: the spoken board is the panel's sentences, row for row, so the caveat is "
        "spoken on the same six rows and on no other (was: none)",
        f"{sum(1 for s in spoken_rows if s.endswith(f', {QUALIFIER}'))} of {len(spoken_rows)} "
        "spoken sentences qualified",
    )
    check(
        "popup-stale" not in fe_1["popupHtml"] and fe_1["panelLines"] == [],
        "no board-wide line: each row speaks for its own age, so none repeats it",
    )
    helpers_src = (FRONTEND / "helpers.js").read_text()
    subway_src = (FRONTEND / "systems" / "subway.js").read_text()
    check(
        "payload.fetched_at == null ? null : now - payload.fetched_at" not in helpers_src,
        "FIXED: the panel no longer ages a board as now - fetched_at (was: helpers.js "
        "shapeStationArrivals' ageSeconds)",
    )
    check(
        "feedAgeLine(body.fetched_at, now)" not in subway_src
        and "function feedAgeLine" not in helpers_src,
        "FIXED: the popup no longer derives an age from fetched_at (was: subway.js calling "
        "feedAgeLine(body.fetched_at, now), the audit's cited helpers.js:713)",
    )

    # ---- (d) ---------------------------------------------------------------
    rule("(d) A SECOND SUCCESSFUL POLL OF THE SAME OLD BYTES, 15 s later")
    rows_2 = [row for rows in board_2["directions"].values() for row in rows]
    aged_ages = {board_2["served_at"] - r["observed_at"] for r in rows_2 if contributor(r) == world.AGED_GROUP}
    healthy_ages = {board_2["served_at"] - r["observed_at"] for r in rows_2 if contributor(r) == world.HEALTHY_GROUP}
    print(f"  aged rows' content age        : {sorted(aged_ages)} s")
    print(f"  healthy rows' content age     : {sorted(healthy_ages)} s")
    check(
        aged_ages == {world.AGED_LAG_S + world.REPOLL_S} and healthy_ages == {world.HEALTHY_LAG_S},
        "the aged rows' age grows with the clock while the healthy rows stay fresh",
    )
    check(
        all(block["ok"] for block in vehicles_2["systems"].values()),
        "every group still reports ok on the second poll",
    )
    expected_2 = [QUALIFIER if contributor(r) == world.AGED_GROUP else "" for r in rows_2]
    popup_2 = [row["qualifier"] for d in ("Northbound", "Southbound") for row in fe_2["popupRows"][d]]
    panel_2 = [QUALIFIER if s.endswith(f", {QUALIFIER}") else "" for s in fe_2["panelRows"]]
    check(
        popup_2 == expected_2 and panel_2 == expected_2,
        "FIXED, REPEATEDLY: the same old bytes returned again leave the same rows qualified "
        "on both surfaces (was: the arrivals clock advanced and nothing said so)",
        f"{sum(1 for q in popup_2 if q)} of {len(popup_2)}",
    )

    # ---- (e) ---------------------------------------------------------------
    rule("(e) EVERY ARRIVALS RESPONSE MODEL IN backend/models.py, PER MODE")
    table = arrivals_model_table()
    for row in table:
        verdict = ", ".join(row["content_clock"]) or "NONE"
        print(f"  {row['mode']:<20} {row['model']:<26} {row['endpoint']:<40} {verdict}")
    check(len(table) == 5, "five arrivals response models found", f"{len(table)}")
    check(
        all(row["content_clock"] for row in table),
        "all five arrivals models declare a content-age field (6.1; was: none of the five)",
    )

    rule("MEASURED SUMMARY")
    print(f"  injected header age                  : {world.AGED_LAG_S:.0f} s on {world.AGED_GROUP}")
    print(f"  lag on /api/subways                  : {lag:.0f} s")
    print(f"  rows at {STATION_ID}, qualified / served : popup {sum(1 for q in popup if q)}/"
          f"{len(popup)}, panel {sum(1 for q in panel if q)}/{len(panel)}")
    print(f"  healthy contributor's rows qualified : {sum(1 for q, e in zip(popup, expected) if q and not e)}")
    print()
    if failures:
        print("REALITY HAS CHANGED. Failed assertions:")
        for item in failures:
            print(f"  - {item}")
        print("DISPOSITION: NOT AS RECORDED, see the failed assertions above")
        raise SystemExit(1)
    print(
        "DISPOSITION: FIXED (claude/freshness-6-2-boards)  "
        "BEFORE: a valid subway feed 600 s behind its own header left /api/subway-arrivals/219 "
        "with a fresh fetched_at, 8/8 groups ok, a popup with no qualifier and a panel aged "
        "now - fetched_at, so a ten minute old prediction read as a live two minute countdown. "
        f"AFTER: the same world with a healthy group beside the aged one: {sum(1 for q in popup if q)} "
        f"of {len(popup)} rows at Prospect Av say '{QUALIFIER}' on the popup and the panel, "
        "exactly the aged group's, the healthy group's rows say nothing, and a second poll of "
        "the same old bytes keeps it so."
    )


if __name__ == "__main__":
    main_script()
