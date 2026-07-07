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
