"""The contract tier's harness: a real backend process against a controlled sim.

RUN IT:  pytest tests/contract          (from the repo root, NOT from backend/)

The hermetic suites are unaffected and remain the fast tier. `cd backend && pytest`
collects only backend/tests; this directory is collected only when named.

THE DETERMINISM RULES this suite holds itself to, stated in full with their
rationale in tests/contract/README.md, in one line each here:

1. Every wait is a poll-until-predicate on an observable, with a hard deadline.
2. No fixed sleeps as synchronization.
3. Zero retries in CI.
4. One backend process per scenario (the `contract_app` fixture is function-scoped;
   the browser tier cannot do this and pays for it with an explicit afterEach).
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upstream_sim import UpstreamSim  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Compressed cadences. The budget for this whole tier is under four minutes in CI,
# and these are what make that reachable: a scenario that has to outlive the
# retention window waits 20 seconds instead of 10 minutes, and a static retry that
# has to heal walks 1s/2s/3s instead of 15s/30s/60s/300s.
#
# POLL_INTERVAL_S is 2 rather than lower on purpose. Below about a second the poll
# loop and the assertions start racing in a way that produces flakes, and rule 3
# says a flake is a bug; two seconds is comfortably fast and comfortably stable.
CONTRACT_TIMING = {
    "POLL_INTERVAL_S": "2",
    "ALERT_POLL_INTERVAL_S": "2",
    "FEED_RETENTION_MAX_S": "20",
    "STATIC_RETRY_S": "3",
    "STATIC_RETRY_SCHEDULE_S": "1,2,3",
}

BOOT_DEADLINE_S = 60.0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ContractApp:
    """A running backend, plus the ways a scenario observes it."""

    def __init__(self, base_url: str, sim: UpstreamSim, process: subprocess.Popen) -> None:
        self.base_url = base_url
        self.sim = sim
        self.process = process

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=10) as resp:
            return json.loads(resp.read())

    def status(self) -> dict:
        return self.get("/api/status")

    def await_status(
        self, predicate: Callable[[dict], bool], what: str, deadline_s: float = 60.0
    ) -> dict:
        """Poll /api/status until `predicate` holds. `what` is quoted in the
        failure, because a bare timeout in a tier like this is nearly useless."""
        end = time.monotonic() + deadline_s
        last: dict = {}
        while time.monotonic() < end:
            try:
                last = self.status()
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.05)
                continue
            if predicate(last):
                return last
            time.sleep(0.05)
        raise AssertionError(
            f"timed out after {deadline_s}s waiting for: {what}\nlast /api/status: "
            f"{json.dumps(last, indent=2)[:4000]}"
        )

    def await_railroads(
        self, predicate: Callable[[dict], bool], what: str, deadline_s: float = 60.0
    ) -> dict:
        """Poll /api/railroads until `predicate` holds. The railroad per-system
        block lives on the live envelope rather than on /api/status, because that
        is where the CLIENT reads it, and a contract test should watch what the
        rider-facing surface says."""
        end = time.monotonic() + deadline_s
        last: dict = {}
        while time.monotonic() < end:
            last = self.get("/api/railroads")
            if predicate(last):
                return last
            time.sleep(0.05)
        raise AssertionError(
            f"timed out after {deadline_s}s waiting for: {what}\nlast /api/railroads systems: "
            f"{json.dumps(last.get('systems'), indent=2)}"
        )


def _wait_for_boot(base_url: str, process: subprocess.Popen) -> None:
    end = time.monotonic() + BOOT_DEADLINE_S
    while time.monotonic() < end:
        if process.poll() is not None:
            raise AssertionError(
                f"backend exited during boot with code {process.returncode}:\n"
                f"{(process.stderr.read() if process.stderr else b'').decode()[-4000:]}"
            )
        try:
            # /api/status, not /healthz: status is always 200 and answers as soon
            # as the app is serving, while healthz is 503 until a feed is fresh,
            # which is a LATER state and one some scenarios never want to reach.
            with urllib.request.urlopen(f"{base_url}/api/status", timeout=2):
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.1)
    raise AssertionError(f"backend did not answer /api/status within {BOOT_DEADLINE_S}s")


class ContractHarness:
    """One simulator and one data directory, across as many app lifetimes as a
    scenario needs.

    THE RESTART IS NOT A CONVENIENCE. A static archive is only re-downloaded when
    the cached copy is missing or older than MAX_AGE_DAYS, and MAX_AGE_DAYS is
    deliberately not a PR 1 seam. So "upstream publishes garbage over a good
    cache" cannot be reached inside one process lifetime, and faking it would mean
    inventing a seam mid-PR, which the spec forbids. What CAN be done with the
    seams that exist: run the app once against a good publication so a real archive
    lands in DATA_DIR, age that file, then run the app again while upstream serves
    garbage. The second boot re-downloads for real, rejects for real, and falls
    back to the archive the first boot wrote. That is the scenario, expressed
    rather than approximated.
    """

    def __init__(self, sim: UpstreamSim, base: str, data_dir: Path) -> None:
        self.sim = sim
        self.base = base
        self.data_dir = data_dir
        self._running: list[subprocess.Popen] = []

    @contextlib.contextmanager
    def launch(self, **env_overrides: str):
        port = _free_port()
        env = {
            **os.environ,
            **self.sim.env(self.base, self.data_dir),
            **CONTRACT_TIMING,
            **env_overrides,
        }
        env.pop("PYTHONPATH", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--app-dir",
                "backend",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._running.append(process)
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_for_boot(base_url, process)
            yield ContractApp(base_url, self.sim, process)
        finally:
            _terminate(process)
            self._running.remove(process)

    def age_archives(self, days: float = 60.0) -> None:
        """Backdate every cached archive so the next boot treats it as stale and
        re-downloads. The only way to reach the refresh path without a seam for
        MAX_AGE_DAYS, and it is exactly what the passage of time would do."""
        old = time.time() - days * 86400
        for archive in (self.data_dir / "gtfs_static").glob("*.zip"):
            os.utime(archive, (old, old))

    def shutdown(self) -> None:
        for process in list(self._running):
            _terminate(process)
        self._running.clear()


def _terminate(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.fixture
def harness(tmp_path):
    """A simulator and a tmp data root, with no app running yet.

    DATA_DIR being a tmp directory per scenario is what makes a cold start
    expressible at all: against the repo's own data/ directory the loaders would
    find a valid cached archive, serve it, and never download.
    """
    sim = UpstreamSim()
    base = sim.start()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    harness = ContractHarness(sim, base, data_dir)
    try:
        yield harness
    finally:
        harness.shutdown()
        sim.stop()


@pytest.fixture
def contract_app(harness):
    """The common case: one healthy app against a healthy simulator."""
    with harness.launch() as app:
        yield app
