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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import CONTRACT_TIMING  # noqa: E402
from upstream_sim import UpstreamSim  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

APP_PORT = int(os.environ.get("CONTRACT_PORT", "5174"))
SIM_PORT = int(os.environ.get("CONTRACT_SIM_PORT", "5175"))


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
    # and fails on a simulator that is no longer running. That is a confusing
    # failure to debug and it is entirely avoidable here.
    # THE ESCALATION LIVES IN THE HANDLER, not in a finally. An earlier shape put
    # `return process.wait()` in a try and the terminate/kill ladder in the finally,
    # where it was unreachable: wait() only returns once the child is gone, so
    # poll() was never None there. The one state that matters is a uvicorn whose
    # event loop is blocked -- its SIGTERM handler is installed ON that loop, so the
    # signal is simply never processed -- and in that state the old code would have
    # waited forever, holding both fixed ports and never reaching sim.stop().
    def _relay(_signum, _frame):
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _relay)

    try:
        return process.wait()
    finally:
        sim.stop()


if __name__ == "__main__":
    raise SystemExit(main())
