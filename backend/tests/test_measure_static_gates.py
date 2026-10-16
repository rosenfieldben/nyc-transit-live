"""backend/scripts/measure_static_gates.py, without a download or a socket.

The script fetches four public archives into a scratch directory and times each sibling
loader's light check against its publication gate (claude/static-rule-siblings, D2). Three
properties keep it honest, and each is pinned here:

  * it refuses (exit 2, before any download) a directory that is, or overlaps, a data
    root, or that is a checkout, since it writes the archives under the caches' names;
  * importing it does nothing;
  * with the archives already in the directory it downloads nothing, and it measures and
    prints every archive, NJ Transit's built from the committed fixture.

Loaded from its file path, as test_measure_subway_validation loads its script.
"""

from __future__ import annotations

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

from tests.test_static_shared import good_archive

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "measure_static_gates.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_static_gates", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_importing_the_script_does_nothing(monkeypatch):
    def no_socket(*_args, **_kwargs):
        raise AssertionError("importing the script opened a socket")

    monkeypatch.setattr(socket, "socket", no_socket)
    module = _load()
    assert module.REFUSED == 2


@pytest.mark.parametrize("where", ["the root itself", "inside it", "around it"])
def test_a_directory_overlapping_a_data_root_is_refused(tmp_path, where):
    module = _load()
    root = tmp_path / "data"
    target = {"the root itself": root, "inside it": root / "gtfs_static", "around it": tmp_path}
    with pytest.raises(module.Refused):
        module.check_scratch(target[where], [root])


def test_a_checkout_is_refused(tmp_path):
    module = _load()
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "env_seams.py").write_text("")
    with pytest.raises(module.Refused):
        module.check_scratch(tmp_path, [tmp_path / "elsewhere" / "data"])


def test_main_refuses_with_exit_2_before_doing_anything(tmp_path, monkeypatch, capsys):
    module = _load()
    monkeypatch.setattr(module, "real_data_roots", lambda: (tmp_path,))
    assert module.main([str(tmp_path / "scratch")]) == module.REFUSED
    assert not (tmp_path / "scratch").exists()
    assert "REFUSED" in capsys.readouterr().err


def test_an_offline_run_over_archives_on_disk_measures_every_one(tmp_path, monkeypatch, capsys):
    """Every archive the run measures is already in the directory, so nothing is
    downloaded (the transfer is replaced by one that fails the test), and the table has a
    block for each of the five, NJ Transit's labelled as the fixture it is."""
    module = _load()
    monkeypatch.setattr(module, "real_data_roots", lambda: (tmp_path / "data",))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    for name in ("gtfs_lirr.zip", "gtfs_mnr.zip", "gtfs_path.zip", "gtfs_ferry.zip"):
        (scratch / name).write_bytes(good_archive())
    if str(_SCRIPT.parent.parent) not in sys.path:
        sys.path.insert(0, str(_SCRIPT.parent.parent))
    import static_shared

    async def no_download(*_args, **_kwargs):
        raise AssertionError("an archive already on disk was downloaded again")

    monkeypatch.setattr(static_shared, "_stream_to_file", no_download)

    assert module.main([str(scratch), "--runs", "1"]) == 0
    out = capsys.readouterr().out
    for label in ("LIRR:", "Metro-North:", "PATH:", "NYC Ferry:", "NJ Transit (the committed"):
        assert label in out
    assert out.count("reused") == 4
    assert out.count("publication (after)") == 5
