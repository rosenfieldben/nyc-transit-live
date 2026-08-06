"""THE COUPLING TEST for the poll registry and every feed_cache fixture (15b).

WHY THIS EXISTS, stated as the failure rather than the rule. `_poll_feeds` looks
up `cache[name]` for every entry in `pollers.FEED_REFRESHERS` BEFORE the
TaskGroup's children start. A feed_cache missing one key therefore raises a
KeyError in the cycle BODY, not inside a child: the cycle's own `except Exception`
logs it, the loop sleeps, and goes round again. Under the concurrency suite's
`_CycleClock`, which replaces that sleep with a counter and a zero delay, the loop
then spins at full speed forever. The suite does not fail. It HANGS, with no
failing assertion and no error to read.

That is exactly what adding "njt" to the registry without updating the two
fixtures did, and it cost a debugging detour to find. The countermeasure is not a
comment at the registry: this project has watched comments fail at this job
before (the A4 record's "loud, never absent" convention exists for the same
reason). It is this test.

WHAT IT ASSERTS, and why it is a COUPLING test rather than a derivation: each
fixture keeps its keys written out by hand, and this compares that hand-written
set against the registry. If the fixtures were built FROM the registry they could
never disagree, which sounds better and is worse: the whole class of "the author
of the next system forgot something" would move from a red test to silence, and
nothing would be checking that the fixtures still model what the cycle reads. Two
lists maintained independently, compared here, is the 15a workflow-guard pattern
applied to the same hazard.

Every module with a feed_cache fixture gets one line here (see
_assert_covers_every_registry_key). A new such fixture that is not listed is the
one gap this cannot see, so keep the roster below complete.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

# main BEFORE pollers, matching every other test module here: pollers imports main
# and main imports pollers back, so importing pollers first hits the half-built
# module. The app resolves this at runtime because main is always the entry point;
# a test module has to respect the same order.
import main as _app_module  # noqa: F401
import pollers


def _registry_keys() -> set[str]:
    return {name for name, _refresher_name in pollers.FEED_REFRESHERS}


def _assert_covers_every_registry_key(cache: dict, where: str) -> None:
    """The assertion itself, shared so the failure message is identical wherever
    it fires and says what to do rather than only what is wrong."""
    missing = sorted(_registry_keys() - set(cache))
    extra = sorted(set(cache) - _registry_keys())
    assert not missing, (
        f"{where} builds a feed_cache missing {missing}, which pollers.FEED_REFRESHERS "
        "registers. _poll_feeds reads cache[name] before its TaskGroup children start, so "
        "the KeyError lands in the cycle body: the loop logs, sleeps, and repeats, and "
        "under the concurrency suite's clock that sleep is a no-op. The suite HANGS "
        "instead of failing. Add the key to that fixture."
    )
    assert not extra, (
        f"{where} builds a feed_cache with {extra}, which pollers.FEED_REFRESHERS does not "
        "register. A key nothing refreshes serves a permanently warming endpoint."
    )


def test_the_registry_is_the_only_place_the_source_list_lives():
    """The extraction that makes this test possible must stay real: the cycle has
    to ITERATE the registry rather than carry its own copy. A second literal list
    inside _poll_feeds would leave every assertion here true and meaningless."""
    source = inspect.getsource(pollers._poll_feeds)
    assert "for name, refresher_name in FEED_REFRESHERS:" in source, (
        "_poll_feeds must iterate pollers.FEED_REFRESHERS; a private copy of the source "
        "list would make the fixture coupling tests vacuous"
    )
    # And nothing else in the cycle may enumerate sources by hand.
    for name in _registry_keys():
        assert f'("{name}",' not in source, (
            f"the cycle appears to name {name!r} inline; the registry is the single list"
        )


def test_every_registered_source_resolves_to_a_coroutine_refresher():
    """The registry holds NAMES so a monkeypatched refresher is the one the cycle
    runs (see its comment). A name that resolves to nothing would raise a KeyError
    in the cycle body, which is the same hang this file exists to prevent."""
    for name, refresher_name in pollers.FEED_REFRESHERS:
        refresh = getattr(pollers, refresher_name, None)
        assert refresh is not None, (
            f"{name} names a refresher {refresher_name!r} that does not exist"
        )
        assert inspect.iscoroutinefunction(refresh), f"{name} refresher must be async"
    names = [name for name, _ in pollers.FEED_REFRESHERS]
    assert len(names) == len(set(names)), f"duplicate source name in the registry: {names}"


def test_the_degrader_knows_every_registered_source():
    """_feed_degrader is the "mark everything down" hook a failure nobody
    classified routes through. A source it does not recognise silently marks
    nothing, which is the false-green C4 exists to remove: the poll stops and every
    operator surface stays healthy.

    Buses are the one deliberate exception and are named as such in that function:
    the bus source publishes no per-feed health dict at all, so there is nothing to
    mark beyond its cache error.
    """
    known = set(pollers._FEED_HEALTH_TOTALS) | set(pollers._SINGLE_FEED_HEALTH) | {"buses"}
    unhandled = sorted(_registry_keys() - known)
    assert not unhandled, (
        f"{unhandled} are registered sources that _feed_degrader cannot mark. An "
        "unclassified failure in one of them would stop its poll while /api/status and "
        "the per-system blocks all still reported health."
    )


def _feed_cache_keys(module_filename: str) -> list[str]:
    """The string keys a test module's `cache` fixture builds its feed_cache from.

    READ FROM THE SOURCE rather than by requesting the fixture, and that is forced
    rather than chosen: pytest fixtures are private to their defining module (or a
    conftest), so a test here cannot ask test_api.py for its `cache`. Moving both
    fixtures into conftest.py would make them requestable and would ALSO make them
    a single shared object, which is precisely the derivation this file argues
    against: two lists that cannot disagree need no coupling test.

    Handles both shapes in use: a dict literal ({"buses": _fresh_entry(), ...}) and
    a dict comprehension over a tuple of names. A shape this cannot read raises
    rather than returning an empty list, so a rewrite of either fixture fails here
    loudly instead of quietly asserting nothing.
    """
    tree = ast.parse((pathlib.Path(__file__).parent / module_filename).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Attribute) and target.attr == "feed_cache"):
            continue
        if isinstance(node.value, ast.Dict):
            return [
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            ]
        if isinstance(node.value, ast.DictComp):
            for gen in node.value.generators:
                if isinstance(gen.iter, ast.Tuple):
                    return [
                        element.value
                        for element in gen.iter.elts
                        if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    ]
    raise AssertionError(
        f"could not read the feed_cache keys out of {module_filename}; the fixture's shape "
        "changed, and this guard must be taught the new one rather than left silent"
    )


@pytest.mark.parametrize("module_filename", ["test_api.py", "test_pollers_concurrency.py"])
def test_every_feed_cache_fixture_covers_the_registry(module_filename):
    """THE COUPLING ITSELF. Each module's hand-written key set against the
    registry's, in both directions.

    The roster is parametrized rather than discovered, so adding a third module
    with a feed_cache fixture means adding it here. That is the one gap this
    cannot close by itself, and it is named in the module docstring.
    """
    keys = _feed_cache_keys(module_filename)
    assert len(keys) == len(set(keys)), f"{module_filename} lists a duplicate cache key: {keys}"
    _assert_covers_every_registry_key(dict.fromkeys(keys, {}), f"tests/{module_filename}::cache")
