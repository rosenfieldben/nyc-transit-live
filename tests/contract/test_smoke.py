"""The tier's own foundation test: the real app boots against the simulator.

Everything else in this directory assumes a real backend process really is polling
a real socket the test controls. This asserts exactly that, so a harness fault
fails HERE with an obvious message rather than as a puzzling assertion inside a
scenario about railroad staleness.

HERMETICITY IS ASSERTED IN THREE LAYERS, because no one of them is sufficient:

  1. Every upstream the simulator serves is actually fetched (feeds and archives).
     Catches a seam that did not take effect, and a source that stopped polling.
  2. Every path the app requests resolves to a route (no 404s), and every URL seam
     the backend declares appears in the env the simulator hands over. The first
     half catches a seam pointed here with no route behind it; the second catches a
     seam nobody pointed here at all. Layer 1 sees neither, since it iterates the
     simulator's own roster.
  3. No backend module hardcodes an upstream URL. Catches the case neither of the
     others can: a source that never registers a seam at all, whose URL is written
     literally at its use site. Both other layers start from a declaration, so
     something with no declaration is invisible to both.
"""

from __future__ import annotations

import ast
import importlib.util
import re

from conftest import REPO_ROOT
from upstream_sim import UpstreamSim


def _load_env_seams():
    """Import backend/env_seams.py by path.

    By path rather than by putting backend/ on sys.path: this tier runs the backend
    as a SUBPROCESS on purpose, and importing its package into the test process
    would make it possible to accidentally assert against in-process state that the
    real app never sees. One module, one name list, no import graph.
    """
    spec = importlib.util.spec_from_file_location("_env_seams", REPO_ROOT / "backend/env_seams.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_app_boots_and_polls_the_simulator(contract_app):
    app = contract_app
    # Two polls of one subway group: proof the app is not merely up but running its
    # loop against the sim, and the first assertion any harness regression trips.
    app.sim.await_polls("subway:1-7+S", 2)

    body = app.get("/api/subways")
    assert body["data"], "the app served no trains from a healthy simulator feed"
    assert body["fetched_at"] is not None
    # The C2 per-system block, populated from a real poll rather than a fixture.
    assert set(body["systems"]) == {"1-7+S", "ACE", "BDFM", "G", "JZ", "NQRW", "L", "SIR"}


def test_every_upstream_the_app_polls_is_the_simulator(contract_app):
    # HERMETICITY, LAYER 1. Every feed AND every archive the simulator serves must
    # actually be asked for: the counts come from the sim, so a nonzero count IS
    # proof the app asked us rather than the internet. A zero means either a seam
    # that did not take effect (the app is off polling the real upstream) or a
    # source that stopped being polled at all.
    #
    # The archives are included because leaving them out hid a whole class of
    # mismatch: a loader's filename drifting from the route _resolve matches gives a
    # 404, which is a download failure the api scenarios do not look at, and the
    # symptom surfaced two tiers away as a browser assertion about marker opacity.
    app = contract_app
    for key in sorted(app.sim.feeds):
        app.sim.await_polls(key, 1)
    # await_FETCHED for the archives, not await_polls: a static loader downloads
    # once at warmup and then leaves the cache alone, so waiting for one MORE fetch
    # would time out against a perfectly healthy app.
    for key in sorted(app.sim.archives):
        app.sim.await_fetched(key)


def test_the_simulator_answers_every_path_the_app_asks_for(contract_app):
    # HERMETICITY, LAYER 2a: not just "a seam exists and is pointed here" but "this
    # simulator can answer what the app BUILDS from it". The name comparison below
    # cannot see that. A base seam whose route is missing, or a path scheme that
    # drifts (a filename change, a new query suffix), produces a 404 -- which the app
    # records as an ordinary download failure that no scenario inspects, and which
    # would leave that upstream silently uncovered for the rest of the tier.
    app = contract_app
    app.sim.await_polls("subway:1-7+S", 2)
    # The 404 check goes in a finally, so it REPLACES the wait's timeout rather than
    # never running. A missing route makes both fire, and the two messages are not
    # equally useful: "upstream mnr was fetched 0 times, the app may not be polling
    # it" sends the reader after the poll loop, while "the app asked for
    # /static/rail/gtfsmnr.zip and nothing routed it" names the actual defect.
    try:
        for key in sorted(app.sim.archives):
            app.sim.await_fetched(key)
    finally:
        assert app.sim.snapshot()["not_found"] == [], (
            "the app asked for paths this simulator does not route; each reached a "
            "404 instead of a controlled body: "
            f"{sorted(set(app.sim.snapshot()['not_found']))}"
        )


def test_the_simulator_points_at_every_url_seam_the_backend_declares():
    # HERMETICITY, LAYER 2b. Two lists maintained independently, in different
    # directories, for different reasons: the backend's static seam roster and the
    # env this simulator hands the app. Names only -- whether the routes behind them
    # answer is the test above.
    #
    # THE SPLIT IS AN EXPLICIT INVENTORY, NOT A NAMING CONVENTION. This used to
    # filter for names ending in _URL/_BASE, which nothing enforces -- env_seams
    # only checks membership in SEAM_NAMES, never the shape of the name -- so a
    # future AMTRAK_RT_ENDPOINT would have been dropped from both sides of the
    # comparison and sailed through. Listing the non-URL seams by hand means ANY new
    # seam name must be either pointed at the simulator or added below deliberately.
    non_url_seams = {
        "POLL_INTERVAL_S",
        "ALERT_POLL_INTERVAL_S",
        "FEED_RETENTION_MAX_S",
        "STATIC_RETRY_S",
        "STATIC_RETRY_SCHEDULE_S",
        "DATA_DIR",
    }
    seams = _load_env_seams()
    pointed = set(UpstreamSim().env("http://127.0.0.1:1", REPO_ROOT))
    # CREDENTIALS ARE NOT SEAMS, and the distinction is load-bearing rather than
    # bookkeeping: a seam is something env_seams.assert_unset REFUSES to let the
    # contract monitor run with, because a redirected upstream would have it check
    # the simulator against itself. The monitor needs these three SET to check the
    # real upstreams, so they are read as plain environment variables and must be
    # subtracted here. The simulator sets them because the bus feed refuses to
    # fetch without a key, and because the default contract app is a
    # CREDENTIALED NJT deployment (the not-configured scenario empties them).
    credentials = {"BUS_TIME_API_KEY", "NJT_USERNAME", "NJT_PASSWORD"}
    assert set(seams.SEAM_NAMES) == (pointed - {"DATA_DIR"} - credentials) | non_url_seams, (
        "every seam must be pointed at the simulator or listed as a non-URL seam"
    )


# Literal URLs a backend module may carry without being an upstream the app fetches
# from. Each would need a reason, and the reason has to be that no request is made
# to it. EMPTY TODAY, and deliberately so: the one candidate (PATH's User-Agent) is
# embedded mid-string as "nyc-transit-live (+https://github.com/...)", which the
# scanner never considers because the literal does not START with a scheme. An
# allowlist entry that matches nothing documents a protection that is not operating.
_ALLOWED_LITERAL_URLS: set[str] = set()


def test_no_backend_module_hardcodes_an_upstream_url():
    """HERMETICITY, LAYER 3: the hole the other two structurally cannot see.

    Layer 1 iterates the simulator's roster and layer 2 iterates the backend's seam
    roster, so both begin from a declaration. A source whose URL is written literally
    at its use site -- `AMTRAK_RT_URL = "https://api.amtrak.com/gtfsrt"` in a new
    feeds module -- appears in neither roster, every existing test stays green, and
    the contract tier silently starts polling a third party on every CI run.

    So this reads the source. Every URL-shaped string literal in every shipped
    backend module must FLOW INTO an env_seams.url() call -- directly as its default
    argument, or through a module-level constant that is passed as one (the railroad
    and alerts feeds share a base that way) -- or be listed above with a reason. A
    seam existing is what layer 2 then converts into "the simulator must answer it".
    Tests and scripts are excluded: they are not the running app.
    """
    url_re = re.compile(r"^https?://")
    modules = [
        path
        for path in sorted((REPO_ROOT / "backend").rglob("*.py"))
        if not {"tests", "scripts"} & set(path.relative_to(REPO_ROOT).parts)
        and path.name != "env_seams.py"  # the seam helper itself declares no upstream
    ]
    trees = {path: ast.parse(path.read_text(encoding="utf-8")) for path in modules}

    # A literal is a seam default if it reaches env_seams.url(), OR if it is the
    # default of an os.getenv() whose variable is in SEAM_NAMES. The second form is
    # not a loophole: PATH_RT_URL and FERRY_RT_BASE predate the seam helper and read
    # their env var directly, which env_seams.SEAM_NAMES documents. Requiring the
    # NAME to be a declared seam is what keeps this honest -- a bare os.getenv with
    # an undeclared name is still an offender, and so is a plain assignment.
    declared_seams = set(_load_env_seams().SEAM_NAMES)

    def _is_seam_call(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "url":
            return True
        if name != "getenv":
            return False
        first = node.args[0] if node.args else None
        return isinstance(first, ast.Constant) and first.value in declared_seams

    # Pass 1, across every module: which literals sit in a seam call, and which
    # NAMES are handed to one as a default (making their assignment legitimate too).
    allowed_nodes: set[int] = set()
    seam_default_names: set[str] = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if not _is_seam_call(node):
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant):
                    allowed_nodes.add(id(arg))
                elif isinstance(arg, ast.Name):
                    seam_default_names.add(arg.id)

    # Pass 2: assigning a URL literal to one of those names is a seam default stated
    # once and reused, not a hardcoded upstream.
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                if any(
                    isinstance(target, ast.Name) and target.id in seam_default_names
                    for target in node.targets
                ):
                    allowed_nodes.add(id(node.value))

    offenders: list[str] = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if not url_re.match(node.value):
                continue
            if id(node) in allowed_nodes or node.value in _ALLOWED_LITERAL_URLS:
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {node.value}")
    assert not offenders, (
        "backend modules must not hardcode upstream URLs; route them through "
        "env_seams so the contract tier can point them at the simulator:\n" + "\n".join(offenders)
    )


def test_the_static_archives_are_fetched_from_the_simulator(contract_app):
    app = contract_app
    app.await_status(
        lambda s: s["subway_static"] == "ready",
        "the subway static group to reach ready from a simulator archive",
    )
    assert app.sim.fetches("subway") >= 1
    assert app.status()["static_archives"]["subway"]["last_promoted_at"] is not None


def test_the_bus_feed_is_actually_exercised(contract_app):
    """The bus layer must not be silently empty.

    It was: the simulator served the subway capture on the bus feed, and the subway
    capture carries no vehicle POSITIONS (correct for NYCT), so the decoder skipped
    every entity. /api/buses returned an empty list forever with every liveness
    signal green -- a permanent, unasserted instance of the exact silent-failure
    shape this tier exists to catch. Pinned here so it cannot come back.
    """
    app = contract_app
    app.sim.await_polls("buses", 1)
    app.await_status(
        lambda _s: bool(app.get("/api/buses")["data"]),
        "the bus feed to place at least one vehicle",
    )
    # EVERY vehicle in the capture, not merely "some". A latitude-box assertion would
    # be a tautology -- the decoder drops anything outside that box before serving,
    # so it can never fail -- and the interesting regression is the decoder silently
    # dropping vehicles, which only a count can see. 28 is what ferry_vp_a.pb holds;
    # if the fixture is ever regenerated this number moves with it, deliberately, so
    # that a shrinking bus layer cannot pass unnoticed.
    buses = app.get("/api/buses")["data"]
    assert len(buses) == 28, f"every vehicle in the capture should be placed, got {len(buses)}"
    assert all(b["latitude"] and b["longitude"] for b in buses)
