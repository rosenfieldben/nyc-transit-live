"""End-to-end test of the realtime capture script (backend/scripts/gen_njt_rt_fixture.py).

THE SCRIPT HAD NO TEST AND THAT IS WHY IT SHIPPED BROKEN. It needs credentials to
run, so nothing in CI could execute it, and the first person to try it with real
credentials hit a gate that refused every capture and blamed a cause it had never
measured. The network is the only part that genuinely needs credentials; the join
measurement, the trim widening and the fixture writing are all pure, so with the
download faked the whole flow runs here in milliseconds.

What this pins is the property the whole capture rests on: THE PAIR IT WRITES
JOINS. Not "the script does not crash", which the broken version also satisfied.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2 as pb

_GEN_PATH = Path(__file__).resolve().parent.parent / "scripts" / "gen_njt_rt_fixture.py"
_spec = importlib.util.spec_from_file_location("gen_njt_rt_fixture", _GEN_PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

NOW = 1786061700.0  # 2026-08-06 18:15 EDT, the rush probe's own hour

# A publication big enough to trim meaningfully: 4 routes, 20 trips each, so the
# two-lexicographically-first-per-route rule keeps 8 and leaves 72 behind. That
# gap is the defect's whole mechanism.
ROUTES = ["1", "5", "6", "13"]
TRIPS_PER_ROUTE = 20
STOPS = {
    "109": "New York Penn Station",
    "112": "Newark Penn Station",
    "38": "Hoboken",
    "140": "Suffern",
    "200": "Otisville",
    "201": "Port Jervis",
    "300": "Westwood",
}


def _csv(fieldnames, rows) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def _archive(service_date: str = "20991231") -> bytes:
    """A synthetic NJ Transit publication, in the shape the real one has: no
    calendar.txt, no feed_info.txt, route_type 113, additive calendar_dates."""
    trips, stop_times = [], []
    for route in ROUTES:
        for index in range(TRIPS_PER_ROUTE):
            trip_id = f"{route}-{index:03d}"
            # Two trips per route reach the west-of-Hudson stops and are headsigned
            # Port Jervis; one runs back headsigned Hoboken, which is the return
            # working that used to cancel the exclusivity heuristic.
            if route in ("5", "6") and index == 19:
                headsign, calls = "Port Jervis", ["38", "140", "200", "201"]
            elif route in ("5", "6") and index == 18:
                headsign, calls = "Hoboken", ["201", "200", "140", "38"]
            elif route == "13":
                headsign, calls = "Westwood", ["38", "300"]
            else:
                headsign, calls = "New York", ["112", "109"]
            trips.append(
                {
                    "route_id": route,
                    "service_id": "SVC",
                    "trip_id": trip_id,
                    "trip_headsign": headsign,
                    "direction_id": "0",
                    "trip_short_name": f"{index:03d}",
                }
            )
            for seq, stop_id in enumerate(calls, start=1):
                stop_times.append(
                    {
                        "trip_id": trip_id,
                        "arrival_time": f"{7 + seq:02d}:00:00",
                        "departure_time": f"{7 + seq:02d}:00:30",
                        "stop_id": stop_id,
                        "stop_sequence": str(seq),
                    }
                )
    members = {
        "agency.txt": _csv(
            ["agency_id", "agency_name", "agency_url", "agency_timezone"],
            [
                {
                    "agency_id": "NJT",
                    "agency_name": "NJ TRANSIT",
                    "agency_url": "https://x",
                    "agency_timezone": "America/New_York",
                }
            ],
        ),
        "routes.txt": _csv(
            [
                "route_id",
                "route_short_name",
                "route_long_name",
                "route_type",
                "route_color",
                "route_text_color",
            ],
            [
                {
                    "route_id": r,
                    "route_short_name": f"R{r}",
                    "route_long_name": f"Line {r}",
                    "route_type": "113",
                    "route_color": "EF3E42",
                    "route_text_color": "",
                }
                for r in ROUTES
            ],
        ),
        "stops.txt": _csv(
            ["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon"],
            [
                {
                    "stop_id": sid,
                    "stop_code": sid,
                    "stop_name": name,
                    "stop_lat": f"40.7{int(sid) % 100:02d}",
                    "stop_lon": f"-74.0{int(sid) % 100:02d}",
                }
                for sid, name in STOPS.items()
            ],
        ),
        "trips.txt": _csv(
            [
                "route_id",
                "service_id",
                "trip_id",
                "trip_headsign",
                "direction_id",
                "trip_short_name",
            ],
            trips,
        ),
        "stop_times.txt": _csv(
            ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
            stop_times,
        ),
        "calendar_dates.txt": _csv(
            ["service_id", "date", "exception_type"],
            [{"service_id": "SVC", "date": service_date, "exception_type": "1"}],
        ),
        "shapes.txt": _csv(
            ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"],
            [
                {
                    "shape_id": "s1",
                    "shape_pt_sequence": "1",
                    "shape_pt_lat": "40.7",
                    "shape_pt_lon": "-74.0",
                }
            ],
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return buffer.getvalue()


# The trips "in flight" when the capture is taken. DELIBERATELY THE HIGH-NUMBERED
# ONES, so they cannot overlap the two-lexicographically-first-per-route trim.
# That is the real capture's shape: 165 trips moving, 25 in the trim, zero shared.
IN_FLIGHT = [f"{route}-{index:03d}" for route in ROUTES for index in range(10, 20)]


def _trip_updates(trip_ids=None, *, header_ts: float = NOW) -> bytes:
    """A capture carrying every trap shape the generator's floors require."""
    trip_ids = list(IN_FLIGHT if trip_ids is None else trip_ids)
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(header_ts)
    for position, trip_id in enumerate(trip_ids):
        entity = feed.entity.add()
        entity.id = trip_id.split("-")[1]  # equals trip_short_name, as the probe found
        tu = entity.trip_update
        tu.trip.trip_id = trip_id
        tu.trip.route_id = trip_id.split("-")[0]
        canceled = position % 10 == 0  # a phantom every tenth trip
        if canceled:
            tu.trip.schedule_relationship = pb.TripDescriptor.CANCELED
        for seq, (stop_id, offset) in enumerate((("112", -240), ("109", 360)), start=1):
            stu = tu.stop_time_update.add()
            stu.stop_sequence = seq * 10
            stu.stop_id = stop_id
            bare = position % 7 == 3 and seq == 1  # the times-less SKIPPED variant
            if canceled or bare:
                stu.schedule_relationship = pb.TripUpdate.StopTimeUpdate.SKIPPED
            if not bare:
                stu.arrival.time = int(header_ts + offset)
                stu.departure.time = int(header_ts + offset + 30)
    return feed.SerializeToString()


def _alerts() -> bytes:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(NOW)
    entity = feed.entity.add()
    entity.id = "a1"
    period = entity.alert.active_period.add()
    period.start = int(NOW) - 3600
    entity.alert.header_text.translation.add().text = "Delays"
    return feed.SerializeToString()


@pytest.fixture
def capture(tmp_path, monkeypatch):
    """Point the generator at a temp directory and fake the three downloads."""
    out = tmp_path / "fixtures"
    static_out = out / "njt_gtfs"
    monkeypatch.setattr(gen, "OUT_DIR", out)
    monkeypatch.setattr(gen, "STATIC_OUT_DIR", static_out)

    def run(tu=None, archive=None, received_at=NOW + 15):
        monkeypatch.setattr(
            gen,
            "_download",
            lambda: (
                _trip_updates() if tu is None else tu,
                _alerts(),
                _archive() if archive is None else archive,
                received_at,
            ),
        )
        return gen.main(), out, static_out

    return run


def _rows(path: Path) -> list[dict]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def test_a_healthy_capture_writes_a_pair_that_joins(capture):
    """THE WHOLE CLAIM. The script runs end to end and the two fixtures it leaves
    behind refer to the same trips.

    Under the old code this capture was impossible: none of the in-flight trips
    are among the two-lexicographically-first per route, so the join measured
    0.0000 and the script refused before writing anything.
    """
    code, out, static_out = capture()
    assert code == 0, "a capture carrying every required trap shape must be accepted"

    # Both fixture sets exist.
    assert (out / "njt_tu.pb").exists()
    assert (out / "njt_alerts.pb").exists()
    expected = json.loads((out / "njt_tu_expected.json").read_text())

    # AND THEY JOIN. Every trip in the capture is in the written trips.txt.
    written = {row["trip_id"] for row in _rows(static_out / "trips.txt")}
    assert set(IN_FLIGHT) <= written, (
        "the re-trim must widen the static fixture around the capture's trips; "
        f"missing {sorted(set(IN_FLIGHT) - written)[:5]}"
    )
    # The decoded golden reflects that: every placed train took its headsign from
    # the static rather than from the synthesized "<route> <train number>"
    # fallback _identity uses for a trip no schedule knows. That fallback is what
    # a non-joining pair produces, and it is what these goldens would have been
    # silently measuring.
    assert expected["trains"], "the capture must place trains"
    real_headsigns = {row["trip_headsign"] for row in _rows(static_out / "trips.txt")}
    for train in expected["trains"]:
        assert train["headsign"] in real_headsigns, (
            f"train {train['trip_id']} has headsign {train['headsign']!r}, which is not one "
            "the static publishes; that is the synthesized fallback and means it did not join"
        )
        assert train["headsign"] != f"{train['route_id']} {train['train_num']}"


def test_the_written_static_is_still_a_trim_and_still_referentially_intact(capture):
    """Widening is not "keep everything". The must-include set and the capture's
    trips are kept; the rest of the publication is not, and no stop_times row may
    point at a stop that is not in the written stops.txt."""
    code, _out, static_out = capture()
    assert code == 0
    trips = _rows(static_out / "trips.txt")
    stops = {row["stop_id"] for row in _rows(static_out / "stops.txt")}
    stop_times = _rows(static_out / "stop_times.txt")

    assert len(trips) < len(ROUTES) * TRIPS_PER_ROUTE, "it must still be a trim"
    assert {row["stop_id"] for row in stop_times} <= stops, (
        "every stop_times row must point at a stop the fixture carries"
    )
    # calendar_dates goes in WHOLE: it is this feed's only schedule and the
    # service-date guard reads the maximum over the entire table.
    assert _rows(static_out / "calendar_dates.txt")


def test_the_west_of_hudson_stations_survive_the_widened_trim(capture):
    """The mandated stops are still mandated. The synthetic publication carries
    Port Jervis service in BOTH directions, which is the shape that used to empty
    the exclusivity heuristic and take its guard with it."""
    code, _out, static_out = capture()
    assert code == 0
    stops = {row["stop_id"] for row in _rows(static_out / "stops.txt")}
    assert {"200", "201"} <= stops, "Otisville and Port Jervis must be in the fixture"
    headsigns = {row["trip_headsign"] for row in _rows(static_out / "trips.txt")}
    assert "Port Jervis" in headsigns, "and a trip must still NAME the line"


def test_an_empty_overnight_capture_is_refused_with_what_it_measured(capture, capsys):
    """The 13-byte valid feed. Refused, and the message says the count it measured
    rather than diagnosing a cause."""
    empty = pb.FeedMessage()
    empty.header.gtfs_realtime_version = "2.0"
    empty.header.timestamp = int(NOW)
    code, _out, static_out = capture(tu=empty.SerializeToString())
    assert code == 1
    output = capsys.readouterr().out
    assert "MEASURED 0 trip_updates" in output
    assert not (static_out / "trips.txt").exists(), "a refused capture writes nothing"


def test_a_capture_that_does_not_match_the_publication_reports_the_rate_not_a_cause(
    capture, capsys
):
    """THE REFUSAL THAT BLOCKED THE FIRST REAL CAPTURE, rewritten.

    A capture whose trips are absent from the publication is a real condition
    worth refusing. What it must NOT do is assert why. The old message named a
    schedule rollover as fact, having measured nothing of the kind, and it was
    wrong: the archive had re-downloaded byte-identical to the probe's.
    """
    stranger = _trip_updates([f"UNKNOWN-{i:03d}" for i in range(40)])
    code, _out, static_out = capture(tu=stranger)
    assert code == 1
    output = capsys.readouterr().out

    assert "MEASURED join rate 0.0000" in output, "it must lead with the measurement"
    assert "40 trip_updates matched" in output or "0 of 40" in output
    assert "unmatched trip_ids" in output, "and name the ids, so the reader can look"
    assert "none of them measured here" in output, (
        "the candidate causes must be labelled as unmeasured"
    )
    assert not (static_out / "trips.txt").exists()


def test_the_join_gate_is_measured_against_the_publication_not_the_trim(capture):
    """THE DEFECT ITSELF, pinned as arithmetic.

    Every trip in this capture exists in the publication and none is in the
    two-per-route trim. Against the publication the join is 1.0 and the capture is
    accepted; against the trim it would be 0.0 and refused. That difference is the
    entire bug, so it gets its own test rather than only being implied by the
    happy path.
    """
    archive = _archive()
    parsed = gen.njt_static._parse_zip(io.BytesIO(archive))
    publication = gen.njt_static.build_njt_trip_index(parsed["trips"])
    assert all(t in publication for t in IN_FLIGHT), "the capture matches the publication"

    code, _out, static_out = capture()
    assert code == 0

    # And the trim the OLD gate would have measured against shares nothing with it.
    trim_only = gen.trim.select_trim(
        trips=[
            {"trip_id": t, "route_id": t.split("-")[0], "trip_headsign": ""} for t in publication
        ],
        calls={t: [{"stop_id": "109", "stop_sequence": "1"}] for t in publication},
        trips_by_route={
            r: [
                {"trip_id": t, "route_id": r, "trip_headsign": ""}
                for t in publication
                if t.startswith(f"{r}-")
            ]
            for r in ROUTES
        },
        pj_keep=set(),
        mandated_stops=[],
    )
    assert not (set(IN_FLIGHT) & trim_only), (
        "the in-flight trips share nothing with the plain trim, which is exactly why "
        "joining against the trim measured zero"
    )


def _added_entities(feed, count, *, start=90000, empty_trip_id=True):
    """`count` ADDED trip_updates, in NJ Transit's real shape: empty trip_id, a
    distinct entity.id per train."""
    for index in range(count):
        entity = feed.entity.add()
        entity.id = str(start + index)
        tu = entity.trip_update
        tu.trip.trip_id = "" if empty_trip_id else f"EXTRA-{index}"
        tu.trip.route_id = "1"
        tu.trip.schedule_relationship = pb.TripDescriptor.ADDED
        for seq, (stop_id, offset) in enumerate((("112", -240), ("109", 360)), start=1):
            stu = tu.stop_time_update.add()
            stu.stop_sequence = seq * 10
            stu.stop_id = stop_id
            stu.arrival.time = int(NOW + offset)
            stu.departure.time = int(NOW + offset + 30)


def _capture_with_added(added_count=36, *, empty_trip_id=True):
    """Tonight's shape: every scheduled trip joins, plus N ADDED extras."""
    feed = pb.FeedMessage()
    feed.ParseFromString(_trip_updates())
    _added_entities(feed, added_count, empty_trip_id=empty_trip_id)
    return feed.SerializeToString()


def test_added_trips_are_excluded_from_the_join_denominator(capture, capsys):
    """THE GATE THAT REFUSED A PERFECT CAPTURE.

    NJ Transit publishes ADDED trips with an EMPTY trip_id, so they can never match
    a trip in the schedule: they are definitionally unjoinable. Counting them in
    the denominator turned a live feed whose every joinable trip joined into a
    measured 0.78 failure. The real numbers were 164 trip_updates, 128 joined, 36
    ADDED, and 164 - 128 = 36 exactly.

    Here every scheduled trip joins and 36 extras ride along. Over the joinable set
    the rate is 1.0000, so the capture must be ACCEPTED; over all trip_updates it
    would be 40/76 and refused.
    """
    code, _out, static_out = capture(tu=_capture_with_added(36))
    output = capsys.readouterr().out

    assert code == 0, "a capture whose every joinable trip joins must not be refused"
    assert "40/40 SCHEDULED-or-CANCELED" in output, (
        f"the denominator must be the joinable trips only; got:\n{output}"
    )
    assert "(1.0000)" in output
    assert "ADDED, definitionally unjoinable: 36, all with empty trip_id: yes" in output
    assert (static_out / "trips.txt").exists(), "and the fixtures are written"


def test_a_nonempty_trip_id_on_an_added_trip_is_reported_as_a_new_fact(capture, capsys):
    """Worth its own printed note, because it would mean NJ Transit has started
    publishing extras against the schedule and they might be joinable after all.
    Not a refusal: a new fact is for a human to read, not for the script to decide."""
    code, _out, _static = capture(tu=_capture_with_added(4, empty_trip_id=False))
    output = capsys.readouterr().out
    assert code == 0
    assert "ADDED, definitionally unjoinable: 4, all with empty trip_id: no" in output
    assert "carry a NONEMPTY trip_id" in output


def test_an_empty_trip_id_on_a_scheduled_trip_is_reported_as_a_new_fact(capture, capsys):
    """The mirror. A trip claiming to be in the schedule with no way to say which
    is the opposite new fact, and it is counted as unjoined rather than excused."""
    feed = pb.FeedMessage()
    feed.ParseFromString(_trip_updates())
    entity = feed.entity.add()
    entity.id = "5555"
    tu = entity.trip_update
    tu.trip.trip_id = ""  # SCHEDULED by omission, with no id
    tu.trip.route_id = "1"
    for seq, (stop_id, offset) in enumerate((("112", -240), ("109", 360)), start=1):
        stu = tu.stop_time_update.add()
        stu.stop_sequence = seq * 10
        stu.stop_id = stop_id
        stu.arrival.time = int(NOW + offset)
        stu.departure.time = int(NOW + offset + 30)

    capture(tu=feed.SerializeToString())
    output = capsys.readouterr().out
    assert "carry an EMPTY trip_id, which is a new fact" in output
    assert "40/41 SCHEDULED-or-CANCELED" in output, "and it counts against the join"


def test_the_captured_extras_reach_the_golden_as_distinct_trains(capture):
    """END TO END: 36 extras with empty trip_ids go in, and the committed golden
    carries 36 distinct trains rather than one.

    The count is what makes this discriminating. A single ADDED trip, which is what
    the trap matrix carries, can never reveal a collapse: you need at least two
    sharing the empty id before merging them is visible.
    """
    code, out, _static = capture(tu=_capture_with_added(36))
    assert code == 0
    expected = json.loads((out / "njt_tu_expected.json").read_text())
    shapes = expected["capture_shapes"]
    assert shapes["added_trips"] == 36
    assert shapes["added_with_empty_trip_id"] == 36
    assert shapes["added_distinct_entity_ids"] == 36

    entity_ids = set(shapes["added_entity_ids"])
    extras = [t for t in expected["trains"] if t["train_num"] in entity_ids]
    assert len(extras) == 36, f"every extra must be placed, got {len(extras)}"
    assert len({t["trip_id"] for t in extras}) == 36, (
        "and each must keep its own key; a collapse here is 35 trains vanishing"
    )
