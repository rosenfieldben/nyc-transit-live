"""Environment overrides for upstream endpoints and timing constants (C6).

WHY THESE EXIST. The hermetic suites are deliberately blind to one thing: they
never run the real backend process against a controlled upstream. pytest injects
clients, Playwright serves mock.js stubs, and each layer can be locally correct
while the rider-facing composite lies, which is the third audit's closing
diagnosis. The C6 contract tier closes that gap by launching the real app against
a simulator, and to do that it has to be able to point every outbound fetch at
the simulator and compress every cadence so a scenario finishes in seconds
instead of minutes. These are the seams that make that possible.

EVERY DEFAULT IS THE PRIOR LITERAL, byte for byte. Setting nothing changes
nothing, and tests/test_env_seams.py pins each default BY VALUE against a
hardcoded table, so editing a default in passing fails a named test rather than
quietly shifting production.

Not only a test seam: pointing an upstream at a mirror is an ordinary operational
lever, which is why PATH_RT_URL (13b) and FERRY_RT_BASE (R3) already existed in
this shape. Those two keep their bare os.getenv calls and are deliberately not
routed through here; converting them would be a behavior-neutral churn in code
two audits have already reviewed.

WHY A HELPER RATHER THAN BARE os.getenv AT EACH SITE. load_dotenv runs inside
feeds/shared.py, so a module-level os.getenv only sees .env values if something
pulled feeds.shared in first. Measured: importing static_data, railroad_static,
path_static, ferry_static or bus_static on its own does NOT load it. A bare
os.getenv in those modules would therefore honor a real environment variable but
silently ignore the same name in .env, which is exactly the kind of
works-depending-on-import-order behavior this project keeps having to remove.
Reading through here guarantees dotenv has loaded first, wherever the seam sits.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The same .env feeds/shared.py loads. load_dotenv does not override variables
# already in the real environment and is safe to call twice, so whichever module
# imports first wins the race harmlessly and the other call is a no-op.
load_dotenv(PROJECT_ROOT / ".env")

# name -> the default this process would use with nothing set. Recorded as the
# seams are declared, so the inertness test can assert the WHOLE set against its
# own hardcoded table: a default that drifts fails, and so does a seam added
# without being pinned. The registry is deliberately not the test's source of
# truth (a test that asks the code what it should be cannot notice the code
# changing); it is only the thing the hardcoded table is compared against.
DEFAULTS: dict[str, object] = {}


def _record(name: str, default: object) -> None:
    if name in DEFAULTS and DEFAULTS[name] != default:
        raise RuntimeError(f"env seam {name} declared twice with different defaults")
    DEFAULTS[name] = default


def url(name: str, default: str) -> str:
    """An upstream endpoint override. Trailing slashes are stripped so a base is
    safe to concatenate with a suffix, matching FERRY_RT_BASE's .rstrip("/")."""
    _record(name, default)
    return os.getenv(name, default).rstrip("/")


def seconds(name: str, default: float) -> float:
    """A cadence, retention window, or deadline in seconds.

    A malformed value is a configuration error worth failing loudly on: these are
    set by an operator or a test harness, never by a user, and silently falling
    back to the default would leave a contract scenario timing out with no clue
    why.
    """
    _record(name, default)
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def seconds_tuple(name: str, default: tuple[float, ...]) -> tuple[float, ...]:
    """A schedule of seconds, comma separated (the static retry rungs).

    An empty string yields an empty tuple, which the warmup reads as "no schedule"
    and falls back to the steady-state interval; that is an existing documented
    branch (warmups._rung), not a new one.
    """
    _record(name, default)
    raw = os.getenv(name)
    if raw is None:
        return default
    return tuple(float(part) for part in raw.split(",") if part.strip())


def directory(name: str, default_relative: str) -> Path:
    """A filesystem root the process reads and writes under.

    NEEDED BY THE CONTRACT TIER FOR A REASON THE URL SEAMS DO NOT COVER. Redirecting
    an archive's URL is not enough to test a cold start: every static loader caches
    under the repo's data/ directory, so a run in a checkout that already holds a
    valid archive would serve the cache and never download at all, and the
    finding-4 cold-start scenario (no cache, a headers-only publish, never ready)
    could not be expressed. It would also write the simulator's archives over a
    developer's real ones. Pointing the whole data root at a tmp directory solves
    both at once.

    The default is RELATIVE and resolved against PROJECT_ROOT, so the recorded
    default stays a fixed literal rather than an absolute path that differs per
    checkout, which is what lets the inertness table pin it by value. An absolute
    value is used as given, which is how a harness hands over a tmp directory.
    """
    _record(name, default_relative)
    candidate = Path(os.getenv(name, default_relative))
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def assert_unset(context: str) -> None:
    """Refuse to run `context` with any seam set.

    FOR THE CONTRACT MONITOR, and it matters more than its size suggests. The
    monitor imports feeds.SUBWAY_FEED_URLS, feeds.ALERT_FEED_URLS,
    static_data.SUBWAY_GTFS_URL and friends to watch the REAL upstreams from the
    outside; that independent vantage point is its entire value. Every one of
    those symbols is now redirectable, so a monitor process that happened to
    inherit these variables (a shared CI env block, a compose file, a developer
    shell that ran the contract tier earlier) would quietly check the simulator
    against itself and pass forever while the real upstream drifted. That is the
    exact "locally correct, composite lies" shape this arc exists to remove, so it
    fails loudly instead.

    Not a general policy: the app is SUPPOSED to honor these. Only a caller whose
    job is to observe the real world calls this.
    """
    set_here = sorted(name for name in DEFAULTS if os.getenv(name) is not None)
    if set_here:
        raise RuntimeError(
            f"{context} must observe the real upstreams, but these overrides are set: "
            f"{', '.join(set_here)}. Unset them, or run this outside the contract tier's "
            "environment."
        )
