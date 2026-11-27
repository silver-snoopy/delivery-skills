"""Read the team calendar (vacations.xlsx) month grids with the standard library only."""

import posixpath
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MIN_DAY_CELLS = 28
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


@dataclass
class Vacations:
    """Parsed calendar leave, squad labels, warnings, and every date column present."""

    days_off: dict[str, dict[date, float]] = field(default_factory=dict)
    squad: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    days: set[date] = field(default_factory=set)


def match_name(name: str, candidates: Iterable[str]) -> Optional[str]:
    """Match ignoring case, accents and word order; None when nothing matches."""

    def key(s):
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
        return tuple(sorted(re.findall(r"\w+", s)))

    wanted = key(name)
    if not wanted:
        return None
    for candidate in candidates:
        if key(candidate) == wanted:
            return candidate
    return None


def resolve_name(cal: str, names: Sequence[str], aliases: dict[str, str]) -> Optional[str]:
    """The roster name for a calendar name: its name_aliases entry, else match_name."""
    target = aliases.get(cal)
    return target if target in names else match_name(cal, names)


def _col_row(ref: str) -> tuple[int, int]:
    m = re.match(r"([A-Za-z]+)(\d+)$", ref)
    if m is None:
        raise ValueError(f"invalid cell reference: {ref!r}")
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + ord(ch) - 64
    return int(m.group(2)), col


def _text(el) -> str:
    return "".join(t.text or "" for t in el.iter(NS + "t"))


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return [_text(si) for si in root.iter(NS + "si")]


def _sheets(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets: dict[str, str] = {}
    for r in rels.iter(PKG_REL_NS + "Relationship"):
        rel_id, target = r.get("Id"), r.get("Target")
        if rel_id and target:
            targets[rel_id] = target
    out: list[tuple[str, str]] = []
    for s in wb.iter(NS + "sheet"):
        rel_id = s.get(REL_NS + "id")
        target = targets.get(rel_id) if rel_id else None
        name = s.get("name")
        if target and name is not None:
            path = target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target)
            out.append((name, path))
    return out


def _read_sheet(z, path, shared):
    """Return ({(row, col): text}, [(row1, col1, row2, col2)])."""
    root = ET.fromstring(z.read(path))
    cells = {}
    for c in root.iter(NS + "c"):
        kind = c.get("t")
        if kind == "inlineStr":
            is_el = c.find(NS + "is")
            text = _text(is_el) if is_el is not None else ""
        else:
            v = c.find(NS + "v")
            text = v.text if v is not None and v.text is not None else ""
            if kind == "s" and text:
                text = shared[int(text)]
        text = text.strip()
        ref = c.get("r")
        if text and ref:
            cells[_col_row(ref)] = text
    merges = []
    for m in root.iter(NS + "mergeCell"):
        ref = m.get("ref")
        if not ref:
            continue
        a, _, b = ref.partition(":")
        r1, c1 = _col_row(a)
        r2, c2 = _col_row(b or a)
        merges.append((r1, c1, r2, c2))
    return cells, merges


def _day_number(text: str) -> Optional[int]:
    try:
        f = float(text)
    except ValueError:
        return None
    return int(f) if f == int(f) and 1 <= f <= 31 else None


def _month_title(text: str) -> Optional[tuple[int, int]]:
    m = re.match(r"^(\d{4})-(\d{1,2})$", text)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{4})$", text)
    if m:
        word = m.group(1).lower()
        for i, name in enumerate(MONTHS):
            if word == name or word == name[:3]:
                return int(m.group(2)), i + 1
    return None


def _find_header(cells):
    rows = sorted({r for r, _ in cells})
    for r in rows:
        for (rr, c), text in sorted(cells.items()):
            if rr == r and text.lower() == "name":
                days = [
                    cc for (r2, cc), t in cells.items() if r2 == r and cc > c and _day_number(t) is not None
                ]
                if len(days) >= MIN_DAY_CELLS:
                    return r, c
    return None


def _runs(cells, header_row, name_col):
    cols = sorted(
        c for (r, c), t in cells.items() if r == header_row and c > name_col and _day_number(t) is not None
    )
    runs: list[list[tuple[int, int]]] = []
    for c in cols:
        d = _day_number(cells[(header_row, c)])
        if d is None:
            continue
        if runs and d == runs[-1][-1][1] + 1:
            runs[-1].append((c, d))
        else:
            runs.append([(c, d)])
    return runs


def _run_months(cells, merges, header_row, runs):
    months: list[Optional[tuple[int, int]]] = [None] * len(runs)
    for (r, c), text in sorted(cells.items()):
        title = _month_title(text) if r < header_row else None
        if not title:
            continue
        lo = hi = c
        for r1, c1, r2, c2 in merges:
            if r1 <= r <= r2 and c1 <= c <= c2:
                lo, hi = c1, c2
        for i, run in enumerate(runs):
            if months[i] is None and lo <= run[-1][0] and hi >= run[0][0]:
                months[i] = title
                break
    if all(m is None for m in months):
        return None
    for i in range(len(runs) - 2, -1, -1):
        nxt = months[i + 1]
        if months[i] is None and nxt is not None:
            y, m = nxt
            months[i] = (y, m - 1) if m > 1 else (y - 1, 12)
    for i in range(1, len(runs)):
        prev = months[i - 1]
        if months[i] is None and prev is not None:
            y, m = prev
            months[i] = (y, m + 1) if m < 12 else (y + 1, 1)
    return months


def read_vacations(path: str, half_day_codes: Sequence[str] = ()) -> Vacations:
    result = Vacations()
    halves = {c.strip() for c in half_day_codes}
    with zipfile.ZipFile(path) as z:
        shared = _shared_strings(z)
        for sheet_name, sheet_path in _sheets(z):
            cells, merges = _read_sheet(z, sheet_path, shared)
            header = _find_header(cells)
            if header is None:
                result.warnings.append(
                    f"Sheet '{sheet_name}': no header row with Name and day numbers; skipped"
                )
                continue
            header_row, name_col = header
            runs = _runs(cells, header_row, name_col)
            months = _run_months(cells, merges, header_row, runs)
            if months is None:
                result.warnings.append(f"Sheet '{sheet_name}': no month titles found; skipped")
                continue
            squad_col = next(
                (c for (r, c), t in cells.items() if r == header_row and t.lower() == "squad"), None
            )
            col_day: dict[int, date] = {}
            for run, ym in zip(runs, months):
                if ym is None:
                    continue
                year, month = ym
                for c, d in run:
                    try:
                        col_day[c] = date(year, month, d)
                    except ValueError:
                        result.warnings.append(
                            f"Sheet '{sheet_name}': day {d} does not exist in {year:04d}-{month:02d} (column {c})"
                        )
            result.days.update(col_day.values())
            last_row = max(r for r, _ in cells)
            for r in range(header_row + 1, last_row + 1):
                name = cells.get((r, name_col))
                if not name:
                    continue
                if squad_col is not None and (r, squad_col) in cells:
                    result.squad[name] = cells[(r, squad_col)]
                mine = result.days_off.setdefault(name, {})
                for c, day in col_day.items():
                    value = cells.get((r, c))
                    if value:
                        mine[day] = max(mine.get(day, 0.0), 0.5 if value in halves else 1.0)
    return result
