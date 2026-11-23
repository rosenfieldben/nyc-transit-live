"""The 2026-11-22 incident's reduced capture, and every count the records quote from it.

fixtures/njt_drift_2026-11-22.json is what production served that evening, before and after
a redeploy, set beside NJ Transit's public schedule (its `about` says how it was taken).
The ledger, feeds/njt.py and the pull request quote numbers from it; this module re-derives
each one, so a number in a record is a test and not a memory. It pins no code.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

CAPTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "njt_drift_2026-11-22.json").read_text(encoding="utf-8")
)
TRIPS = CAPTURE["before"]["trips"]
REFUTED = [t for t in TRIPS if t["joined"]]
UNJOINED = [t for t in TRIPS if not t["joined"]]


def _same(a: str, b: str) -> bool:
    """Case apart, and the full stop the display rule keeps: the app's own rewording."""
    return a.casefold().rstrip(".") == b.casefold().rstrip(".")


def test_the_sample():
    assert (len(TRIPS), len(REFUTED), len(UNJOINED)) == (50, 42, 8)
    assert len(CAPTURE["before"]["series"]) == 30
    assert CAPTURE["before"]["sweep"]["boards"] == 165
    assert CAPTURE["publication"]["sha256"] == (
        "99ec46b5279860edada2b23a8c7a33e6c660a3464b34ab9ade5c8b4d4099c211"
    )


def test_every_joined_train_was_served_as_another():
    """Not one right number, and two right destinations of 42, by coincidence: each of the
    two is under a wrong number as well."""
    assert sum(t["served_number"] == t["number"] for t in REFUTED) == 0
    right = [t for t in REFUTED if _same(t["served_destination"], t["destination"])]
    assert len(right) == 2
    assert all(t["served_number"] != t["number"] for t in right)


def test_every_unjoined_train_kept_its_number_and_lost_its_destination():
    """Route 5, all of it: the schedule held had no such ids, so the number fell back to the
    feed's own entity.id and the destination to the route id and that number."""
    assert {t["line"] for t in UNJOINED} == {"Main/Bergen County Line"}
    assert all(t["served_number"] == t["number"] for t in UNJOINED)
    assert all(t["served_destination"] == f"5 {t['number']}" for t in UNJOINED)


def test_the_line_was_right_on_every_row():
    assert all(t["route_matches"] for t in TRIPS)


def test_part_1a_the_destination_is_the_name_of_the_last_stop():
    """Against the schedule, 42 of 42 and 8 of 8, and 1441 of 1441 rail trips in the whole
    publication. Against the feed, the last call a trip showed on production's own boards
    was its terminal on 39 of 39 whose terminal row the six-row board still showed."""
    assert sum(t["destination"] == t["last_stop"] for t in REFUTED) == 42
    assert sum(t["destination"] == t["last_stop"] for t in UNJOINED) == 8
    assert CAPTURE["publication"]["rail_trips_headsigned_by_their_last_stop"] == 1441
    assert CAPTURE["publication"]["rail_trips"] == 1441
    assert Counter(t["last_call_on_the_boards"] for t in TRIPS) == {
        "terminal": 39,
        "past-the-cap": 5,
        None: 6,
    }


def test_part_1b_the_stale_schedule_rejoined_by_number_does_not_hold():
    """Of the 42 refuted numbers, one is in the stale publication under two headsigns, one is
    not in it, and one of the other forty goes somewhere else now."""
    verdicts = Counter()
    wrong = []
    for trip in REFUTED:
        pairs = trip["stale_by_number"]
        if len(pairs) != 1:
            verdicts["absent" if not pairs else "ambiguous"] += 1
        elif pairs[0][0] == trip["destination"]:
            verdicts["equal"] += 1
        else:
            verdicts["different"] += 1
            wrong.append((trip["number"], pairs[0][0], trip["destination"]))
    assert verdicts == {"equal": 39, "ambiguous": 1, "absent": 1, "different": 1}
    assert len(wrong) == 1


def test_part_1c_every_row_has_a_line_and_a_number():
    assert all(t["line"] and t["number"] for t in TRIPS)


def test_the_two_penn_boards_were_wrong_on_every_row_and_then_right_on_every_row():
    before = [row for rows in CAPTURE["before"]["boards"].values() for row in rows]
    after = [row for rows in CAPTURE["after"]["boards"].values() for row in rows]
    assert list(CAPTURE["before"]["boards"]) == ["New York Penn Station", "Newark Penn Station"]
    assert [len(before), sum(_same(row[2], row[3]) for row in before)] == [12, 0]
    assert [len(after), sum(_same(row[2], row[3]) for row in after)] == [12, 12]
    # Every row on the New York board was a train ending there, and none said so.
    assert {row[3] for row in CAPTURE["before"]["boards"]["New York Penn Station"]} == {
        "NEW YORK PENN STATION"
    }


def test_the_count_before_and_after_the_redeploy():
    counts = [row[1] for row in CAPTURE["before"]["series"]]
    assert (min(counts), max(counts)) == (37, 40)
    assert [row[1:] for row in CAPTURE["after"]["series"]] == [[0, 29, 29, 29]] * 3
