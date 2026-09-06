"""The containment every audit script in this directory installs, in one place.

WHY THIS EXISTS AS A MODULE RATHER THAN A PARAGRAPH IN EACH SCRIPT. The rule was
already written down and already followed, in seven slightly different ways, and
four of them were wrong in the same invisible manner. This is what they got wrong:

    for var in ("NJT_USERNAME", "NJT_PASSWORD"):
        os.environ.pop(var, None)
    import njt_auth            # <- the credentials come BACK here

`env_seams` calls `load_dotenv` at import time, and `load_dotenv` fills keys that
are ABSENT. Popping a variable makes it absent, so a pop before the first backend
import is not a scrub: it is an invitation. On a developer checkout whose `.env`
holds real RailData credentials, the process that ran that preamble was configured,
pointed at `raildata.njtransit.com`, and one warmup away from spending a mint out
of ten a day. Measured on this repository: f06, f08, f09 and f13 all reached
`is_configured() is True` after their own scrub.

Blanking the variables in the parent shell does not help either, and that is the
trap inside the trap: `NJT_USERNAME= script.py` makes the value empty rather than
absent, so `load_dotenv` leaves it alone, but the script's own `pop` then DELETES
the empty value and `load_dotenv` refills it from `.env` on the next import.

SO THE CONTAINMENT DOES NOT RELY ON CREDENTIALS BEING ABSENT AT ALL. Two independent
walls, either of which is sufficient:

  1. THE ADDRESS. Every NJ Transit URL seam is pointed at the loopback discard port
     BEFORE any backend module is imported. These are seams `env_seams` reads once
     at import, and `load_dotenv` never overrides a key that is already set, so a
     value written here cannot be replaced by a `.env` entry. A process contained
     this way cannot reach NJ Transit even holding perfect credentials.
  2. THE CREDENTIALS. Set EMPTY rather than deleted, before the first backend import.
     `load_dotenv` fills absent keys and leaves set ones alone, so an empty value is
     one it cannot overwrite, at either of the two places this backend loads the
     `.env` (env_seams.py:46 and feeds/shared.py:35) or at a third somebody adds
     later. Deleting them, which is what these scripts used to do, is what invited
     the refill in the first place.

Wall 1 is the load-bearing one, because it survives a script that legitimately
NEEDS to look configured (f05 and f07 both drive the app as a configured
deployment; that is the finding, not a mistake). Wall 2 is what the other nine
assert, because for them "no credentials" is also the state under test.

USE IT LIKE THIS. Before touching any backend module:

    import _hermetic
    _hermetic.contain()
    sys.path.insert(0, str(BACKEND))
    import njt_auth
    _hermetic.verify()

`contain()` is idempotent and never overwrites a seam a script has already set, so
a script that stands up its own in-process fake (f05, f07) keeps its own addresses
and still gets the loopback default for any seam it did not think of.
"""

from __future__ import annotations

import os

# The loopback discard port. Nothing listens on TCP 9 on a developer machine or on
# a CI runner, so a request addressed here fails immediately and locally rather than
# hanging: the scripts want a fast, honest failure, not a timeout.
LOOPBACK = "http://127.0.0.1:9"

# Every env seam that can carry an NJ Transit address. Kept as a literal list rather
# than derived from env_seams.SEAM_NAMES, and the reason is the ordering this module
# exists to fix: reading the backend's own registry would mean importing the backend
# first, which is the exact thing that must not happen before the seams are set.
# tests/contract/upstream_sim.py holds the same four names for the same reason.
NJT_URL_SEAMS = (
    "NJT_TOKEN_URL",
    "NJT_STATIC_URL",
    "NJT_TU_URL",
    "NJT_ALERTS_URL",
)

CREDENTIAL_VARS = ("NJT_USERNAME", "NJT_PASSWORD")

# The hosts a contained process must never name. Both spellings, because the vendor
# uses one in its address and the other in its documentation and either could appear
# in a future default.
FORBIDDEN_HOSTS = ("raildata.njtransit.com", "njtransit.com")


def contain(*, keep_credentials: bool = False) -> None:
    """Install the containment. MUST run before any backend module is imported.

    `keep_credentials` is for the two scripts that drive the app as a CONFIGURED
    deployment (f05, f07). They fabricate their own username and password, which
    they set themselves before calling this, and wall 1 is what keeps them safe.
    Every other script leaves it False and gets both walls.
    """
    for name in NJT_URL_SEAMS:
        # setdefault, not assignment: a script that stands up its own fake upstream
        # has already put its own address here and must keep it.
        os.environ.setdefault(name, f"{LOOPBACK}/blocked/{name.lower()}")
    if not keep_credentials:
        drop_credentials()


def blank(*names: str) -> None:
    """Make each variable read as absent, PERMANENTLY, by setting it empty.

    SET, NOT POPPED, AND THAT IS THE ENTIRE TRICK. load_dotenv fills keys that are
    ABSENT and leaves keys that are SET, whatever they are set to, so an empty string
    is a value it will not overwrite while a deleted key is an invitation it will
    accept. Every consumer in this repository reads these through a helper that
    strips and treats empty as missing (njt_auth.credentials, feeds.shared._api_key),
    so blank is indistinguishable from absent everywhere it matters.

    THE POP IS NOT MERELY WEAKER, IT IS THE BUG. There are TWO load_dotenv calls in
    this backend, env_seams.py:46 and feeds/shared.py:35, and a script reaches them at
    different moments: f13 imports njt_auth at module scope and feeds.shared much
    later, inside a function. A drop placed correctly after the first import was
    undone by the second, and the app came up configured again. Counting the refill
    points and dropping after each is a rule that decays the moment somebody adds a
    third; setting the value once cannot decay, because there is nothing left to
    refill.
    """
    for name in names:
        os.environ[name] = ""


def drop_credentials() -> None:
    """Make the NJ Transit credentials read as absent for the life of this process."""
    blank(*CREDENTIAL_VARS)


def verify(*, expect_configured: bool = False, announce: bool = True) -> list[str]:
    """Assert the containment held, AFTER the backend modules are imported.

    Returns the lines it checked so a script can print them, because a containment
    nobody can see in the output is one nobody will notice breaking. Raises
    SystemExit rather than AssertionError so a `python -O` run cannot skip it.

    `expect_configured` is the f05/f07 case: those scripts MUST look configured, so
    what is asserted for them is that the credentials are not the machine's. A
    fabricated pair is proof the `.env` did not reach this process.
    """
    # IMPORTED FIRST, THEN THE CREDENTIALS ARE DROPPED, AND THAT ORDER IS THE POINT
    # OF THE WHOLE MODULE. This import is what runs env_seams, and env_seams is what
    # runs load_dotenv, so a drop before it is undone by it. For most callers njt_auth
    # is already in sys.modules and this line is free; for a script that imports the
    # backend lazily (f13) this IS the first backend import, which is exactly why the
    # drop cannot come before it.
    #
    # Imported here rather than at module scope for the same ordering reason: this
    # module is imported before the backend is on sys.path, which is what makes
    # contain() early enough to matter, so it cannot name njt_auth until now.
    import njt_auth as njt_auth_module

    if not expect_configured:
        drop_credentials()
    lines = []
    for name in NJT_URL_SEAMS:
        value = os.environ.get(name, "")
        lines.append(f"{name} = {value}")
        for host in FORBIDDEN_HOSTS:
            if host in value.lower():
                raise SystemExit(f"CONTAINMENT BREACH: {name} names {host}: {value!r}")

    # The module's own resolved values, not just the environment, because a seam set
    # too late reads as contained in os.environ while njt_auth already captured the
    # real default at import.
    for attr in ("NJT_TOKEN_URL",):
        resolved = str(getattr(njt_auth_module, attr, ""))
        lines.append(f"njt_auth.{attr} = {resolved}")
        for host in FORBIDDEN_HOSTS:
            if host in resolved.lower():
                raise SystemExit(
                    f"CONTAINMENT BREACH: njt_auth.{attr} names {host}. The seams were set "
                    "after the backend import rather than before it."
                )

    configured = njt_auth_module.is_configured()
    lines.append(f"njt_auth.is_configured() = {configured}")
    if expect_configured:
        if not configured:
            raise SystemExit(
                "this script drives NJ Transit as a CONFIGURED deployment and its "
                "fabricated credentials did not survive; it cannot run"
            )
        creds = njt_auth_module.credentials() or ("", "")
        if not creds[0].startswith(("f05-", "f07-")):
            raise SystemExit(
                f"CONTAINMENT BREACH: the credentials in this process are not the "
                f"script's own fabricated pair (username starts {creds[0][:4]!r})"
            )
        lines.append("credentials = the script's own fabricated pair, never the machine's")
    elif configured:
        raise SystemExit(
            "CONTAINMENT BREACH: NJ Transit credentials are present. env_seams calls "
            "load_dotenv at import, so they must be dropped again AFTER the import."
        )
    if announce:
        # ONE LINE, IN EVERY SCRIPT, EVERY RUN. A containment nobody can see in the
        # output is one nobody notices breaking, and run_all.sh prints a failing
        # script's whole log, so this is the first thing a reader of a red run sees.
        creds = "fabricated credentials" if expect_configured else "no credentials"
        print(f"containment: NJ Transit seams at {LOOPBACK}, {creds}, no real host reachable")
    return lines
