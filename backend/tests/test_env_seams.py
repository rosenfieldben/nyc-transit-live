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

import bus_static
import cache
import env_seams
import ferry_static
import main
import path_static
import pollers
import railroad_static
import static_data
from feeds import alerts, buses, ferry, path, railroad, subway

BACKEND = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Every seam, every default, by value
# ---------------------------------------------------------------------------

_MTA_DATASERVICE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds"
_RRGTFS = "https://rrgtfsfeeds.s3.amazonaws.com"

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
    # Timing.
    "POLL_INTERVAL_S": 20,
    "ALERT_POLL_INTERVAL_S": 60,
    "FEED_RETENTION_MAX_S": 600,
    "STATIC_RETRY_S": 300,
    "STATIC_RETRY_SCHEDULE_S": (15, 30, 60, 300),
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
    }


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


def test_timing_constants_unchanged():
    assert pollers.POLL_INTERVAL_S == 20
    assert pollers.ALERT_POLL_INTERVAL_S == 60
    assert cache.FEED_RETENTION_MAX_S == 600
    assert main.STATIC_RETRY_S == 300
    assert main.STATIC_RETRY_SCHEDULE_S == (15, 30, 60, 300)


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
