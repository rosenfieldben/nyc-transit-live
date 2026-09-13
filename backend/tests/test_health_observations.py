"""The observations-qualified rule, clause by clause (contract 6.2, design section 4.5).

routes/status.py's _systems_serving_nothing_current decides whether /healthz publishes
`observations-qualified`. It is pure and clock-injected like the rest of that module's
classification, so every clause is asserted here with no client, no poller and no wall
clock. The rule and its measured threshold are documented once, at
models.HEALTH_OBSERVATIONS_QUALIFIED; the last test in this file re-derives that
comment's three numbers from the committed capture so the two cannot drift apart.

Where the other witnesses live: the probe-level ones (the code never gates, and it is
independent of feed-content-stale in both directions) sit beside the other /healthz
tests in test_api.py, and the composite one is tests/contract/test_contract_api.py's
test_one_subway_group_down_reaches_healthz_as_qualified_observations.
"""

import json
import types
from pathlib import Path

import pytest

import feeds
from cache import FEED_RETENTION_MAX_S, FEED_STALE_AFTER_S
from routes import status as status_routes
from routes.status import (
    _served_arrival_systems,
    _ServedSystem,
    _systems_serving_nothing_current,
)

FIX = Path(__file__).parent / "fixtures"

NOW = 10_000.0
FRESH = NOW - 5.0
OLD = NOW - 600.0  # the F03 world's injected lag


def _row(observed_at=FRESH, provenance="reported"):
    """One decoder-shaped arrival row carrying the contract pair."""
    return {
        "route_id": "E",
        "trip_id": f"t-{observed_at}-{provenance}",
        "arrival": NOW + 120.0,
        "observed_at": observed_at,
        "provenance": provenance,
    }


def _index(*rows):
    """The subway and railroad index shape, {station: {bucket: [row]}}."""
    return {"G08": {"Northbound": list(rows)}}


def _block(*, ok=True, fetched_at=NOW, retained_since=None):
    """A per-system freshness block, as pollers._system_freshness writes one."""
    return {
        "fetched_at": fetched_at,
        "feed_timestamp": FRESH,
        "ok": ok,
        "retained_since": retained_since,
        "routes": [],
    }


def _state(**attributes):
    """A stand-in for app.state carrying only what _served_arrival_systems reads."""
    return types.SimpleNamespace(**attributes)


def _serving_nothing(state, now=NOW):
    return _systems_serving_nothing_current(_served_arrival_systems(state), now)


# ---------------------------------------------------------------------------
# Which systems are read
# ---------------------------------------------------------------------------


def test_a_state_before_the_first_poll_is_quiet():
    """Every index absent, the shape app.state has before any poll lands. The three
    single-feed systems are always listed; with nothing under them, nothing fires."""
    systems = _served_arrival_systems(_state())
    assert sorted(systems) == ["ferry", "njt", "path"]
    assert _systems_serving_nothing_current(systems, NOW) == []


def test_empty_indexes_are_quiet():
    state = _state(
        feed_cache={"subways": {"systems": {}}, "railroads": {"systems": {}}},
        subway_arrivals_by_system={},
        railroad_arrivals={},
        path_arrivals={},
        ferry_arrivals={},
        njt_arrivals={},
    )
    assert _serving_nothing(state) == []


def test_every_arrivals_index_is_read():
    """One system per subway group and per railroad, whether it is known by its rows,
    by its block, or by both; plus the three single-feed systems."""
    state = _state(
        feed_cache={
            "subways": {"systems": {"ACE": _block(), "SIR": _block()}},
            "railroads": {"systems": {"LIRR": _block(), "MNR": _block()}},
        },
        subway_arrivals_by_system={"ACE": _index(_row()), "NQRW": _index(_row())},
        railroad_arrivals={"LIRR": _index(_row())},
        path_arrivals={},
        ferry_arrivals={},
        njt_arrivals={},
    )
    assert sorted(_served_arrival_systems(state)) == [
        "ferry",
        "njt",
        "path",
        "railroad:LIRR",
        "railroad:MNR",
        "subway:ACE",
        "subway:NQRW",
        "subway:SIR",
    ]


# ---------------------------------------------------------------------------
# The row rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "age_gated", "qualified"),
    [
        pytest.param(_row(), True, False, id="reported-and-current"),
        pytest.param(_row(provenance="retained"), True, True, id="retained"),
        pytest.param(_row(provenance="unknown"), True, True, id="unknown"),
        pytest.param(_row(provenance="placed"), True, True, id="placed"),
        pytest.param(
            {"route_id": "E", "trip_id": "t", "arrival": NOW + 120.0},
            True,
            True,
            id="no-provenance-key-at-all",
        ),
        pytest.param(_row(observed_at=None), True, True, id="gated-and-undated"),
        pytest.param(_row(observed_at=None), False, False, id="ungated-and-undated"),
        # THE BOUNDARY, on both sides. `>=` matches the frontend, which flags at
        # age >= FEED_STALE_AFTER_S, so a row exactly 90 seconds old is qualified.
        pytest.param(_row(observed_at=NOW - FEED_STALE_AFTER_S), True, True, id="exactly-90s"),
        pytest.param(
            _row(observed_at=NOW - FEED_STALE_AFTER_S + 0.5), True, False, id="just-under-90s"
        ),
        # Ungated excuses only a MISSING clock. A row that carries one is aged by it,
        # and a carried-forward row is qualified whatever its system dates.
        pytest.param(_row(observed_at=OLD), False, True, id="ungated-but-dated-and-old"),
        pytest.param(
            _row(observed_at=None, provenance="retained"), False, True, id="ungated-but-retained"
        ),
    ],
)
def test_a_one_row_system_follows_the_row_rule(row, age_gated, qualified):
    systems = {"s": _ServedSystem(_index(row), age_gated)}
    assert _systems_serving_nothing_current(systems, NOW) == (["s"] if qualified else [])


def test_one_current_row_keeps_a_whole_system_quiet():
    """The healthy LIRR shape: most predictions old, a few current. Quiet, and the
    current row is the whole reason, which the second half shows by taking it away."""
    old = [_row(observed_at=NOW - age) for age in (95.0, 300.0, 3_600.0, 52_538.0)]
    systems = {"railroad:LIRR": _ServedSystem(_index(*old, _row()), True)}
    assert _systems_serving_nothing_current(systems, NOW) == []
    systems = {"railroad:LIRR": _ServedSystem(_index(*old), True)}
    assert _systems_serving_nothing_current(systems, NOW) == ["railroad:LIRR"]


def test_metro_north_rows_carry_no_clock_and_stay_quiet(monkeypatch):
    """Metro-North dates nothing (design 3.3), so its rows are undated by design rather
    than by accident. The exemption is read off feeds.RAILROAD_FRESHNESS_SYSTEMS and
    restated nowhere in the rule, which the second half proves by moving the SET: the
    same rows fire the moment the set claims Metro-North dates them."""
    assert "MNR" not in feeds.RAILROAD_FRESHNESS_SYSTEMS
    state = _state(
        feed_cache={"railroads": {"systems": {"MNR": _block()}}},
        railroad_arrivals={"MNR": _index(_row(observed_at=None), _row(observed_at=None))},
    )
    assert _serving_nothing(state) == []

    monkeypatch.setattr(status_routes, "RAILROAD_FRESHNESS_SYSTEMS", frozenset({"LIRR", "MNR"}))
    assert _serving_nothing(state) == ["railroad:MNR"]


# ---------------------------------------------------------------------------
# Every case the code exists for
# ---------------------------------------------------------------------------


def test_the_f03_world_fires_for_the_old_group_only():
    """A group polled fresh (ok, fetched now, never retained) whose content is ten
    minutes old. Every measure that predates 6.1 calls it healthy, and its riders are
    served nothing current."""
    state = _state(
        feed_cache={"subways": {"systems": {"ACE": _block(), "NQRW": _block()}}},
        subway_arrivals_by_system={"ACE": _index(_row(observed_at=OLD)), "NQRW": _index(_row())},
    )
    assert _serving_nothing(state) == ["subway:ACE"]


def test_a_retained_group_fires_and_its_healthy_sibling_does_not():
    """A failed group carried forward, stamped by the production merge rather than by
    hand: merge_system_generations marks every carried row `retained`, so every row the
    group serves is qualified however young its clock."""
    previous = {"ACE": _index(_row()), "NQRW": _index(_row())}
    merged, retained_since = feeds.merge_system_generations(
        {"NQRW": _index(_row())}, previous, ["ACE"], {}, NOW, 600.0
    )
    assert retained_since == {"ACE": NOW}
    state = _state(
        feed_cache={
            "subways": {
                "systems": {
                    "ACE": _block(ok=False, fetched_at=NOW - 20.0, retained_since=NOW),
                    "NQRW": _block(),
                }
            }
        },
        subway_arrivals_by_system=merged,
    )
    assert _serving_nothing(state) == ["subway:ACE"]


def test_a_system_serving_only_unknown_provenance_fires():
    """`unknown` is what a row that reached a surface around a decoder says about
    itself, and the rule reads it the pessimistic way the client's fail-safe does
    (design 3.1)."""
    state = _state(ferry_arrivals={"18": {"East River": [_row(provenance="unknown")] * 3}})
    assert _serving_nothing(state) == ["ferry"]


def test_a_gated_system_serving_only_undated_rows_fires():
    """PATH dates every prediction by its own trip_update.timestamp, so a PATH row with
    no clock is an anomaly (design 3.2, clause c), unlike a Metro-North row."""
    state = _state(
        path_arrivals={"26733": {"To NY": [_row(observed_at=None), _row(observed_at=None)]}}
    )
    assert _serving_nothing(state) == ["path"]


def test_path_old_trip_clocks_fire_under_a_fresh_bridge_write_time():
    """The PATH bridge rewrites its feed every ~15 seconds, so the envelope's
    feed_timestamp is fresh whether or not anything upstream moved. The rule reads the
    rows' own trip clocks and never that write time."""
    state = _state(
        feed_cache={"path": {"data": [], "fetched_at": NOW, "feed_timestamp": NOW, "error": None}},
        path_arrivals={"26733": {"To NY": [_row(observed_at=OLD), _row(observed_at=NOW - 120.0)]}},
    )
    assert _serving_nothing(state) == ["path"]


def test_a_total_outage_fires_once_its_rows_age_past_the_threshold():
    """A total subway failure returns before any merge runs, so nothing is stamped
    `retained`: the rows stay `reported` and only their age can qualify them. Quiet
    while the last decode is young, and firing once it is 90 seconds old. Rows present
    also keep the failed block out of the dropped rung."""
    failed = _block(ok=False, fetched_at=NOW - 30.0)
    state = _state(
        feed_cache={"subways": {"systems": {"ACE": failed}}},
        subway_arrivals_by_system={"ACE": _index(_row(observed_at=NOW - 35.0))},
    )
    assert _serving_nothing(state) == []
    assert _serving_nothing(state, now=NOW + 55.0) == ["subway:ACE"]


def test_a_dropped_system_fires_on_the_cap_poll_and_on_the_poll_after():
    """The ladder's last rung (design 3.4, step 5), through the production merge on two
    consecutive failed polls, each block written as pollers._system_freshness writes it.

    On the first the cap fires: the rows go and the block reads retained_since None. On
    the second, pollers._merge_feed_systems hands the merge no previous clock for the
    group (it carries only a SET retained_since forward), so the merge opens a NEW
    window with nothing to carry and the block reads retained_since set again. The
    first version of _was_dropped read that second block as "inside the window" and went
    quiet. The group has been failing past the cap on both polls, and its last decode,
    which does not move while it fails, says so on both.
    test_healthz_names_a_dropped_group_on_every_poll_of_its_outage in test_api.py walks
    the same thing through the real refresher for 45 minutes.
    """
    poll_s = 22.0
    last_decode = NOW - FEED_RETENTION_MAX_S - poll_s
    merged, retained_since = feeds.merge_system_generations(
        {"NQRW": _index(_row())},
        {"ACE": _index(_row(provenance="retained")), "NQRW": _index(_row())},
        ["ACE"],
        {"ACE": NOW - FEED_RETENTION_MAX_S},
        NOW,
        FEED_RETENTION_MAX_S,
    )
    assert "ACE" not in merged and retained_since == {}
    state = _state(
        feed_cache={
            "subways": {
                "systems": {"ACE": _block(ok=False, fetched_at=last_decode), "NQRW": _block()}
            }
        },
        subway_arrivals_by_system=merged,
    )
    assert _serving_nothing(state) == ["subway:ACE"]

    later = NOW + poll_s
    merged, retained_since = feeds.merge_system_generations(
        {"NQRW": _index(_row(observed_at=later - 5.0))},
        merged,
        ["ACE"],
        {},
        later,
        FEED_RETENTION_MAX_S,
    )
    assert "ACE" not in merged and retained_since == {"ACE": later}
    state.subway_arrivals_by_system = merged
    state.feed_cache["subways"]["systems"]["ACE"] = _block(
        ok=False, fetched_at=last_decode, retained_since=later
    )
    assert _serving_nothing(state, now=later) == ["subway:ACE"]


# ---------------------------------------------------------------------------
# What serves nothing without anything having been taken away
# ---------------------------------------------------------------------------


def test_a_system_that_never_decoded_has_dropped_nothing():
    """ok False on a block with no decode behind it: a group down since this process
    started. It serves nothing, but nothing was taken away, and fetched_at is what keeps
    it out of the dropped rung, HOWEVER LONG it fails. That is the blind spot the
    contract monitor's note for this code names: after a deploy, a group that was
    already failing is invisible here until it decodes."""
    state = _state(
        feed_cache={"subways": {"systems": {"SIR": _block(ok=False, fetched_at=None)}}},
        subway_arrivals_by_system={},
    )
    assert _serving_nothing(state) == []
    assert _serving_nothing(state, now=NOW + 10 * FEED_RETENTION_MAX_S) == []


def test_a_healthy_group_with_nothing_running_is_quiet():
    state = _state(
        feed_cache={"subways": {"systems": {"SIR": _block()}}},
        subway_arrivals_by_system={"SIR": {}},
    )
    assert _serving_nothing(state) == []


def test_a_failing_group_inside_its_window_with_nothing_to_carry_is_quiet():
    """Retention starts whether or not anything was carried (merge_system_generations
    records the clock for a group that went down holding an empty list), so a group can
    be failing, inside its window, and serving nothing. Nothing has been dropped YET.

    It becomes the dropped rung the moment its last decode is FEED_RETENTION_MAX_S old,
    the merge's own `>=` on the merge's own value, and the retention clock still set on
    its block has no say: the same block, clock and all, is quiet half a second before
    that edge and fires on it."""
    last_decode = NOW - 52.0
    state = _state(
        feed_cache={
            "subways": {
                "systems": {
                    "SIR": _block(ok=False, fetched_at=last_decode, retained_since=NOW - 30.0)
                }
            }
        },
        subway_arrivals_by_system={},
    )
    assert _serving_nothing(state) == []
    assert _serving_nothing(state, now=last_decode + FEED_RETENTION_MAX_S - 0.5) == []
    assert _serving_nothing(state, now=last_decode + FEED_RETENTION_MAX_S) == ["subway:SIR"]


def test_a_total_outage_leaves_an_empty_entry_undropped():
    """THE CASE THAT SHAPED _was_dropped. A total outage returns before any merge, so
    _mark_all_systems_failed leaves every block ok False with retained_since None, the
    very pair the cap leaves, while the index keeps every group's entry. A group whose
    entry was already empty has had nothing taken from anyone. This is the F10
    reproduction's world (eight groups, empty arrival entries, all failed), and a rule
    that read the block alone reported all eight as dropped. The second half is the
    same block over NO entry, a group failing past the cap that the merge has emptied,
    and does count."""
    last_decode = NOW - FEED_RETENTION_MAX_S - 120.0
    state = _state(
        feed_cache={"subways": {"systems": {"SIR": _block(ok=False, fetched_at=last_decode)}}},
        subway_arrivals_by_system={"SIR": {}},
    )
    assert _serving_nothing(state) == []
    state.subway_arrivals_by_system = {}
    assert _serving_nothing(state) == ["subway:SIR"]


def test_a_single_failed_njt_poll_is_quiet_with_or_without_rows():
    """NJ Transit keeps last-known rows on a failed poll and never retains, so its
    block's ok False with retained_since None means one failed poll there, not a cap
    that fired. Fresh rows keep it quiet; and on an empty night the same block must not
    read as dropped observations, which is why the rule is handed no NJ Transit block."""
    state = _state(
        feed_cache={"njt": {"systems": {"njt": _block(ok=False, fetched_at=NOW - 20.0)}}},
        njt_arrivals={"105": [_row(observed_at=NOW - 25.0)]},
    )
    assert _serving_nothing(state) == []
    state.njt_arrivals = {}
    assert _serving_nothing(state) == []


# ---------------------------------------------------------------------------
# The threshold, re-derived
# ---------------------------------------------------------------------------


def test_the_whole_system_threshold_is_the_measured_one():
    """The three numbers at models.HEALTH_OBSERVATIONS_QUALIFIED, from the capture.

    526 of LIRR's 765 served rows are qualified on an ordinary evening, so an any-row
    rule would publish this code permanently; 36 of its 97 trips carry a current
    prediction, so the whole-system rule is quiet. Aged against the golden's own `now`,
    never against today's date, so this holds on any day it runs.
    """
    golden = json.loads((FIX / "railroad_lirr_arrivals_expected.json").read_text())
    now = golden["now"]
    rows = list(feeds.iter_rows(golden["arrivals"]))
    qualified = [row for row in rows if status_routes._row_is_qualified(row, True, now)]
    assert (len(qualified), len(rows)) == (526, 765)
    trips = {row["trip_id"] for row in rows}
    current = {
        row["trip_id"] for row in rows if not status_routes._row_is_qualified(row, True, now)
    }
    assert (len(current), len(trips)) == (36, 97)

    state = _state(railroad_arrivals={golden["system"]: golden["arrivals"]})
    assert _serving_nothing(state, now=now) == []
