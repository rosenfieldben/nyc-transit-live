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
    """Ask the OS for a free port, then let go of it so uvicorn can bind it.

    There is a window between the close here and uvicorn's bind in launch(), and it
    is accepted rather than closed because losing the race is LOUD, not silent:
    uvicorn exits non-zero, _wait_for_boot's `process.poll() is not None` branch
    fires, and the failure carries the exit code and uvicorn's own stderr. Stated
    because everything else in this file documents its timing decision, and an
    undocumented one invites a future reader to "fix" it with a retry loop.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ContractApp:
    """A running backend, plus the ways a scenario observes it."""

    def __init__(
        self, base_url: str, sim: UpstreamSim, process: subprocess.Popen, log_path: Path
    ) -> None:
        self.base_url = base_url
        self.sim = sim
        self.process = process
        self.log_path = log_path

    def log(self, limit: int = 4000) -> str:
        """The backend's own output, for a scenario that wants to say why it failed."""
        return _tail(self.log_path, limit)

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=10) as resp:
            return json.loads(resp.read())

    def status(self) -> dict:
        return self.get("/api/status")

    def _await(
        self,
        read: Callable[[], dict],
        predicate: Callable[[dict], bool],
        what: str,
        deadline_s: float,
        describe: Callable[[dict], str],
    ) -> dict:
        """Poll until `predicate` holds, or fail saying what it was waiting for.

        THE PREDICATE RUNS INSIDE THE RETRY GUARD, not outside it, and that is the
        whole reason this helper exists instead of two hand-written loops. Several
        scenarios pass predicates that make their own request (`app.get("/api/alerts")`
        inside the lambda), and the live endpoints answer 502/503 while their cache
        is still warming or while an upstream error is recorded -- which urllib
        raises as HTTPError. With the predicate outside the guard, the very
        transient these waits exist to ride out instead aborted the run with a bare
        `HTTP Error 503` and none of the diagnostic this tier is built around.
        """
        end = time.monotonic() + deadline_s
        last: dict = {}
        last_error: BaseException | None = None
        while time.monotonic() < end:
            try:
                last = read()
                if predicate(last):
                    return last
                last_error = None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                # HTTPError is a URLError subclass, so a warming or erroring endpoint
                # lands here. It is REMEMBERED rather than only swallowed: an
                # endpoint stuck at 503 for the whole deadline is the likeliest
                # cause of a timeout, and reporting only "timed out waiting for X"
                # while knowing the response was 503 the entire time would point the
                # reader at the wrong thing. Anything that is NOT a network error --
                # a KeyError or TypeError from the predicate itself -- is deliberately
                # not caught, so a broken predicate fails immediately and loudly.
                last_error = exc
            time.sleep(0.05)
        reason = f"\nlast response error: {last_error!r}" if last_error is not None else ""
        raise AssertionError(
            f"timed out after {deadline_s}s waiting for: {what}{reason}\n{describe(last)}"
        )

    def await_status(
        self, predicate: Callable[[dict], bool], what: str, deadline_s: float = 60.0
    ) -> dict:
        """Poll /api/status until `predicate` holds. `what` is quoted in the
        failure, because a bare timeout in a tier like this is nearly useless."""
        return self._await(
            self.status,
            predicate,
            what,
            deadline_s,
            lambda last: f"last /api/status: {json.dumps(last, indent=2)[:4000]}",
        )

    def await_railroads(
        self, predicate: Callable[[dict], bool], what: str, deadline_s: float = 60.0
    ) -> dict:
        """Poll /api/railroads until `predicate` holds. The railroad per-system
        block lives on the live envelope rather than on /api/status, because that
        is where the CLIENT reads it, and a contract test should watch what the
        rider-facing surface says."""
        return self._await(
            lambda: self.get("/api/railroads"),
            predicate,
            what,
            deadline_s,
            lambda last: (
                f"last /api/railroads systems: {json.dumps(last.get('systems'), indent=2)}"
            ),
        )


def _tail(log_path: Path, limit: int = 4000) -> str:
    try:
        return log_path.read_text(errors="replace")[-limit:]
    except OSError:
        return "(no log)"


def _wait_for_boot(base_url: str, process: subprocess.Popen, log_path: Path) -> None:
    end = time.monotonic() + BOOT_DEADLINE_S
    while time.monotonic() < end:
        if process.poll() is not None:
            raise AssertionError(
                f"backend exited during boot with code {process.returncode}:\n{_tail(log_path)}"
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
        # THE BACKEND'S OUTPUT GOES TO A FILE, NEVER TO A PIPE, and this is a
        # correctness matter rather than a style one. A pipe nothing reads is a
        # 64 KiB budget on how much the child may log, and the backend logs a
        # WARNING per failed poll at the compressed 2s cadence: with the alert
        # feeds down that is roughly 800 B/s, so the buffer fills around 70
        # seconds in. The child then blocks forever inside write(2) ON THE EVENT
        # LOOP THREAD -- it stops serving, stops polling, and stops responding to
        # SIGTERM, because uvicorn's signal handling runs on the loop that is
        # blocked. The visible result is a scenario that times out with a
        # plausible-looking message describing the wrong failure. Nothing here
        # ever read those pipes, so the buffering bought nothing; a file has no
        # such limit and _tail still gives failures the log they need.
        log_path = self.data_dir / f"backend-{port}.log"
        log = log_path.open("wb")
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
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()  # the child holds its own descriptor
        self._running.append(process)
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_for_boot(base_url, process, log_path)
            yield ContractApp(base_url, self.sim, process, log_path)
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
        # Every process gets signalled even if an earlier one misbehaves. Without
        # the guard, a TimeoutExpired escaping _terminate -- or a second Ctrl-C
        # landing inside its 10s wait -- aborted this loop, leaving the remaining
        # backends alive and skipping the caller's sim.stop() entirely.
        errors: list[BaseException] = []
        for process in list(self._running):
            try:
                _terminate(process)
            except BaseException as exc:  # noqa: BLE001 - re-raised below
                errors.append(exc)
        self._running.clear()
        if errors:
            raise errors[0]


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
        # sim.stop() in its own finally: a shutdown that raises must not leave the
        # simulator thread running and its port held for the next scenario.
        try:
            harness.shutdown()
        finally:
            sim.stop()


@pytest.fixture
def contract_app(harness):
    """The common case: one healthy app against a healthy simulator."""
    with harness.launch() as app:
        yield app
