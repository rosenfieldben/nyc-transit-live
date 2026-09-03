"""C6 PR 1: the seams exist, they are INERT, and each default is pinned by value.

WHY THIS MODULE IS THE POINT OF SPLITTING PR 1 OUT. PR 1 adds a way to redirect
every upstream and compress every cadence, and adds nothing that uses it. The
whole claim is "setting nothing changes nothing", and a claim like that is worth
exactly as much as its test. So every default is written out HERE, as a literal,
and compared against what the modules actually resolved. A drive-by edit to a
default fails a named test instead of quietly shifting production.

The table is hardcoded rather than derived from env_seams.DEFAULTS, and that
distinction is load-bearing. C5 shipped a test that read its expectation from the
very tuple it was meant to pin; deleting an entry deleted the test case and the
suite stayed green. A test that asks the code what it should be cannot notice the
code changing, so this one does not ask.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import bus_static
import cache
import env_seams
import ferry_static
import main
import njt_auth
import njt_static
import path_static
import pollers
import railroad_static
import static_data
from feeds import alerts, buses, ferry, path, railroad, subway
from feeds import njt as njt_feed

BACKEND = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Every seam, every default, by value
# ---------------------------------------------------------------------------

_MTA_DATASERVICE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds"
_RRGTFS = "https://rrgtfsfeeds.s3.amazonaws.com"
_NJT_RAILDATA = "https://raildata.njtransit.com/api/GTFSRT"

EXPECTED_DEFAULTS: dict[str, object] = {
    # Realtime upstreams.
    "SUBWAY_RT_BASE": _MTA_DATASERVICE + "/nyct%2Fgtfs",
    "RAILROAD_RT_BASE": _MTA_DATASERVICE,
    "BUS_RT_URL": "https://gtfsrt.prod.obanyc.com/vehiclePositions",
    "ALERTS_RT_BASE": _MTA_DATASERVICE,
    "FERRY_ALERTS_URL": (
        "https://nycferry.connexionz.net/rtt/public/utility/gtfsrealtime.aspx/alert"
    ),
    # Static archives.
    "SUBWAY_GTFS_URL": _RRGTFS + "/gtfs_subway.zip",
    "RAILROAD_STATIC_BASE": _RRGTFS,
    "PATH_STATIC_URL": "https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip",
    "FERRY_STATIC_URL": "https://nycferry.connexionz.net/rtt/public/utility/gtfs.aspx",
    "BUS_STATIC_BASE": _RRGTFS,
    # NJ Transit (15a). TWO seams for one upstream, because the mint and the
    # archive live in different modules and both must be redirectable together:
    # pointing only the archive at a simulator would leave every contract run
    # minting real tokens out of ten a day (njt_auth.DAILY_MINT_LIMIT).
    "NJT_TOKEN_URL": _NJT_RAILDATA + "/getToken",
    "NJT_STATIC_URL": _NJT_RAILDATA + "/getGTFS",
    # NJ Transit realtime (15b). Two more whole URLs for the reason the 15a pair
    # gives: each is owned by the module that consumes it, and a contract scenario
    # has to be able to fail TRIP UPDATES while alerts keep flowing.
    "NJT_TU_URL": _NJT_RAILDATA + "/getTripUpdates",
    "NJT_ALERTS_URL": _NJT_RAILDATA + "/getAlerts",
    # Timing.
    "POLL_INTERVAL_S": 20,
    "ALERT_POLL_INTERVAL_S": 60,
    "FEED_RETENTION_MAX_S": 600,
    "STATIC_RETRY_S": 300,
    "STATIC_RETRY_SCHEDULE_S": (15, 30, 60, 300),
    # Relative on purpose, so this table stays a fixed literal instead of an
    # absolute path that differs per checkout (see env_seams.directory).
    "DATA_DIR": "data",
}


def test_registry_is_exactly_the_pinned_table():
    # Both directions on purpose. A changed default fails on the value; a seam
    # added without being pinned fails on the key set, which is the failure mode a
    # hand-maintained list always eventually misses.
    assert env_seams.DEFAULTS == EXPECTED_DEFAULTS


def test_no_seam_is_set_in_this_environment():
    # If CI or a developer's .env happened to set one of these, every assertion
    # below would be measuring the override rather than the default, and the whole
    # module would pass while proving nothing.
    leaked = sorted(name for name in EXPECTED_DEFAULTS if os.getenv(name) is not None)
    assert leaked == []


# ---------------------------------------------------------------------------
# The defaults as the modules actually resolved them
# ---------------------------------------------------------------------------
#
# The registry proves the default STRING. These prove it is still wired into the
# composed values callers use, which is a different claim: a seam could carry a
# perfect default and still be spliced into the wrong URL.


def test_subway_feed_urls_unchanged():
    base = _MTA_DATASERVICE + "/nyct%2Fgtfs"
    assert subway.SUBWAY_FEED_URLS == {
        "1-7+S": base,
        "ACE": base + "-ace",
        "BDFM": base + "-bdfm",
        "G": base + "-g",
        "JZ": base + "-jz",
        "NQRW": base + "-nqrw",
        "L": base + "-l",
        "SIR": base + "-si",
    }


def test_railroad_feed_urls_unchanged():
    assert railroad.RAILROAD_FEED_URLS == {
        "LIRR": _MTA_DATASERVICE + "/lirr%2Fgtfs-lirr",
        "MNR": _MTA_DATASERVICE + "/mnr%2Fgtfs-mnr",
    }


def test_alert_feed_urls_unchanged():
    assert alerts.ALERT_FEED_URLS == {
        "subway": _MTA_DATASERVICE + "/camsys%2Fsubway-alerts",
        "bus": _MTA_DATASERVICE + "/camsys%2Fbus-alerts",
        "LIRR": _MTA_DATASERVICE + "/camsys%2Flirr-alerts",
        "MNR": _MTA_DATASERVICE + "/camsys%2Fmnr-alerts",
        "ferry": "https://nycferry.connexionz.net/rtt/public/utility/gtfsrealtime.aspx/alert",
        # The sixth feed (15b), and the only one that is a POST behind a token.
        "njt": "https://raildata.njtransit.com/api/GTFSRT/getAlerts",
    }
    # KEYLESS_ALERT_FEEDS is the same table minus that one, derived rather than
    # re-listed. Pinned here in BOTH directions so neither the derivation nor the
    # membership can drift silently.
    assert alerts.NJT_ALERT_SYSTEM not in alerts.KEYLESS_ALERT_FEEDS
    assert set(alerts.KEYLESS_ALERT_FEEDS) | {"njt"} == set(alerts.ALERT_FEED_URLS)


def test_bus_urls_unchanged():
    assert buses.VEHICLE_POSITIONS_URL == "https://gtfsrt.prod.obanyc.com/vehiclePositions"
    assert bus_static.BUS_GTFS_URLS == {
        "manhattan": _RRGTFS + "/gtfs_m.zip",
        "brooklyn": _RRGTFS + "/gtfs_b.zip",
        "bronx": _RRGTFS + "/gtfs_bx.zip",
        "queens": _RRGTFS + "/gtfs_q.zip",
        "staten_island": _RRGTFS + "/gtfs_si.zip",
        "mta_bus_co": _RRGTFS + "/gtfs_busco.zip",
    }


def test_static_archive_urls_unchanged():
    assert static_data.SUBWAY_GTFS_URL == _RRGTFS + "/gtfs_subway.zip"
    assert railroad_static.RAILROAD_STATIC_URLS == {
        "LIRR": _RRGTFS + "/gtfslirr.zip",
        "MNR": _RRGTFS + "/gtfsmnr.zip",
    }
    assert (
        path_static.PATH_STATIC_URL
        == "https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip"
    )
    assert (
        ferry_static.FERRY_STATIC_URL
        == "https://nycferry.connexionz.net/rtt/public/utility/gtfs.aspx"
    )
    assert njt_static.NJT_STATIC_URL == _NJT_RAILDATA + "/getGTFS"
    assert njt_auth.NJT_TOKEN_URL == _NJT_RAILDATA + "/getToken"
    assert njt_feed.NJT_TU_URL == _NJT_RAILDATA + "/getTripUpdates"
    assert njt_feed.NJT_ALERTS_URL == _NJT_RAILDATA + "/getAlerts"


def test_timing_constants_unchanged():
    assert pollers.POLL_INTERVAL_S == 20
    assert pollers.ALERT_POLL_INTERVAL_S == 60
    assert cache.FEED_RETENTION_MAX_S == 600
    assert main.STATIC_RETRY_S == 300
    assert main.STATIC_RETRY_SCHEDULE_S == (15, 30, 60, 300)


def test_static_cache_paths_unchanged():
    # PROJECT_ROOT is derived here independently rather than read off a module, so
    # this compares the resolved paths against where they have always been rather
    # than against the code's own idea of where that is.
    project_root = Path(__file__).resolve().parent.parent.parent
    static = project_root / "data" / "gtfs_static"
    assert static_data.SUBWAY_GTFS_ZIP == static / "gtfs_subway.zip"
    assert railroad_static.RAILROAD_STATIC_ZIPS == {
        "LIRR": static / "gtfs_lirr.zip",
        "MNR": static / "gtfs_mnr.zip",
    }
    assert path_static.PATH_STATIC_ZIP == static / "gtfs_path.zip"
    assert ferry_static.FERRY_STATIC_ZIP == static / "gtfs_ferry.zip"
    assert njt_static.NJT_STATIC_ZIP == static / "gtfs_njt.zip"
    assert bus_static.BUS_CACHE_DIR == project_root / "data" / "cache" / "bus_routes"


def test_timing_constants_keep_their_type():
    # "Byte-identical" includes the type. seconds() returns the default OBJECT when
    # nothing is set rather than float()ing it, so these stay int; == alone would
    # not notice, because 300 == 300.0. A float would leak into log lines and into
    # the schedule tuple, which is a cosmetic regression but a regression.
    for value in (
        pollers.POLL_INTERVAL_S,
        pollers.ALERT_POLL_INTERVAL_S,
        cache.FEED_RETENTION_MAX_S,
        main.STATIC_RETRY_S,
        *main.STATIC_RETRY_SCHEDULE_S,
    ):
        assert isinstance(value, int), f"{value!r} is {type(value).__name__}, not int"


def test_predecessor_seams_still_hold_their_literals():
    # PATH_RT_URL (13b) and FERRY_RT_BASE (R3) predate this registry and keep their
    # bare os.getenv calls, so they are absent from DEFAULTS by design. They are
    # pinned here anyway, because the property C6 depends on is "EVERY upstream is
    # redirectable", and a list that silently omitted the two that already were
    # would be the easiest possible way to leave a real fetch in a hermetic tier.
    assert path.PATH_RT_URL == "https://path.transitdata.nyc/gtfsrt"
    assert (
        ferry.FERRY_RT_BASE
        == "https://nycferry.connexionz.net/rtt/public/utility/gtfsrealtime.aspx"
    )


# ---------------------------------------------------------------------------
# The seams are real, not decorative
# ---------------------------------------------------------------------------


def _resolve(expr: str, env: dict[str, str]) -> str:
    """Import the backend in a FRESH interpreter with `env` set and print `expr`.

    A subprocess rather than importlib.reload: these values are computed at import
    into dicts that other modules have already captured, so reloading in-process
    would leave half the suite looking at the old objects. It also proves the seam
    the way the contract tier will actually use it, which is a real process with
    real environment variables.
    """
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, '.'); print({expr})"],
        cwd=BACKEND,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_a_base_seam_moves_every_url_built_on_it():
    # THE SUBWAY BASE HAS AN UNUSUAL SHAPE and the simulator will have to match it:
    # its suffix set includes the EMPTY string, so one group ("1-7+S") maps to the
    # bare base while the other seven append "-ace", "-bdfm" and so on with NO
    # separating slash. Both forms are pinned here, because a harness that served
    # only the suffixed paths would leave the 1-7+S group 404ing in a way that
    # looks like a decoder failure.
    out = _resolve(
        "__import__('feeds.subway', fromlist=['x']).SUBWAY_FEED_URLS",
        {"SUBWAY_RT_BASE": "http://127.0.0.1:9999/sim"},
    )
    assert "'1-7+S': 'http://127.0.0.1:9999/sim'" in out
    assert "'BDFM': 'http://127.0.0.1:9999/sim-bdfm'" in out


def test_a_trailing_slash_is_harmless_on_a_suffix_without_a_separator():
    # The subway suffixes carry no leading slash, unlike the railroad and alerts
    # ones, so rstrip("/") is what keeps "sim/" and "sim" resolving identically
    # instead of producing "sim/-bdfm".
    out = _resolve(
        "__import__('feeds.subway', fromlist=['x']).SUBWAY_FEED_URLS['BDFM']",
        {"SUBWAY_RT_BASE": "http://127.0.0.1:9999/sim/"},
    )
    assert out == "http://127.0.0.1:9999/sim-bdfm"


def test_the_alerts_base_moves_independently_of_the_railroad_base():
    # The seam split that the C1/C2 partial-outage scenarios depend on: taking the
    # alert feeds to the simulator must leave the railroad realtime feeds pointed
    # at their own base, even though both default to the same MTA host.
    out = _resolve(
        "(__import__('feeds.alerts', fromlist=['x']).ALERT_FEED_URLS['LIRR'], "
        "__import__('feeds.railroad', fromlist=['x']).RAILROAD_FEED_URLS['LIRR'])",
        {"ALERTS_RT_BASE": "http://127.0.0.1:9999/sim"},
    )
    assert out == (
        f"('http://127.0.0.1:9999/sim/camsys%2Flirr-alerts', '{_MTA_DATASERVICE}/lirr%2Fgtfs-lirr')"
    )


def test_a_whole_url_seam_moves_the_url():
    out = _resolve(
        "__import__('static_data').SUBWAY_GTFS_URL",
        {"SUBWAY_GTFS_URL": "http://127.0.0.1:9999/sim/gtfs_subway.zip"},
    )
    assert out == "http://127.0.0.1:9999/sim/gtfs_subway.zip"


def test_a_timing_seam_compresses_the_cadence():
    # Through main, not `import pollers`: pollers and warmups import main back, so
    # importing either first hits a partially-initialized module. main is the
    # composition root and the entry point uvicorn uses, which is also how the
    # contract tier will start the process.
    out = _resolve(
        "(__import__('main'), __import__('pollers').POLL_INTERVAL_S)[1]",
        {"POLL_INTERVAL_S": "2"},
    )
    assert out == "2.0"


def test_the_retry_schedule_seam_takes_a_comma_separated_list():
    out = _resolve(
        "__import__('main').STATIC_RETRY_SCHEDULE_S",
        {"STATIC_RETRY_SCHEDULE_S": "1,2,3", "STATIC_RETRY_S": "4"},
    )
    assert out == "(1.0, 2.0, 3.0)"


def test_a_trailing_slash_on_a_base_does_not_double_the_separator():
    # FERRY_RT_BASE's .rstrip("/") rationale, applied to every base seam: an
    # operator or a harness that writes the simulator root with a trailing slash
    # must not produce "sim//camsys%2Flirr-alerts".
    out = _resolve(
        "__import__('feeds.alerts', fromlist=['x']).ALERT_FEED_URLS['LIRR']",
        {"ALERTS_RT_BASE": "http://127.0.0.1:9999/sim/"},
    )
    assert out == "http://127.0.0.1:9999/sim/camsys%2Flirr-alerts"


def test_a_malformed_timing_value_fails_loudly():
    # Not a silent fallback to the default: these are set by an operator or a
    # harness, and a contract scenario timing out because "2s" was not parseable
    # is a far worse debugging experience than an import that says so.
    result = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import main"],
        cwd=BACKEND,
        env={**os.environ, "POLL_INTERVAL_S": "2s"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "ValueError" in result.stderr


# ---------------------------------------------------------------------------
# The monitor keeps its independent vantage point
# ---------------------------------------------------------------------------


# The guard's coverage list, written out here too. The two predecessors are on it
# and are NOT in EXPECTED_DEFAULTS, because they are declared with a bare os.getenv
# rather than through env_seams; that gap is exactly what let the first version of
# the guard miss them.
EXPECTED_SEAM_NAMES = frozenset(EXPECTED_DEFAULTS) | {"PATH_RT_URL", "FERRY_RT_BASE"}


def test_the_guard_covers_every_seam_including_the_two_predecessors():
    assert frozenset(env_seams.SEAM_NAMES) == EXPECTED_SEAM_NAMES
    assert len(env_seams.SEAM_NAMES) == len(EXPECTED_SEAM_NAMES)  # no duplicates


def test_declaring_a_seam_outside_the_list_fails_at_import():
    # What makes SEAM_NAMES self-enforcing: a seam added through env_seams but not
    # listed cannot silently widen the set behind the guard's back.
    with pytest.raises(RuntimeError, match="not in SEAM_NAMES"):
        env_seams.url("A_SEAM_NOBODY_LISTED", "https://example.invalid")


@pytest.mark.parametrize("name", ["PATH_RT_URL", "FERRY_RT_BASE"])
def test_the_monitor_guard_catches_the_predecessor_overrides(name):
    # THE HOLE AN ADVERSARIAL REVIEW FOUND. These two predate env_seams and never
    # register in DEFAULTS, so a guard built on the registry let the monitor run
    # happily against a redirected PATH or ferry feed: verified by running the real
    # script, which printed PASS lines against the redirect before this was fixed.
    result = subprocess.run(
        [sys.executable, "scripts/contract_monitor.py"],
        cwd=BACKEND,
        env={**os.environ, name: "http://127.0.0.1:9999/sim"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert name in result.stderr


def test_the_guard_does_not_depend_on_which_modules_are_imported():
    # The other half of the same defect: DEFAULTS fills as modules import, so under
    # the monitor's own import graph a registry-driven check saw 11 of 16 seams and
    # the five timing ones were invisible. SEAM_NAMES is static, so a process that
    # imports almost nothing still checks all of them.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.'); import env_seams; "
            "env_seams.assert_unset('a bare process')",
        ],
        cwd=BACKEND,
        env={**os.environ, "POLL_INTERVAL_S": "2"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "POLL_INTERVAL_S" in result.stderr


def test_the_monitor_refuses_to_run_against_a_redirected_upstream():
    # The hazard PR 1 creates: the monitor imports feeds.SUBWAY_FEED_URLS and
    # friends to watch the REAL upstreams, and this PR made every one of them
    # redirectable. A monitor that inherited the contract tier's environment would
    # check the simulator against itself and pass forever, which is worse than no
    # monitor at all because it looks like coverage.
    result = subprocess.run(
        [sys.executable, "scripts/contract_monitor.py"],
        cwd=BACKEND,
        env={**os.environ, "SUBWAY_RT_BASE": "http://127.0.0.1:9999/sim"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "must observe the real upstreams" in result.stderr
    assert "SUBWAY_RT_BASE" in result.stderr


def test_assert_unset_passes_when_nothing_is_set():
    # The converse, so the guard cannot pass its own test by always raising.
    env_seams.assert_unset("a caller with a clean environment")


def test_the_data_root_seam_moves_every_cache_path():
    # Redirecting an archive's URL is NOT enough to test a cold start: a checkout
    # that already holds a valid archive serves the cache and never downloads, so
    # the finding-4 scenario (no cache, headers-only publish, never ready) could
    # not be expressed. This is the seam that lets the harness hand the process a
    # tmp directory, and it must move every loader at once.
    out = _resolve(
        "(__import__('static_data').SUBWAY_GTFS_ZIP, "
        "__import__('ferry_static').FERRY_STATIC_ZIP, "
        "__import__('bus_static').BUS_CACHE_DIR)",
        {"DATA_DIR": "/tmp/c6-contract-data"},
    )
    assert out == (
        "(PosixPath('/tmp/c6-contract-data/gtfs_static/gtfs_subway.zip'), "
        "PosixPath('/tmp/c6-contract-data/gtfs_static/gtfs_ferry.zip'), "
        "PosixPath('/tmp/c6-contract-data/cache/bus_routes'))"
    )


def test_a_relative_data_root_stays_under_the_checkout():
    # The default is relative, so a relative override has to behave the same way
    # rather than resolving against whatever the process working directory happens
    # to be, which for the backend is backend/ and not the repo root.
    # print() of a single Path gives str(); the tuple above goes through repr, which
    # is why only that one carries the PosixPath wrapper.
    out = _resolve("__import__('static_data').SUBWAY_GTFS_ZIP", {"DATA_DIR": "tmpdata"})
    assert out == f"{BACKEND.parent}/tmpdata/gtfs_static/gtfs_subway.zip"
