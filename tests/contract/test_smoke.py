"""The tier's own foundation test: the real app boots against the simulator.

Everything else in this directory assumes a real backend process really is polling
a real socket the test controls. This asserts exactly that, so a harness fault
fails HERE with an obvious message rather than as a puzzling assertion inside a
scenario about railroad staleness.
"""

from __future__ import annotations

import importlib.util

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
    # THE TIER'S HERMETICITY, half one. Every feed the simulator serves must
    # actually be asked for: the counts come from the sim, so a nonzero count IS
    # proof the app asked us rather than the internet. A feed left at zero means
    # either a seam that did not take effect (the app is off polling the real
    # upstream) or a source that stopped being polled at all.
    app = contract_app
    for key in sorted(app.sim.feeds):
        app.sim.await_polls(key, 1)


def test_the_simulator_points_at_every_url_seam_the_backend_declares():
    # THE TIER'S HERMETICITY, half two, and the half the test above cannot cover:
    # a source added WITHOUT a route here would simply be absent from sim.feeds,
    # so iterating sim.feeds can never notice it. This compares two lists that are
    # maintained independently, in different directories, for different reasons:
    # the backend's static seam roster and the env this simulator hands the app.
    # A new upstream that registers a seam and gets no simulator route fails here
    # with its own name, before it can quietly reach the internet in some scenario.
    seams = _load_env_seams()
    declared = {name for name in seams.SEAM_NAMES if name.endswith(("_URL", "_BASE"))}
    pointed = set(UpstreamSim().env("http://127.0.0.1:1", REPO_ROOT))
    assert declared - pointed == set(), "URL seams the simulator does not answer"
    # And the reverse, so a seam deleted from the backend does not leave a dead
    # entry here pretending to cover something.
    assert (pointed - declared) == {"DATA_DIR", "BUS_TIME_API_KEY"}


def test_the_static_archives_are_fetched_from_the_simulator(contract_app):
    app = contract_app
    app.await_status(
        lambda s: s["subway_static"] == "ready",
        "the subway static group to reach ready from a simulator archive",
    )
    assert app.sim.fetches("subway") >= 1
    assert app.status()["static_archives"]["subway"]["last_promoted_at"] is not None
