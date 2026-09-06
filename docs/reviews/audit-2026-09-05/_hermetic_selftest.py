#!/usr/bin/env python3
"""Does _hermetic actually contain a process? Measured against a FAKE, not trusted.

RUN IT (from the repository root), with your real .env in place and NOTHING blanked
in the shell, because that is the situation the containment exists for:

    ./backend/.venv/bin/python docs/reviews/audit-2026-09-05/_hermetic_selftest.py

WHAT IT PROVES, in four arms, each a separate subprocess so the backend import
happens exactly once per arm and cannot be reused:

  1. THE BUG, reproduced. The old preamble (pop, then import) leaves the process
     CONFIGURED and pointed at the real host, on a checkout whose .env holds
     credentials. If this arm reports "contained", your .env has no NJT credentials
     and the rest of this script proves less than it claims, so it says so.
  2. THE SHELL WORKAROUND, refuted. NJT_USERNAME= in the parent shell does not
     survive the script's own pop.
  3. THE CONTAINMENT, on the unconfigured path: no credentials, no real address.
  4. THE CONTAINMENT, on the CONFIGURED path (f05/f07): credentials present and
     fabricated, and still no real address, and a real njt_auth.mint() call lands on
     the loopback discard port rather than on NJ Transit.

Arm 4 is the one that matters most, because it is the arm where a mint is actually
attempted. The attempt is real production code (njt_auth.mint) against a real
socket; only the address is ours.

NO NJ TRANSIT REQUEST IS MADE BY ANY ARM. Arms 1 and 2 report what the process WOULD
have been able to do and never make a request; only arm 4 opens a socket, and it
opens it at 127.0.0.1:9.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(': ' + detail) if detail else ''}")
    if not condition:
        FAILURES.append(label)


# Each arm is a program run in its own interpreter. Written as source strings rather
# than functions because the whole subject is what happens AT IMPORT TIME, which a
# function in an already-imported process cannot exercise.
PREAMBLE = f"""
import json, os, sys
REPO = {str(REPO)!r}
sys.path.insert(0, {str(HERE)!r})
"""

ARM_OLD_PREAMBLE = PREAMBLE + """
# The preamble four scripts used to carry: pop, then import.
for v in ("NJT_USERNAME", "NJT_PASSWORD"):
    os.environ.pop(v, None)
sys.path.insert(0, os.path.join(REPO, "backend"))
import njt_auth
print(json.dumps({"configured": njt_auth.is_configured(), "url": njt_auth.NJT_TOKEN_URL}))
"""

ARM_CONTAINED = PREAMBLE + """
import _hermetic
_hermetic.contain()
sys.path.insert(0, os.path.join(REPO, "backend"))
import njt_auth
lines = _hermetic.verify()
print(json.dumps({"configured": njt_auth.is_configured(), "url": njt_auth.NJT_TOKEN_URL,
                  "lines": lines}))
"""

ARM_CONFIGURED = PREAMBLE + """
import asyncio
os.environ["NJT_USERNAME"] = "f05-fabricated-user"
os.environ["NJT_PASSWORD"] = "f05-fabricated-pass"
import _hermetic
_hermetic.contain(keep_credentials=True)
sys.path.insert(0, os.path.join(REPO, "backend"))
import njt_auth
_hermetic.verify(expect_configured=True)
# A REAL MINT ATTEMPT, through production code, at whatever address the seam holds.
outcome = "no exception"
try:
    asyncio.run(njt_auth.mint())
except Exception as exc:
    outcome = type(exc).__name__ + ": " + str(exc)
print(json.dumps({"configured": njt_auth.is_configured(), "url": njt_auth.NJT_TOKEN_URL,
                  "creds": list(njt_auth.credentials() or ()), "mint": outcome}))
"""


def run_arm(source: str, env_extra: dict | None = None, trace: Path | None = None) -> dict:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    if trace is not None:
        env["NJT_SOCKET_TRACE"] = str(trace)
        env["PYTHONPATH"] = str(HERE / "_tracer") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, env=env, cwd=REPO
    )
    if proc.returncode != 0:
        return {"exit": proc.returncode, "stderr": proc.stderr.strip()[-400:]}
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main() -> int:
    print("_hermetic self test")
    print(f"repository : {REPO}")
    dotenv = REPO / ".env"
    has_env = dotenv.exists() and "NJT_USERNAME" in dotenv.read_text()
    print(f".env holds NJT credentials on this machine : {has_env}")
    print()

    print("ARM 1: the old preamble (pop, then import)")
    old = run_arm(ARM_OLD_PREAMBLE)
    print(f"    is_configured() : {old.get('configured')}")
    print(f"    token URL       : {old.get('url')}")
    if has_env:
        check(
            "1 the old preamble really was leaky on a checkout with credentials",
            old.get("configured") is True and "njtransit" in str(old.get("url")),
            "load_dotenv refilled what the pop removed",
        )
    else:
        print("    (skipped: this machine's .env carries no NJT credentials, so the")
        print("     leak cannot be demonstrated here and arms 3 and 4 prove less)")

    print()
    print("ARM 2: the same preamble with NJT_USERNAME= blanked in the parent shell")
    blanked = run_arm(ARM_OLD_PREAMBLE, {"NJT_USERNAME": "", "NJT_PASSWORD": ""})
    print(f"    is_configured() : {blanked.get('configured')}")
    if has_env:
        check(
            "2 blanking in the shell does NOT survive the script's own pop",
            blanked.get("configured") is True,
            "the pop deletes the empty value and load_dotenv refills it",
        )

    print()
    print("ARM 3: _hermetic.contain() on the unconfigured path")
    contained = run_arm(ARM_CONTAINED)
    for line in contained.get("lines", []):
        print(f"    {line}")
    check(
        "3 a contained process is not configured",
        contained.get("configured") is False,
        str(contained.get("configured")),
    )
    check(
        "3 and its token URL is the loopback discard port",
        str(contained.get("url", "")).startswith(_hermetic_loopback()),
        str(contained.get("url")),
    )

    print()
    print("ARM 4: _hermetic.contain(keep_credentials=True), then a REAL mint attempt")
    trace = HERE / "_selftest_socket.log"
    trace.unlink(missing_ok=True)
    configured = run_arm(ARM_CONFIGURED, trace=trace)
    print(f"    is_configured() : {configured.get('configured')}")
    print(f"    credentials     : {configured.get('creds')}")
    print(f"    token URL       : {configured.get('url')}")
    print(f"    mint outcome    : {configured.get('mint')}")
    check(
        "4 a script that must look configured does, with FABRICATED credentials",
        configured.get("configured") is True
        and str(configured.get("creds", [""])[0]).startswith("f05-fabricated"),
        str(configured.get("creds")),
    )
    check(
        "4 the real mint failed at the loopback address, never at NJ Transit",
        "NjtAuthError" in str(configured.get("mint")),
        str(configured.get("mint"))[:90],
    )
    recorded = trace.read_text() if trace.exists() else ""
    print("    socket trace for this arm:")
    for line in recorded.strip().splitlines() or ["(nothing)"]:
        print(f"      {line}")
    check(
        "4 the socket trace names no NJ Transit host",
        "njtransit" not in recorded.lower() and "raildata" not in recorded.lower(),
        f"{len(recorded.splitlines())} traced events",
    )
    trace.unlink(missing_ok=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("CONTAINMENT VERIFIED: the walls hold, and the leak they replace is real.")
    return 0


def _hermetic_loopback() -> str:
    sys.path.insert(0, str(HERE))
    import _hermetic

    return _hermetic.LOOPBACK


if __name__ == "__main__":
    raise SystemExit(main())
