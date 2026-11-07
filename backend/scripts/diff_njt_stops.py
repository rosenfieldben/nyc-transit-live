#!/usr/bin/env python3
"""Diff two NJ Transit stops.txt files: were the stops renamed, or renumbered.

THE QUESTION THIS ANSWERS (the live list, 2026-11-03 and 2026-11-06). Contract monitor
run #426 (2026-11-03) FAILed njt-static on the identity spot check, "stop 109 name is
'NORTH ELIZABETH', expected New York; stop 112 name is 'ORANGE', expected Newark", where
#425 (2026-11-02) had passed it, and #427 to #433 failed the same way. A RENAME leaves
every id pointing at the station it always meant, under a new name. A RENUMBERING points
every id-keyed consumer at a different station. The two need different fixes, so this
says which, as its last line: VERDICT: RENUMBERED, RENAMED or UNCHANGED.

WHY stop_code IS THE JOIN. The id is the thing under suspicion, so it cannot be the key,
and the name changed too. Measured between the committed fixture
(backend/tests/fixtures/njt_gtfs/stops.txt, committed 2026-09-01 in 0a5f251) and the
2026-11-06 publication: stop_code held on 164 of 164 rail stations, while 115 of those
164 stop_ids moved (Penn Station New York 109 -> 105, Newark Penn 112 -> 107) and all 164
names changed (133 by case only, 31 reworded, such as "Penn Station New York" -> "NEW
YORK PENN STATION"). 95 ids the fixture used now name a different station. Joined on the
id, the diff would call North Elizabeth a rename of Penn Station New York, which is the
exact mistake the monitor caught.

THE THREE VERDICTS, in precedence order. RENUMBERED if any shared code's stop_id moved,
whatever else changed, because an id-keyed consumer is then wrong and nothing else
matters as much. RENAMED if no id moved but some shared code's name changed. UNCHANGED
otherwise. Codes on one side only, and coordinate moves, are reported but never set the
verdict: the question is the identity of the stations both sides carry. (The 2026-11-06
publication adds 65 light-rail rows that no trip calls at; they show as only-new codes.)
So every verdict speaks only for the shared codes, and when the join itself finds little
(no code shared, or under half of the smaller side's), a NOTE line above the verdict says
so: a NEW whose stop_code changed meaning would otherwise read UNCHANGED, the most
reassuring answer, on nothing.

WHAT IT REPORTS above the verdict: rows per side, with rows of a blank stop_code and rows
of a code duplicated within one side counted and kept out of the join; shared, only-old
and only-new codes; ids moved, with the shift histogram (new id minus old id, most common
first; a move across the four-digit boundary is grouped rather than printed as a shift
in the tens of thousands); COLLISIONS, every NEW id that OLD used for a DIFFERENT code,
which is what an id-keyed consumer walks into ("109 was NY Penn Station New York, now NZ
NORTH ELIZABETH"); names changed, split by case only against reworded; and every shared
code whose coordinates moved more than 200 m (haversine, mean earth radius 6371008.8 m).
Collisions are taken over every row with an id, joined or not, since a consumer keyed on
the id sees every row; where an id repeats within one side its first row speaks for it.

NEVER SPENDS A MINT. Standard library only: no app import and no network. Pulling an NJ
Transit archive spends one of the account's daily mints, which production shares; this
only reads files already on disk. The input is untrusted data: decoded as utf-8-sig,
parsed as CSV, never evaluated, and every name is printed ASCII-escaped so a hostile
name cannot forge a line of the report.

RUN, from the repository root, on the committed fixture against a pulled archive:
    backend/.venv/bin/python -I backend/scripts/diff_njt_stops.py \\
        backend/tests/fixtures/njt_gtfs/stops.txt PATH/TO/gtfs.zip
Each argument is a stops.txt or a GTFS zip holding one. Exit 0 on any verdict; exit 2,
with the reason on stderr, on a usage error or an input that cannot be read.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import sys
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

EARTH_RADIUS_M = 6371008.8  # IUGG mean earth radius
FAR_MOVE_M = 200.0  # a coordinate move past this is listed
EXAMPLES = 5  # examples printed per finding
# THE FIXTURE'S HIGHEST RAIL ID IS 176; the 2026-11-06 publication moved 13 rail
# stations to five-digit ids, which the histogram groups rather than prints.
FOUR_DIGITS = 9999
MAX_STOPS_BYTES = 64 * 1024 * 1024  # 15,829 bytes on 2026-11-06; refuse a zip bomb
UNREADABLE = 2  # exit status for a usage error or an input that cannot be read

Row = Mapping[str, str | None]


class Unreadable(Exception):
    """An input is missing, is not a stops.txt or a zip holding one, or is not UTF-8."""


@dataclass(frozen=True)
class Stop:
    stop_id: str
    code: str
    name: str
    lat: float | None
    lon: float | None


@dataclass(frozen=True)
class Side:
    """One input, indexed. Only `by_code` takes part in the join."""

    rows: int
    blank_code_ids: list[str]
    duplicated_codes: dict[str, list[str]]  # code -> its stop_ids, in row order
    by_code: dict[str, Stop]
    by_id: dict[str, Stop]


@dataclass(frozen=True)
class Collision:
    stop_id: str
    old: Stop
    new: Stop


@dataclass
class StopsDiff:
    old: Side
    new: Side
    shared: list[str] = field(default_factory=list)
    only_old: list[str] = field(default_factory=list)
    only_new: list[str] = field(default_factory=list)
    moved: list[tuple[str, str, str]] = field(default_factory=list)  # code, old id, new id
    shifts: Counter[int] = field(default_factory=Counter)  # new id minus old id
    to_above_four_digits: int = 0
    from_above_four_digits: int = 0
    non_numeric_moves: int = 0
    collisions: list[Collision] = field(default_factory=list)
    case_only: list[str] = field(default_factory=list)  # codes
    reworded: list[str] = field(default_factory=list)  # codes
    far_moves: list[tuple[str, float]] = field(default_factory=list)  # code, metres
    no_coordinates: int = 0

    @property
    def renamed(self) -> int:
        return len(self.case_only) + len(self.reworded)

    @property
    def verdict(self) -> str:
        # RENUMBERED FIRST: a moved id is wrong for every id-keyed consumer whatever
        # the names did, so a rename must never mask it.
        if self.moved:
            return "RENUMBERED"
        if self.renamed:
            return "RENAMED"
        return "UNCHANGED"

    @property
    def smaller_side(self) -> int:
        """Joinable codes on the side with fewer: the most the join could share."""
        return min(len(self.old.by_code), len(self.new.by_code))

    @property
    def few_shared(self) -> bool:
        """No code shared, or under half of the smaller side's: the join key itself may
        have changed, so a verdict over the few shared codes says little."""
        return not self.shared or 2 * len(self.shared) < self.smaller_side

    def histogram(self) -> list[tuple[str, int]]:
        """(label, count), most common first; ties in shift order, then the groups."""
        entries = [(f"{shift:+d}", n, (0, shift)) for shift, n in self.shifts.items()]
        for rank, (label, n) in enumerate(
            [
                (f"to ids above {FOUR_DIGITS}", self.to_above_four_digits),
                (f"from ids above {FOUR_DIGITS}", self.from_above_four_digits),
                ("non-numeric ids", self.non_numeric_moves),
            ]
        ):
            if n:
                entries.append((label, n, (1, rank)))
        entries.sort(key=lambda e: (-e[1], e[2]))
        return [(label, n) for label, n, _ in entries]


def _cell(row: Row, key: str) -> str:
    return (row.get(key) or "").strip()


def _coordinate(text: str, limit: float) -> float | None:
    """A latitude (limit 90) or longitude (limit 180) in range, or None. The range
    test also refuses nan and the infinities, since every comparison with nan is
    False and an infinity is out of range; float() accepts all three, and an
    infinity reaching math.sin would raise."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if -limit <= value <= limit else None


def _index(rows: Iterable[Row]) -> Side:
    count = 0
    blank: list[str] = []
    ids_by_code: dict[str, list[str]] = {}
    stops_by_code: dict[str, Stop] = {}
    by_id: dict[str, Stop] = {}
    for row in rows:
        count += 1
        stop = Stop(
            stop_id=_cell(row, "stop_id"),
            code=_cell(row, "stop_code"),
            name=_cell(row, "stop_name"),
            lat=_coordinate(_cell(row, "stop_lat"), 90.0),
            lon=_coordinate(_cell(row, "stop_lon"), 180.0),
        )
        if stop.stop_id:
            by_id.setdefault(stop.stop_id, stop)
        if not stop.code:
            blank.append(stop.stop_id)
            continue
        ids_by_code.setdefault(stop.code, []).append(stop.stop_id)
        stops_by_code.setdefault(stop.code, stop)
    duplicated = {code: ids for code, ids in ids_by_code.items() if len(ids) > 1}
    by_code = {code: stop for code, stop in stops_by_code.items() if code not in duplicated}
    return Side(count, blank, duplicated, by_code, by_id)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres on a sphere of the mean earth radius."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def _distance(before: Stop, after: Stop) -> float | None:
    if before.lat is None or before.lon is None or after.lat is None or after.lon is None:
        return None
    return haversine_m(before.lat, before.lon, after.lat, after.lon)


def _number(stop_id: str) -> int | None:
    """The id as an integer, or None. Untrusted input can carry a digit string that
    int() refuses: a superscript two passes str.isdigit, and past 4,300 digits the
    interpreter's limit for int() applies. Either reads as a non-numeric id."""
    if not stop_id.isdigit():
        return None
    try:
        return int(stop_id)
    except ValueError:
        return None


def _id_order(stop_id: str) -> tuple[int, int, str]:
    number = _number(stop_id)
    return (0, number, "") if number is not None else (1, 0, stop_id)


def diff_stops(old_rows: Iterable[Row], new_rows: Iterable[Row]) -> StopsDiff:
    """Join OLD and NEW stops.txt rows on stop_code and say what moved.

    Rows are csv.DictReader-shaped mappings. Pure: no I/O, so the tests drive it
    directly and the CLI is a thin reader around it.
    """
    old, new = _index(old_rows), _index(new_rows)
    result = StopsDiff(old=old, new=new)
    result.shared = sorted(old.by_code.keys() & new.by_code.keys())
    result.only_old = sorted(old.by_code.keys() - new.by_code.keys())
    result.only_new = sorted(new.by_code.keys() - old.by_code.keys())

    for code in result.shared:
        before, after = old.by_code[code], new.by_code[code]
        if before.stop_id != after.stop_id:
            result.moved.append((code, before.stop_id, after.stop_id))
            old_n, new_n = _number(before.stop_id), _number(after.stop_id)
            if old_n is not None and new_n is not None:
                if old_n <= FOUR_DIGITS < new_n:
                    result.to_above_four_digits += 1
                elif new_n <= FOUR_DIGITS < old_n:
                    result.from_above_four_digits += 1
                else:
                    result.shifts[new_n - old_n] += 1
            else:
                result.non_numeric_moves += 1
        if before.name != after.name:
            if before.name.casefold() == after.name.casefold():
                result.case_only.append(code)
            else:
                result.reworded.append(code)
        metres = _distance(before, after)
        if metres is None:
            result.no_coordinates += 1
        elif metres > FAR_MOVE_M:
            result.far_moves.append((code, metres))

    for stop_id in sorted(old.by_id.keys() & new.by_id.keys(), key=_id_order):
        if old.by_id[stop_id].code != new.by_id[stop_id].code:
            result.collisions.append(Collision(stop_id, old.by_id[stop_id], new.by_id[stop_id]))
    result.far_moves.sort(key=lambda move: -move[1])
    return result


# ---------------------------------------------------------------------------
# Reading and reporting
# ---------------------------------------------------------------------------


def _escape(ch: str) -> str:
    if " " <= ch <= "~":
        return ch
    code = ord(ch)
    if code < 0x100:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"


def clean(text: str) -> str:
    """Printable ASCII only: anything else, a newline included, is escaped, so a
    hostile name cannot forge a line of the report or drive the terminal."""
    return "".join(_escape(ch) for ch in text)


def parse_stops(text: str) -> list[dict[str, str | None]]:
    """stops.txt text to rows. Fails with Unreadable if the join column is absent."""
    try:
        # NEWLINE="" LETS csv FIND THE LINE ENDS: CRLF, LF and CR-only all read, and a
        # quoted line break inside a name stays in the name. StringIO's default splits on
        # LF only, and csv then refused a CR-only stops.txt (review, 2026-11-06).
        reader = csv.DictReader(io.StringIO(text.removeprefix("\ufeff"), newline=""))
        fields = [name.strip() for name in reader.fieldnames or []]
        # Cells past the header land under the key None; they are no column of ours.
        rows = [{k.strip(): v for k, v in row.items() if k is not None} for row in reader]
    except csv.Error as exc:
        raise Unreadable(f"not a CSV stops.txt: {exc}") from exc
    for required in ("stop_id", "stop_code", "stop_name"):
        if required not in fields:
            raise Unreadable(f"no {required} column in the header")
    return rows


def _stops_member(zf: zipfile.ZipFile) -> zipfile.ZipInfo:
    members = [info for info in zf.infolist() if not info.is_dir()]
    for info in members:
        if info.filename == "stops.txt":
            return info
    nested = [info for info in members if info.filename.rsplit("/", 1)[-1] == "stops.txt"]
    if len(nested) != 1:
        raise Unreadable(f"the zip holds {len(nested)} stops.txt members, not one")
    return nested[0]


def read_stops(path: str) -> list[dict[str, str | None]]:
    """A stops.txt, or a GTFS zip holding one, to rows. Reads; executes nothing."""
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                with zf.open(_stops_member(zf)) as member:
                    body = member.read(MAX_STOPS_BYTES + 1)
        else:
            with open(path, "rb") as fh:
                body = fh.read(MAX_STOPS_BYTES + 1)
    except (
        OSError,  # missing, a directory, unreadable
        zipfile.BadZipFile,  # a damaged archive, or a member failing its CRC
        zipfile.LargeZipFile,
        RuntimeError,  # an encrypted member
        NotImplementedError,  # a compression method zipfile lacks
        EOFError,
        zlib.error,
    ) as exc:
        raise Unreadable(str(exc)) from exc
    if len(body) > MAX_STOPS_BYTES:
        raise Unreadable(f"stops.txt is over {MAX_STOPS_BYTES} bytes")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise Unreadable(f"not UTF-8: {exc}") from exc
    return parse_stops(text)


def _describe(stop: Stop) -> str:
    return f"{clean(stop.code) or '(blank code)'} {clean(stop.name)}"


def report(result: StopsDiff, old_label: str, new_label: str) -> list[str]:
    """The report, one string per line, the verdict last."""
    lines: list[str] = []
    for tag, label, side in (("OLD", old_label, result.old), ("NEW", new_label, result.new)):
        dup_rows = sum(len(ids) for ids in side.duplicated_codes.values())
        lines.append(
            f"{tag} {clean(label)}: {side.rows} rows, {len(side.by_code)} joinable"
            f" ({len(side.blank_code_ids)} with a blank stop_code,"
            f" {dup_rows} in {len(side.duplicated_codes)} duplicated codes)"
        )
        if side.blank_code_ids:
            shown = ", ".join(clean(i) or "(blank id)" for i in side.blank_code_ids[:EXAMPLES])
            lines.append(f"  blank stop_code, excluded: ids {shown}")
        for code, ids in sorted(side.duplicated_codes.items())[:EXAMPLES]:
            lines.append(f"  duplicated code {clean(code)}: ids {', '.join(map(clean, ids))}")
    lines.append(
        f"codes: {len(result.shared)} shared, {len(result.only_old)} only in OLD,"
        f" {len(result.only_new)} only in NEW"
    )
    for tag, codes, side in (
        ("only in OLD", result.only_old, result.old),
        ("only in NEW", result.only_new, result.new),
    ):
        for code in codes[:EXAMPLES]:
            stop = side.by_code[code]
            lines.append(f"  {tag}: {_describe(stop)} (id {clean(stop.stop_id)})")

    lines.append(f"ids moved: {len(result.moved)} of {len(result.shared)} shared codes")
    if result.moved:
        lines.append("  shift (new id minus old id), most common first:")
        lines.extend(f"    {label}: {n}" for label, n in result.histogram())
    lines.append(
        f"collisions (a NEW id that OLD used for a different code): {len(result.collisions)}"
    )
    for hit in result.collisions[:EXAMPLES]:
        lines.append(f"  {clean(hit.stop_id)} was {_describe(hit.old)}, now {_describe(hit.new)}")

    lines.append(
        f"names changed: {result.renamed} of {len(result.shared)} shared codes"
        f" ({len(result.case_only)} by case only, {len(result.reworded)} reworded)"
    )
    for code in result.reworded[:EXAMPLES]:
        before, after = result.old.by_code[code].name, result.new.by_code[code].name
        lines.append(f'  reworded {clean(code)}: "{clean(before)}" -> "{clean(after)}"')

    unreadable = ""
    if result.no_coordinates:
        unreadable = f", {result.no_coordinates} without readable coordinates"
    lines.append(f"coordinate moves over {FAR_MOVE_M:.0f} m: {len(result.far_moves)}{unreadable}")
    for code, metres in result.far_moves:
        lines.append(f"  {clean(code)} {clean(result.new.by_code[code].name)}: {metres:.0f} m")
    if result.few_shared:
        lines.append(
            f"NOTE: {len(result.shared)} of {result.smaller_side} joinable codes on the smaller"
            " side are shared; stop_code itself may have changed, so the verdict covers only"
            " the shared codes"
        )
    lines.append(f"VERDICT: {result.verdict}")
    return lines


def main(argv: list[str] | None = None) -> int:
    # __doc__ IS None UNDER python -OO, so the description falls back to a literal.
    description = (__doc__ or "Diff two NJ Transit stops.txt files.").split("\n")[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("old", help="the OLD stops.txt, or a GTFS zip holding one")
    parser.add_argument("new", help="the NEW stops.txt, or a GTFS zip holding one")
    args = parser.parse_args(argv)  # a usage error exits 2 here, on stderr
    rows: dict[str, list[dict[str, str | None]]] = {}
    for which in ("old", "new"):
        path = getattr(args, which)
        try:
            rows[which] = read_stops(path)
        except Unreadable as exc:
            print(f"{which.upper()} {clean(path)}: unreadable: {clean(str(exc))}", file=sys.stderr)
            return UNREADABLE
    result = diff_stops(rows["old"], rows["new"])
    print("\n".join(report(result, args.old, args.new)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
