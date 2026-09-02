"""Tests for the static fixture generator's geometry refresh
(backend/scripts/gen_njt_fixture.py --shapes-only).

WHY ONLY THIS MODE IS TESTED HERE. The generator's other path downloads a
credentialed archive and rewrites six members whose trip selection every golden is
written against; it is exercised by a human running it and eyeballing the tables
it prints, which is the point of that ritual. The shapes-only refresh is
different: it exists to be run again later, against a publication nobody has seen,
beside committed trips nobody wants re-picked. Its whole value is what it does
when the publication and the commitment DISAGREE, and that decision is pure, so it
runs here in milliseconds with the download replaced by a zip built in memory.

THE REFUSAL IS THE BEHAVIOUR UNDER TEST, not the happy path. A refresh that wrote
the shapes it could find would leave the committed fixture internally
inconsistent: trips pointing at geometry the fixture does not carry, and every
route-line golden then measuring that gap rather than the map.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import zipfile
from pathlib import Path

_GEN_PATH = Path(__file__).resolve().parent.parent / "scripts" / "gen_njt_fixture.py"
_spec = importlib.util.spec_from_file_location("gen_njt_fixture", _GEN_PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

# Three committed trips referencing two shapes, which is the shape of the real
# thing in miniature: the fixture's 131 trips reference 29 shapes.
_TRIPS = (
    "route_id,service_id,trip_id,trip_headsign,direction_id,trip_short_name,shape_id\n"
    "1,SVC1,T1,New York,0,3800,s1\n"
    "1,SVC1,T2,Bay Head,1,3802,s2\n"
    "6,SVC1,T3,Port Jervis,1,1600,s1\n"
)


def _shapes_text(shape_ids: list[str]) -> str:
    lines = ["shape_id,shape_pt_sequence,shape_pt_lat,shape_pt_lon"]
    for shape_id in shape_ids:
        for seq in (1, 2, 3):
            lines.append(f"{shape_id},{seq},40.{700 + seq},-74.{100 + seq}")
    # An unreferenced shape, always present: the refresh must not commit it.
    for seq in (1, 2):
        lines.append(f"s99,{seq},41.{seq},-75.{seq}")
    return "\n".join(lines) + "\n"


def _archive(shape_ids: list[str] | None, **members: str) -> zipfile.ZipFile:
    """An in-memory publication. shape_ids=None omits shapes.txt entirely."""
    body = {"agency.txt": "agency_id\nNJT\n", **members}
    if shape_ids is not None:
        body["shapes.txt"] = _shapes_text(shape_ids)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in body.items():
            zf.writestr(name, text)
    return zipfile.ZipFile(io.BytesIO(buf.getvalue()))


def _committed(tmp_path: Path, trips: str = _TRIPS) -> Path:
    out = tmp_path / "njt_gtfs"
    out.mkdir()
    (out / "trips.txt").write_text(trips, encoding="utf-8")
    return out


def test_a_refresh_writes_only_the_shapes_the_committed_trips_reference(tmp_path, capsys):
    out = _committed(tmp_path)
    with _archive(["s1", "s2"]) as zf:
        assert gen.run_shapes_only(zf, out_dir=out) == 0
    written = sorted(path.name for path in out.iterdir())
    assert written == ["shapes.txt", "trips.txt"], "no other member may be touched"
    rows = list(csv.DictReader(io.StringIO((out / "shapes.txt").read_text(encoding="utf-8"))))
    assert {row["shape_id"] for row in rows} == {"s1", "s2"}
    assert "s99" not in {row["shape_id"] for row in rows}, "an unreferenced shape was committed"
    # Every row of every wanted shape, not just the first: a truncated shape draws
    # a line that stops short, which is the failure the whole arm is about.
    assert len(rows) == 6
    assert "2 distinct shape_ids" in capsys.readouterr().out


def test_a_refresh_refuses_and_writes_nothing_when_a_referenced_shape_is_absent(tmp_path, capsys):
    """THE REFUSAL. s2 is referenced by a committed trip and missing from this
    publication, so there is no honest partial answer: the fixture would carry a
    trip pointing at geometry it does not have."""
    out = _committed(tmp_path)
    with _archive(["s1"]) as zf:
        assert gen.run_shapes_only(zf, out_dir=out) == 1
    assert sorted(path.name for path in out.iterdir()) == ["trips.txt"], "nothing may be written"
    printed = capsys.readouterr().out
    assert "FEED DRIFT, fixture NOT written" in printed
    assert "'s2'" in printed, "the refusal must name the ids it could not find"
    assert "s1" not in printed.replace("'s2'", ""), "and must not name the ones it found"


def test_a_refresh_refuses_when_the_publication_carries_no_shapes_at_all(tmp_path, capsys):
    out = _committed(tmp_path)
    with _archive(None) as zf:
        assert gen.run_shapes_only(zf, out_dir=out) == 1
    assert sorted(path.name for path in out.iterdir()) == ["trips.txt"]
    assert "carries no shapes.txt" in capsys.readouterr().out


def test_a_refresh_refuses_when_there_is_no_committed_trips_file(tmp_path, capsys):
    """The mode reads the COMMITTED trips rather than re-picking them, so with no
    commitment there is nothing to refresh against and guessing would be worse."""
    out = tmp_path / "njt_gtfs"
    out.mkdir()
    with _archive(["s1"]) as zf:
        assert gen.run_shapes_only(zf, out_dir=out) == 1
    assert list(out.iterdir()) == []
    assert "does not exist" in capsys.readouterr().out


def test_a_refresh_refuses_when_the_committed_trips_reference_no_geometry(tmp_path, capsys):
    """A trips.txt with no shape_id column at all: writing an empty shapes.txt would
    be a fixture that says NJ Transit publishes no geometry, which is false."""
    out = _committed(
        tmp_path,
        trips="route_id,service_id,trip_id,trip_headsign,direction_id,trip_short_name\n"
        "1,SVC1,T1,New York,0,3800\n",
    )
    with _archive(["s1"]) as zf:
        assert gen.run_shapes_only(zf, out_dir=out) == 1
    assert sorted(path.name for path in out.iterdir()) == ["trips.txt"]
    assert "reference no shape_id at all" in capsys.readouterr().out


def test_the_cli_defaults_to_the_full_generator():
    """--shapes-only is opt-in: a bare run still rewrites the whole fixture, so
    nobody gets a geometry-only refresh by forgetting a flag."""
    assert gen._parse_args([]).shapes_only is False
    assert gen._parse_args(["--shapes-only"]).shapes_only is True
