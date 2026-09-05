"""Shared helpers for the backend test modules.

pytest's rootless collection puts this directory on sys.path, so test modules
import from here directly (`from conftest import golden_fixture_guard`).
"""

import os

import pytest


def golden_fixture_guard(sentinel, gen_script):
    """Gate a module's golden tests on the presence of their fixture.

    Locally, a missing fixture skips loudly, because generating one needs
    egress the developer may not have. In CI (GitHub Actions sets CI=true)
    a missing fixture FAILS instead: 13a and 13b both merged green while
    all ten goldens were dormant, because a skip is invisible in a passing
    summary line. The failure message names the generation script so the
    fix is one command away.
    """
    if sentinel.exists():
        # Inert marker so callers can decorate unconditionally.
        return pytest.mark.skipif(False, reason=f"golden fixture {sentinel.name} present")
    reason = f"golden fixture missing ({sentinel}); run {gen_script} to generate it"
    if os.environ.get("CI"):
        return pytest.mark.missing_golden(reason)
    return pytest.mark.skip(reason=reason)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "missing_golden(reason): golden fixture absent in CI; fails the test at setup",
    )


def pytest_runtest_setup(item):
    marker = item.get_closest_marker("missing_golden")
    if marker is not None:
        pytest.fail(marker.args[0], pytrace=False)


# ---------------------------------------------------------------------------
# The .env scrub: a developer's real credentials must not reach this suite
# ---------------------------------------------------------------------------

# The credential variables .env carries. NOT env_seams.SEAM_NAMES, and the
# distinction is the point: a seam redirects an upstream and every seam defaults
# to the production literal, so leaving one unset changes nothing. These three are
# the opposite shape. They have no default, the app branches on their PRESENCE,
# and seven tests in this suite assert the behavior of the branch taken when
# they are absent.
#
# NJT_USERNAME and NJT_PASSWORD are the pair those seven tests are about
# (feeds.active_alert_feeds reads them on every call, so a configured NJ Transit
# joins ALERT_FEED_URLS and the alert-health map). BUS_TIME_API_KEY is listed
# beside them because it is the third credential .env holds and the leak this
# fixture closes is not specific to NJ Transit; measured on 2026-09-05, no test in
# this suite depends on it in either direction, so scrubbing it is inert today and
# is here so the next unconfigured-bus test does not have to rediscover this.
CREDENTIAL_VARS = ("NJT_USERNAME", "NJT_PASSWORD", "BUS_TIME_API_KEY")


@pytest.fixture(scope="session", autouse=True)
def scrub_developer_credentials():
    """Run the hermetic suite as an UNCONFIGURED deployment, whatever .env says.

    THE FAILURE THIS REMOVES. backend/env_seams.py calls load_dotenv at import, and
    so does feeds/shared.py, so importing anything in this app copies the
    project-root .env into os.environ. A developer who has actually registered for
    NJ Transit RailData (which the README tells them to do) therefore runs this
    suite with NJT_USERNAME and NJT_PASSWORD set, and seven tests written for an
    unconfigured NJ Transit fail on their machine and only on their machine: three
    in test_api.py, three in test_feeds_alerts.py, plus
    test_alert_health_seeds_a_system_that_gained_credentials in
    test_pollers_concurrency.py, which says the quiet part in its own assertion
    message ("the test environment has no NJT credentials"). CI passes because the
    runner has no .env at all. That is a hermetic suite whose result depends on the
    machine, which is the one property it exists to not have.

    SESSION SCOPED, and once is enough. Both load_dotenv calls are module level, so
    every one of them has already run by the time a session fixture first executes;
    nothing in this suite reloads a module that carries one. Restoring on teardown
    keeps the pytest process honest for anything that runs after it.

    THE OPT-IN IS configure_njt() below. A test that needs a CONFIGURED deployment
    sets the pair through monkeypatch, which overrides this scrub for that test and
    is undone by monkeypatch's own teardown back to the scrubbed state.
    """
    saved = {name: os.environ[name] for name in CREDENTIAL_VARS if name in os.environ}
    for name in saved:
        del os.environ[name]
    try:
        yield
    finally:
        os.environ.update(saved)


def configure_njt(monkeypatch, username="rider", password="secret"):
    """Opt one test back in to a CONFIGURED NJ Transit.

    The counterpart to scrub_developer_credentials, and a named function rather
    than a fixture because both call sites are inside helpers that already hold a
    monkeypatch, not test bodies. The values are deliberately fake: a test that
    wanted the developer's REAL credentials would be a test that reaches NJ
    Transit, which this tier never does.
    """
    monkeypatch.setenv("NJT_USERNAME", username)
    monkeypatch.setenv("NJT_PASSWORD", password)
