"""Tests for the NJ Transit stops diff (backend/scripts/diff_njt_stops.py).

WHY THIS FILE EXISTS. Contract monitor run #426 (2026-11-03) found stop 109 named
NORTH ELIZABETH where the app expects Penn Station New York, and the live list asked
whether NJ Transit had renamed its stops or renumbered them. The diff answers that with
one of three verdicts, and each verdict is pinned here, together with the real event:
the four 2026-11-06 rows the monitor's failure turns on, against the committed
fixture's rows for the same four codes.

The precedence is the part most worth pinning. A renumbering arrives with renames
beside it (on 2026-11-06 every name changed case at the same time as 115 of 164 ids
moved), so a verdict that asked about names first would have called the event a rename,
the reading that leaves an id-keyed consumer pointing at the wrong stations.

Nothing here reaches NJ Transit: the tool is standard library only (read from its
source below, and the CLI runs under -I -S, where nothing else is importable) and every
input is a committed fixture, a row pasted below, or a temporary file. The script lives
under scripts/, so it is loaded from its file path, as the other script tests load theirs.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
_SCRIPT = BACKEND / "scripts" / "diff_njt_stops.py"
FIXTURE_STOPS = BACKEND / "tests" / "fixtures" / "njt_gtfs" / "stops.txt"

_spec = importlib.util.spec_from_file_location("diff_njt_stops", _SCRIPT)
diff = importlib.util.module_from_spec(_spec)
# Registered before it runs: dataclasses resolves its string annotations through
# sys.modules, and a module loaded from a path is not there unless put there.
sys.modules[_spec.name] = diff
_spec.loader.exec_module(diff)

HEADER = (
    "stop_id,stop_code,stop_name,stop_desc,stop_lat,stop_lon,zone_id,stop_url,"
    "location_type,parent_station,stop_timezone,wheelchair_boarding"
)

# REAL ROWS, verbatim from the 2026-11-06 publication's stops.txt (pulled 2026-11-06,
# not committed): Penn Station New York and Newark Penn under their new ids, and the two
# stations that now hold the ids the app keyed them on.
NEW_2026_11_06 = f"""{HEADER}
105,NY,"NEW YORK PENN STATION","NYP",40.750046,-73.992358,329,,,,,
107,NP,"NEWARK PENN STATION","NWK",40.734221,-74.164554,336,,,,,
109,NZ,"NORTH ELIZABETH","NELZ",40.680265,-74.206165,1357,,,,,
112,OG,"ORANGE","MEORNGE",40.771883,-74.233103,1101,,,,,
"""

THE_FOUR = ("NY", "NP", "NZ", "OG")


def _rows(*lines: str) -> list[dict[str, str | None]]:
    """stops.txt rows from (id,code,name,lat,lon) lines, the rest of the columns blank."""
    body = [HEADER]
    for line in lines:
        stop_id, code, name, lat, lon = line.split(",")
        body.append(f"{stop_id},{code},{name},,{lat},{lon},,,,,,")
    return diff.parse_stops("\n".join(body) + "\n")


def _fixture_rows(codes) -> list[dict[str, str | None]]:
    rows = [row for row in diff.read_stops(str(FIXTURE_STOPS)) if row["stop_code"] in codes]
    assert sorted(row["stop_code"] for row in rows) == sorted(codes), (
        "the committed fixture no longer carries one of the four codes"
    )
    return rows


# A small network under its fixture ids and names, for the synthetic verdicts.
OLD = (
    "109,NY,Penn Station New York,40.750048,-73.992358",
    "112,NP,Newark Penn Station,40.734221,-74.164557",
    "114,NZ,North Elizabeth,40.680265,-74.206165",
    "117,OG,Orange,40.771883,-74.233103",
)


# ---------------------------------------------------------------------------
# The three verdicts
# ---------------------------------------------------------------------------


def test_the_committed_fixture_against_itself_is_unchanged():
    rows = diff.read_stops(str(FIXTURE_STOPS))
    result = diff.diff_stops(rows, rows)

    assert result.verdict == "UNCHANGED"
    assert len(result.shared) == 164, "the fixture carries 164 rail stations"
    assert (result.only_old, result.only_new) == ([], [])
    assert (result.moved, result.collisions, result.renamed) == ([], [], 0)
    assert result.far_moves == []
    lines = diff.report(result, "a", "b")
    assert not any(line.startswith("NOTE:") for line in lines), "164 of 164 codes joined"
    assert lines[-1] == "VERDICT: UNCHANGED"


def test_same_ids_with_ny_reworded_and_np_recased_is_renamed_with_the_split_counted():
    """Every id holds its station, so nothing keyed on the id is wrong: RENAMED, and the
    two kinds of rename are counted apart, because a case-only change is the cheap one
    to accept and a rewording is the one a human reads."""
    new = list(OLD)
    new[0] = "109,NY,NEW YORK PENN STATION,40.750048,-73.992358"
    new[1] = "112,NP,NEWARK PENN STATION,40.734221,-74.164557"
    result = diff.diff_stops(_rows(*OLD), _rows(*new))

    assert result.verdict == "RENAMED"
    assert result.moved == [] and result.collisions == []
    assert result.case_only == ["NP"]
    assert result.reworded == ["NY"]
    lines = diff.report(result, "old", "new")
    assert "names changed: 2 of 4 shared codes (1 by case only, 1 reworded)" in lines
    assert '  reworded NY: "Penn Station New York" -> "NEW YORK PENN STATION"' in lines
    assert lines[-1] == "VERDICT: RENAMED"


def test_shifted_ids_are_renumbered_even_with_a_rename_beside_them():
    """Four ids slide down by one, a fifth jumps past 9999, and one name changes too.
    RENUMBERED wins whatever else changed; the histogram counts the slide and groups the
    jump; and each slid id now naming a different station is a collision."""
    old = _rows(
        "10,AA,Alpha,40.0,-74.0",
        "11,BB,Bravo,40.1,-74.0",
        "12,CC,Charlie,40.2,-74.0",
        "13,DD,Delta,40.3,-74.0",
        "14,EE,Echo,40.4,-74.0",
    )
    new = _rows(
        "9,AA,Alpha,40.0,-74.0",
        "10,BB,Bravo,40.1,-74.0",
        "11,CC,Charlie,40.2,-74.0",
        "12,DD,Delta Junction,40.3,-74.0",
        "30000,EE,Echo,40.4,-74.0",
    )
    result = diff.diff_stops(old, new)

    assert result.verdict == "RENUMBERED"
    assert len(result.moved) == 5
    assert result.shifts == Counter({-1: 4})
    assert result.to_above_four_digits == 1
    assert result.histogram() == [("-1", 4), ("to ids above 9999", 1)]
    assert [(c.stop_id, c.old.code, c.new.code) for c in result.collisions] == [
        ("10", "AA", "BB"),
        ("11", "BB", "CC"),
        ("12", "CC", "DD"),
    ]
    assert result.reworded == ["DD"]
    lines = diff.report(result, "old", "new")
    assert "ids moved: 5 of 5 shared codes" in lines
    assert "  10 was AA Alpha, now BB Bravo" in lines
    assert lines[-1] == "VERDICT: RENUMBERED"


def test_ids_moved_past_every_old_id_are_renumbered_with_no_collision():
    """A renumbering need not collide. Every id here moves up 1000 (109 -> 1109), past
    every id OLD used, so no NEW id names a station OLD gave it to another. An id-keyed
    consumer is still wrong for all four, so RENUMBERED rests on the moves, never on the
    collisions: every other RENUMBERED test here has a collision beside its moves."""
    new = [f"1{line}" for line in OLD]
    result = diff.diff_stops(_rows(*OLD), _rows(*new))

    assert result.collisions == []
    assert result.shifts == Counter({1000: 4})
    lines = diff.report(result, "old", "new")
    assert "collisions (a NEW id that OLD used for a different code): 0" in lines
    assert lines[-1] == "VERDICT: RENUMBERED"


@pytest.mark.parametrize(("recoded", "noted"), [(4, True), (3, True), (2, False)])
def test_few_shared_codes_put_a_note_above_the_verdict(recoded, noted):
    """THE JOIN CAN FAIL ON ITS OWN KEY. A NEW whose stop_code changed meaning shares no
    code with OLD, and every verdict speaks only for shared codes, so the bare verdict
    would read UNCHANGED, the most reassuring answer, on nothing (review, 2026-11-06).
    The three verdict words stay; a NOTE above the verdict says the join found little,
    when no code or under half of the smaller side's is shared: 0 or 1 of 4, not 2 of 4."""
    new = [
        line.replace(f",{code},", f",{code.lower()},") if i < recoded else line
        for i, (line, code) in enumerate(zip(OLD, THE_FOUR, strict=True))
    ]
    result = diff.diff_stops(_rows(*OLD), _rows(*new))
    lines = diff.report(result, "old", "new")

    assert len(result.shared) == 4 - recoded
    assert result.verdict == "UNCHANGED" and lines[-1] == "VERDICT: UNCHANGED"
    note = (
        f"NOTE: {4 - recoded} of 4 joinable codes on the smaller side are shared; stop_code"
        " itself may have changed, so the verdict covers only the shared codes"
    )
    assert (lines[-2] == note) is noted
    assert any(line.startswith("NOTE:") for line in lines) is noted


# ---------------------------------------------------------------------------
# The real event
# ---------------------------------------------------------------------------


def test_the_2026_11_06_publication_moved_penn_station_new_york_and_newark_penn():
    """THE EVENT the monitor caught from #426 on, on its four real rows. Penn Station
    New York went 109 -> 105 and Newark Penn 112 -> 107, and North Elizabeth and
    Orange took 109 and 112, which is why the identity check read NORTH ELIZABETH and
    ORANGE. Codes held, so the join sees one station per code; ids moved, so the
    verdict is RENUMBERED, not the rename the upper-cased names alone would suggest."""
    result = diff.diff_stops(_fixture_rows(THE_FOUR), diff.parse_stops(NEW_2026_11_06))

    assert result.verdict == "RENUMBERED"
    assert result.shared == sorted(THE_FOUR)
    assert sorted(result.moved) == [
        ("NP", "112", "107"),
        ("NY", "109", "105"),
        ("NZ", "114", "109"),
        ("OG", "117", "112"),
    ]
    assert result.shifts == Counter({-5: 3, -4: 1})
    assert [(c.stop_id, c.old.code, c.new.code) for c in result.collisions] == [
        ("109", "NY", "NZ"),
        ("112", "NP", "OG"),
    ]
    assert (result.case_only, result.reworded) == (["NP", "NZ", "OG"], ["NY"])
    assert result.far_moves == [], "Penn Station New York and Newark Penn moved under 1 m"

    lines = diff.report(result, "fixture", "2026-11-06")
    assert "    -5: 3" in lines and "    -4: 1" in lines
    assert lines.index("    -5: 3") < lines.index("    -4: 1"), "most common first"
    assert "  109 was NY Penn Station New York, now NZ NORTH ELIZABETH" in lines
    assert "  112 was NP Newark Penn Station, now OG ORANGE" in lines
    assert lines[-1] == "VERDICT: RENUMBERED"


def test_haversine_puts_the_penn_station_light_rail_rows_103_m_from_newark_penn():
    """The 2026-11-06 publication's PENN STATION LIGHT RAIL rows (26326 and 42545, both
    at 40.73481,-74.163603) against Newark Penn's new row: 103.5 m, under the 200 m bar
    a coordinate move must clear to be listed. Pins the distance on real numbers."""
    metres = diff.haversine_m(40.734221, -74.164554, 40.73481, -74.163603)
    assert metres == pytest.approx(103.49, abs=0.01)


# ---------------------------------------------------------------------------
# What the report carries beside the verdict
# ---------------------------------------------------------------------------


def test_blank_and_duplicated_codes_are_reported_and_kept_out_of_the_join():
    """A blank code and a code used twice cannot be joined, so both are counted and kept
    out. Their ids still collide, since an id-keyed consumer reads every row: id 2, the
    blank-coded Nameless in OLD, is one of NEW's two BB rows."""
    old = _rows("1,AA,Alpha,40.0,-74.0", "2,,Nameless,40.0,-74.0", "3,BB,Bravo,40.1,-74.0")
    new = _rows("1,AA,Alpha,40.0,-74.0", "3,BB,Bravo,40.1,-74.0", "2,BB,Bravo Annex,40.1,-74.0")
    result = diff.diff_stops(old, new)

    assert result.old.blank_code_ids == ["2"]
    assert result.new.duplicated_codes == {"BB": ["3", "2"]}
    assert result.shared == ["AA"] and result.only_old == ["BB"]
    assert [(c.stop_id, c.old.code, c.new.code) for c in result.collisions] == [("2", "", "BB")]
    assert result.verdict == "UNCHANGED", "the one code both sides can join on held"
    lines = diff.report(result, "old", "new")
    assert "  blank stop_code, excluded: ids 2" in lines
    assert "  duplicated code BB: ids 3, 2" in lines
    assert "  only in OLD: BB Bravo (id 3)" in lines
    assert "  2 was (blank code) Nameless, now BB Bravo Annex" in lines


def test_a_padded_stop_code_joins_its_bare_twin():
    """stop_code is the join, so it is stripped: " NY " in NEW is NY in OLD, not an
    only-new code beside an only-old one."""
    new = [line.replace(",NY,", ", NY ,") for line in OLD]
    result = diff.diff_stops(_rows(*OLD), _rows(*new))

    assert result.shared == sorted(THE_FOUR)
    assert result.verdict == "UNCHANGED"


@pytest.mark.parametrize("variant", ["byte-order-mark", "cr-only", "lf-only"])
def test_a_byte_order_mark_or_other_line_ends_read_like_the_fixture(tmp_path, variant):
    """The committed fixture is CRLF with no byte-order mark; a publication may differ in
    both. Read as utf-8-sig with csv finding the line ends, each variant joins all 164
    codes and changes nothing."""
    raw = FIXTURE_STOPS.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    body = {
        "byte-order-mark": b"\xef\xbb\xbf" + raw,
        "cr-only": lf.replace(b"\n", b"\r"),
        "lf-only": lf,
    }[variant]
    path = tmp_path / "stops.txt"
    path.write_bytes(body)
    result = diff.diff_stops(diff.read_stops(str(FIXTURE_STOPS)), diff.read_stops(str(path)))

    assert len(result.shared) == 164
    assert result.verdict == "UNCHANGED"


def test_a_zip_holding_its_stops_txt_in_one_folder_reads_it(tmp_path):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("gtfs_njt/agency.txt", "agency_id\n")
        zf.writestr("gtfs_njt/stops.txt", NEW_2026_11_06)

    assert [row["stop_code"] for row in diff.read_stops(str(path))] == list(THE_FOUR)


def test_a_stops_txt_over_the_size_cap_is_unreadable(monkeypatch):
    """The cap (64 MiB, against 15,829 bytes on 2026-11-06) keeps a zip bomb from
    inflating into memory. Lowered to one byte under the fixture, it refuses the fixture."""
    monkeypatch.setattr(diff, "MAX_STOPS_BYTES", FIXTURE_STOPS.stat().st_size - 1)
    with pytest.raises(diff.Unreadable, match="over"):
        diff.read_stops(str(FIXTURE_STOPS))


def test_a_coordinate_move_over_200_m_is_listed_and_does_not_set_the_verdict():
    new = list(OLD)
    new[2] = "114,NZ,North Elizabeth,40.683265,-74.206165"  # 0.003 deg north, 333.6 m
    result = diff.diff_stops(_rows(*OLD), _rows(*new))

    assert [code for code, _ in result.far_moves] == ["NZ"]
    assert result.far_moves[0][1] == pytest.approx(333.6, abs=0.1)
    assert "  NZ North Elizabeth: 334 m" in diff.report(result, "old", "new")
    assert result.verdict == "UNCHANGED"


def test_a_nonfinite_or_out_of_range_coordinate_is_unreadable_not_a_crash():
    """float() accepts nan and inf, and an infinity reaching math.sin raises, so a
    hostile stops.txt could crash the diff. A coordinate outside [-90, 90] or
    [-180, 180], nan and the infinities included, is counted as unreadable."""
    new = list(OLD)
    new[1] = "112,NP,Newark Penn Station,inf,-74.164557"
    new[2] = "114,NZ,North Elizabeth,nan,-74.206165"
    new[3] = "117,OG,Orange,40.771883,-274.233103"
    result = diff.diff_stops(_rows(*OLD), _rows(*new))

    assert result.no_coordinates == 3 and result.far_moves == []
    lines = diff.report(result, "old", "new")
    assert "coordinate moves over 200 m: 0, 3 without readable coordinates" in lines
    assert lines[-1] == "VERDICT: UNCHANGED"


def test_a_hostile_name_cannot_forge_the_verdict_line():
    """Input is untrusted: a quoted name may hold a newline. Printed ASCII-escaped, it
    stays on its own line, and the only line starting VERDICT: is the last."""
    text = f'{HEADER}\n109,NY,"Penn\nVERDICT: UNCHANGED \u2192",,40.75,-73.99,,,,,,\n'
    result = diff.diff_stops(_rows(OLD[0]), diff.parse_stops(text))
    lines = "\n".join(diff.report(result, "old", "new")).split("\n")

    assert [line for line in lines if line.startswith("VERDICT:")] == ["VERDICT: RENAMED"]
    assert lines[-1] == "VERDICT: RENAMED"
    assert all(line.isascii() for line in lines)


def test_ids_int_refuses_are_non_numeric_moves_not_a_crash():
    """Two digit strings int() refuses, either of which untrusted input can carry: a
    superscript two, which str.isdigit accepts, and 5,000 digits, past the interpreter's
    limit for int(). Each is grouped as a non-numeric move and the report still prints."""
    long_id = "9" * 5000
    old = _rows("\u00b2,AA,Alpha,40.0,-74.0", f"{long_id},BB,Bravo,40.1,-74.0")
    new = _rows("3,AA,Alpha,40.0,-74.0", "\u00b2,BB,Bravo,40.1,-74.0")
    result = diff.diff_stops(old, new)

    assert result.verdict == "RENUMBERED"
    assert result.non_numeric_moves == 2 and not result.shifts
    assert [c.stop_id for c in result.collisions] == ["\u00b2"]
    assert "  \\xb2 was AA Alpha, now BB Bravo" in diff.report(result, "old", "new")


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def _run(*args: str, flags: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    # -I: neither the working directory nor the script's own directory is on sys.path,
    # and PYTHON* variables are ignored. -I ALONE KEEPS THE VENV'S SITE-PACKAGES
    # importable (httpx imported under it, review 2026-11-06), so -S drops them too: the
    # run sees the standard library and nothing else, the tool's no-mint promise.
    return subprocess.run(
        [sys.executable, "-I", "-S", *flags, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_tool_imports_only_the_standard_library():
    """THE NO-MINT PROMISE, read from the source. Pulling an NJ Transit archive spends one
    of the account's daily mints, and every route to a pull runs through an app or
    third-party import. So each import must name a standard library module, none may be
    relative, and none may be made by name at run time (importlib, __import__), which a
    scan of import statements could not see."""
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"a relative import of {node.module}"
            modules.add((node.module or "").split(".")[0])
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)
    assert "importlib" not in modules and "__import__" not in names


def test_the_cli_reads_a_stops_txt_and_a_zip_and_prints_renumbered_last_with_exit_0(
    tmp_path,
):
    old = tmp_path / "stops.txt"
    old.write_text(
        HEADER
        + "\n"
        + "".join(
            line + "\n"
            for line in FIXTURE_STOPS.read_text(encoding="utf-8-sig").splitlines()[1:]
            if line.split(",")[1] in THE_FOUR
        ),
        encoding="utf-8",
    )
    new = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(new, "w") as zf:
        zf.writestr("stops.txt", NEW_2026_11_06)

    result = _run(str(old), str(new))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert "ids moved: 4 of 4 shared codes" in lines
    assert lines[-1] == "VERDICT: RENUMBERED"


def test_the_cli_exits_2_on_a_missing_file(tmp_path):
    result = _run(str(FIXTURE_STOPS), str(tmp_path / "absent.zip"))

    assert result.returncode == 2
    assert result.stdout == ""
    assert "NEW" in result.stderr and "unreadable" in result.stderr


def _crc_bad_zip(path: Path) -> None:
    """A zip whose central directory is sound, so it is a zip, but whose one stored
    member fails its CRC: one byte of NORTH ELIZABETH flipped after the CRC was taken."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("stops.txt", NEW_2026_11_06)
    body = path.read_bytes()
    assert body.count(b"NORTH ELIZABETH") == 1
    path.write_bytes(body.replace(b"NORTH ELIZABETH", b"NORTH ELIZABETX"))


def _two_nested_zip(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a/stops.txt", NEW_2026_11_06)
        zf.writestr("b/stops.txt", NEW_2026_11_06)


def _no_code_column(path: Path) -> None:
    path.write_text("stop_id,stop_name,stop_lat,stop_lon\n105,NY,40.75,-73.99\n", encoding="utf-8")


@pytest.mark.parametrize("make", [_crc_bad_zip, _two_nested_zip, _no_code_column])
def test_an_input_the_tool_cannot_read_exits_2_with_nothing_on_stdout(tmp_path, make):
    """A damaged zip, a zip that does not say which stops.txt it means, and a stops.txt
    without the join column: each is a reason on stderr and exit 2, never a traceback,
    and never a report whose verdict was reached on rows it could not read."""
    path = tmp_path / "new"
    make(path)
    result = _run(str(FIXTURE_STOPS), str(path))

    assert result.returncode == 2, result.stderr
    assert result.stdout == ""
    assert result.stderr.startswith("NEW ") and "unreadable" in result.stderr


def test_the_cli_runs_under_python_oo_which_drops_the_docstring():
    """python -OO sets __doc__ to None, and argparse's description is read from it."""
    result = _run(str(FIXTURE_STOPS), str(FIXTURE_STOPS), flags=("-OO",))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "VERDICT: UNCHANGED"


def test_a_usage_error_exits_2():
    with pytest.raises(SystemExit) as exc:
        diff.main([str(FIXTURE_STOPS)])
    assert exc.value.code == 2
