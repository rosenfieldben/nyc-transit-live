# pytest adds this file's directory to sys.path, letting tests import the
# backend modules (feeds, bus_static, ...) directly.
import pytest


@pytest.fixture
def anyio_backend():
    # Async tests (httpx ASGITransport in test_api.py) run via the anyio
    # pytest plugin, which ships with the existing deps; trio isn't installed.
    return "asyncio"
