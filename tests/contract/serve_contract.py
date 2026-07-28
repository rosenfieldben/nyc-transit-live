"""Launch the simulator and the real backend together, for the browser tier.

Playwright's webServer takes ONE command, and the contract page needs two
processes: the simulator the app polls, and the app itself. This is that command.

Both ports are fixed rather than ephemeral, because a browser spec has no way to
be handed a port at runtime the way a pytest fixture is. The specs drive the
simulator over its HTTP control endpoint at CONTRACT_SIM_PORT, which is the reason
that endpoint is HTTP rather than a Python API.

DATA_DIR is a fresh temp directory per launch, so the browser tier gets the same
cold start the api tier does and never writes into the repo's own data/.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import CONTRACT_TIMING  # noqa: E402
from upstream_sim import UpstreamSim  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

APP_PORT = int(os.environ.get("CONTRACT_PORT", "5174"))
SIM_PORT = int(os.environ.get("CONTRACT_SIM_PORT", "5175"))

# How long a terminated backend gets to exit before it is killed. Generous enough
# for uvicorn's graceful shutdown, short enough that a wedged one cannot hold
# APP_PORT against the next run.
SHUTDOWN_GRACE_S = 10.0


def main() -> int:
    sim = UpstreamSim()
    base = sim.start(port=SIM_PORT)
    data_dir = Path(tempfile.mkdtemp(prefix="c6-contract-"))
    # The api tier's 20s retention cap exists so a pytest scenario can watch a
    # window expire in seconds. The browser tier wants the opposite: its claims are
    # about markers that are STILL ON THE MAP and dimmed, and a 20s cap would drop
    # them off before the page's 25s staleness threshold could dim them, leaving
    # assertions to pass against an empty marker set. Nothing here asserts the cap,
    # so it is raised past the length of the whole run.
    env = {
        **os.environ,
        **sim.env(base, data_dir),
        **CONTRACT_TIMING,
        "FEED_RETENTION_MAX_S": "600",
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
            str(APP_PORT),
            "--log-level",
            "warning",
        ],
        cwd=REPO_ROOT,
        env=env,
    )

    # Forward the shutdown signal to uvicorn instead of letting this process die
    # alone. Without this a terminated runner leaves an orphaned backend holding
    # APP_PORT, and the NEXT run finds something answering there, attaches to it,
    # and fails on a simulator that is no longer running.
    #
    # THE HANDLER DOES THE MINIMUM: ask, record when, return. It must not wait, and
    # the reason is not style. This handler runs on the main thread, which is
    # already inside Popen's wait holding the waitpid lock, so a nested timed wait
    # can never reap the child; it polls for a lock it already owns, times out every
    # time, and escalates to SIGKILL even for a child that exited instantly.
    # Measured: with a child that exits promptly on SIGTERM, a nested wait(timeout=3)
    # still took the SIGKILL path after the full 3 seconds. All waiting therefore
    # happens on the main flow below, where the escalation is both reachable and
    # bounded -- which is what the state that matters needs. A uvicorn whose event
    # loop is blocked never processes SIGTERM at all (its handler is installed ON
    # that loop), and something has to stop holding the two fixed ports.
    stop_requested_at: list[float] = []

    def _relay(_signum, _frame):
        if not stop_requested_at:
            stop_requested_at.append(time.monotonic())
        process.terminate()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _relay)

    try:
        while True:
            code = process.poll()
            if code is not None:
                return code
            if stop_requested_at and time.monotonic() - stop_requested_at[0] > SHUTDOWN_GRACE_S:
                process.kill()
                return process.wait()
            time.sleep(0.05)
    finally:
        sim.stop()


if __name__ == "__main__":
    raise SystemExit(main())
