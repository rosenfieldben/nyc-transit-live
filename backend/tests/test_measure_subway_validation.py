"""The scratch rule of backend/scripts/measure_subway_validation.py.

That script drives the real subway warmup, which writes, promotes and deletes the
subway cache, after one real download. Two properties keep it from touching anything
it must not, and both are pinned here without a download or a socket:

  * it refuses (exit 2, before any download or environment change) a run whose
    scratch cache would be the real cache, whose scratch data root overlaps the real
    data root, or whose --archive names the real cache;
  * importing it does nothing: no socket, no change to DATA_DIR. The hermetic suite
    can load it (this file does) without a network side effect.

The script lives under scripts/ (not an importable package), so it is loaded from its
file path, the way test_contract_monitor loads the monitor.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "measure_subway_validation.py"

# Socket events seen while _WATCHING is set. An audit hook cannot be removed, so it is
# installed once and gated, and it only records: the assertion is the test's.
_WATCHING = False
_SOCKET_EVENTS: list[str] = []


def _record_sockets(event: str, args: tuple) -> None:
    if _WATCHING and event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
        _SOCKET_EVENTS.append(event)


sys.addaudithook(_record_sockets)


def _load():
    spec = importlib.util.spec_from_file_location("measure_subway_validation", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def watched():
    global _WATCHING
    _SOCKET_EVENTS.clear()
    _WATCHING = True
    try:
        yield _SOCKET_EVENTS
    finally:
        _WATCHING = False


def test_importing_the_script_opens_no_socket_and_leaves_data_dir_alone(watched):
    before = os.environ.get("DATA_DIR")
    module = _load()
    assert watched == []
    assert os.environ.get("DATA_DIR") == before
    assert callable(module.main)


def test_a_scratch_directory_elsewhere_gets_a_plan_inside_it(tmp_path):
    module = _load()
    real = tmp_path / "repo" / "data" / "gtfs_static" / "gtfs_subway.zip"
    out = tmp_path / "scratch"
    plan = module.scratch_plan(out, None, (real,))
    assert plan["scratch_cache"] == out.resolve() / "data" / "gtfs_static" / "gtfs_subway.zip"
    for path in plan.values():
        assert path == out.resolve() or out.resolve() in path.parents


@pytest.mark.parametrize(
    "where",
    [
        "the directory above the data root, so its scratch cache is the real one",
        "inside the real data root",
        "the real data root itself",
    ],
)
def test_the_plan_refuses_anything_that_reaches_the_real_cache(tmp_path, where):
    module = _load()
    real_root = tmp_path / "repo" / "data"
    real = real_root / "gtfs_static" / "gtfs_subway.zip"
    out = {
        "the directory above the data root, so its scratch cache is the real one": real_root.parent,
        "inside the real data root": real_root / "gtfs_static" / "x",
        "the real data root itself": real_root,
    }[where]
    with pytest.raises(module.Refused):
        module.scratch_plan(out, None, (real,))


def test_the_plan_refuses_the_real_cache_as_the_archive_to_measure(tmp_path):
    module = _load()
    real = tmp_path / "repo" / "data" / "gtfs_static" / "gtfs_subway.zip"
    with pytest.raises(module.Refused):
        module.scratch_plan(tmp_path / "scratch", real, (real,))


def test_main_refuses_this_checkouts_real_data_root_before_any_download(watched):
    """End to end through main(): the real data root as the scratch directory is
    refused with exit 2, before the environment changes and before any socket."""
    module = _load()
    real_root = module.real_cache_paths()[0].parent.parent
    before = os.environ.get("DATA_DIR")
    # An --archive that does not exist, so a regression in the rule fails on the copy
    # instead of downloading the MTA archive into the checkout's data root.
    absent = ["--archive", str(real_root / "absent.zip")]
    assert module.main([str(real_root), *absent]) == module.REFUSED
    assert watched == []
    assert os.environ.get("DATA_DIR") == before


def test_a_leftover_data_dir_does_not_unprotect_the_checkouts_default_data_root(
    watched, tmp_path, monkeypatch
):
    """A DATA_DIR exported in the invoking shell (a contract-tier tmp root left behind,
    say) is not what the served app uses, so it must not be the only root protected:
    the checkout's default data/ as the scratch directory's data root is still refused,
    exit 2, before any socket and before the environment changes."""
    module = _load()
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "leftover"))
    caches = module.real_cache_paths()
    default_root = caches[-1].parent.parent
    assert caches[0].parent.parent == tmp_path / "leftover"
    assert default_root != tmp_path / "leftover"
    absent = ["--archive", str(tmp_path / "absent.zip")]  # never a download, as above
    assert module.main([str(default_root.parent), *absent]) == module.REFUSED
    assert watched == []
    assert os.environ.get("DATA_DIR") == str(tmp_path / "leftover")


def test_the_real_cache_it_protects_is_the_one_static_data_serves_from():
    """THE COUPLING, independent of the function under test: whatever DATA_DIR this
    process runs with, the cache static_data serves from is among the ones the plan
    keeps clear of, so a wrong default in real_cache_paths cannot leave the served cache
    unprotected."""
    module = _load()
    import static_data

    assert static_data.SUBWAY_GTFS_ZIP.resolve() in [p.resolve() for p in module.real_cache_paths()]


def test_a_scratch_directory_that_is_another_checkout_is_refused(tmp_path):
    """A copy of this script in a worktree protects its own data roots, not the main
    checkout's; pointed at the main checkout's root it would make that checkout's data/
    its scratch data root. Any directory shaped like a checkout is refused."""
    module = _load()
    other = tmp_path / "other-checkout"
    (other / "backend").mkdir(parents=True)
    (other / "backend" / "env_seams.py").write_text("")
    real = tmp_path / "repo" / "data" / "gtfs_static" / "gtfs_subway.zip"
    with pytest.raises(module.Refused):
        module.scratch_plan(other, None, (real,))


def test_a_leftover_subway_url_refuses_the_download(watched, tmp_path, monkeypatch):
    """SUBWAY_GTFS_URL is the contract tier's seam; left exported, it would make the one
    download fetch a simulator's archive and report it as the MTA's. Refused, exit 2,
    with no socket opened."""
    module = _load()
    monkeypatch.setenv("SUBWAY_GTFS_URL", "http://127.0.0.1:9/gtfs_subway.zip")
    assert module.main([str(tmp_path / "scratch")]) == module.REFUSED
    assert watched == []
