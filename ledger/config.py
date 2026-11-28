"""Configuration files: config.json, team.csv and the per-sprint notes.md."""

import copy
import csv
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any

from ledger.replay import local_tz
from ledger.theme import DEFAULT_THEME_REPORTS, KNOWN_THEME_REPORTS

DEFAULT_NOT_DELIVERED = [
    "Removed",
    "Won't Fix",
    "Won't Do",
    "Cut",
    "Duplicate",
    "Obsolete",
    "Deferred",
    "Abandoned",
]
DEFAULT_MANAGER_METRICS = ("planned", "delivered", "throughput", "build_time", "first_pass")
KNOWN_METRICS = (*DEFAULT_MANAGER_METRICS, "po_satisfaction")
_TEAM_KEYS = {"count", "metrics", "show_flow"}
CONFIG_FILE = "config.json"
TEAM_COLUMNS = ["name", "email", "team", "joined", "left", "allocation"]


@dataclass
class Person:
    name: str
    emails: list[str]
    team: str
    joined: date | None
    left: date | None
    allocation: float = 1.0


def _clean_name(raw: str) -> str:
    name = re.sub(r"\s*\([^()]*\)$", "", raw.strip().strip('"').strip())
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first.strip()} {last.strip()}"
    return " ".join(name.split())


def parse_staff(text: str, warnings: list[str] | None = None) -> list[tuple[str, str]]:
    """(name, email) pairs from `Name <email>;` entries or bare emails; entries without an
    email address are skipped with a line in `warnings`."""
    pairs = []
    for chunk in re.split(r"[;\n]", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.match(r"^(.*?)<([^>]+)>\s*$", chunk)
        if m:
            email = m.group(2).strip().lower()
            found = [(_clean_name(m.group(1)) or email.split("@")[0], email)]
        else:
            found = [(t.split("@")[0], t.lower()) for t in re.split(r"[,\s]+", chunk) if t]
        if not all("@" in email for _, email in found) and warnings is not None:
            warnings.append(f"staff.txt entry '{chunk}' has no email address; skipped")
        pairs += [(name, email) for name, email in found if "@" in email]
    return pairs


def merge_staff(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for name, email in pairs:
        emails = merged.setdefault(name, [])
        if email not in emails:
            emails.append(email)
    return merged


def _date(text: str | None) -> date | None:
    cleaned = (text or "").strip()
    return date.fromisoformat(cleaned) if cleaned else None


def read_team(path: str) -> list[Person]:
    people: dict[str, Person] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row["name"].strip()
            email = row["email"].strip().lower()
            person = people.get(name)
            if person is None:
                alloc = (row.get("allocation") or "").strip()
                person = Person(
                    name,
                    [],
                    row["team"].strip().upper(),
                    _date(row.get("joined")),
                    _date(row.get("left")),
                    float(alloc) if alloc else 1.0,
                )
                people[name] = person
            if email and email not in person.emails:
                person.emails.append(email)
    return list(people.values())


def write_team(path: str, people: list[Person]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(TEAM_COLUMNS)
        for p in people:
            for email in p.emails or [""]:
                w.writerow(
                    [
                        p.name,
                        email,
                        p.team,
                        p.joined.isoformat() if p.joined else "",
                        p.left.isoformat() if p.left else "",
                        f"{p.allocation:g}",
                    ]
                )


def email_index(people: list[Person]) -> dict[str, Person]:
    return {e.lower(): p for p in people for e in p.emails}


@dataclass
class Config:
    organization: str = ""
    project: str = ""
    query: str = ""
    timezone: str = "UTC"
    teams: dict[str, dict[str, Any]] = field(default_factory=dict)
    manager_metrics: list[str] = field(default_factory=lambda: list(DEFAULT_MANAGER_METRICS))
    show_flow: bool = True
    onboarding_sprints: int = 2
    half_day_codes: list[str] = field(default_factory=list)
    waiting_states: list[str] = field(default_factory=list)
    hold_states: list[str] = field(default_factory=list)
    test_states: list[str] = field(default_factory=list)
    not_delivered_reasons: list[str] = field(default_factory=lambda: list(DEFAULT_NOT_DELIVERED))
    staff: str = ""
    vacations: str = ""
    name_aliases: dict[str, str] = field(default_factory=dict)
    squad_teams: dict[str, str] = field(default_factory=dict)
    history_days: int = 200
    theme: str = ""
    theme_reports: list[str] = field(default_factory=lambda: list(DEFAULT_THEME_REPORTS))


def _check_list(value: Any, known: tuple[str, ...], where: str, noun: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"config.json: {where} must be a list of {noun} names")
    seen = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"config.json: {where} entries must be strings, got {item!r}")
        if item not in known:
            raise ValueError(f"config.json: {where} has unknown {noun} '{item}'")
        if item in seen:
            raise ValueError(f"config.json: {where} has duplicate {noun} '{item}'")
        seen.add(item)


def load_config(directory: str, warn: Callable[[str], None] = lambda line: None) -> Config:
    """Read config.json; unknown keys are passed to `warn`, a bad count or time zone raises."""
    with open(os.path.join(directory, CONFIG_FILE), encoding="utf-8-sig") as f:
        data = json.load(f)
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(data) - known)
    if unknown:
        warn(f"config.json keys not recognised and ignored: {', '.join(unknown)}")
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    _check_list(cfg.manager_metrics, KNOWN_METRICS, "manager_metrics", "metric")
    if not isinstance(cfg.theme, str):
        raise ValueError("config.json: theme must be a string")
    _check_list(cfg.theme_reports, KNOWN_THEME_REPORTS, "theme_reports", "report")
    if not isinstance(cfg.show_flow, bool):
        raise ValueError("config.json: show_flow must be a boolean")
    if not isinstance(cfg.teams, dict):
        raise ValueError("config.json: teams must be an object")
    seen_teams: dict[str, str] = {}
    for team, entry in cfg.teams.items():
        low = team.lower()
        if low in seen_teams:
            raise ValueError(f"config.json: duplicate team '{team}' (collides with '{seen_teams[low]}')")
        seen_teams[low] = team
        if not isinstance(entry, dict):
            raise ValueError(f"config.json: team {team} must be an object")
        extra = sorted(set(entry) - _TEAM_KEYS)
        if extra:
            warn(f"config.json: team {team} has unrecognised keys ignored: {', '.join(extra)}")
        c = entry.get("count")
        is_list = isinstance(c, list) and c and all(isinstance(t, str) and t.strip() for t in c)
        if not is_list and c not in (None, "", "stories", "tasks"):
            raise ValueError(
                f'config.json: team {team} has count \'{c}\'; use "stories", "tasks" or a '
                "list of work item types"
            )
        if "metrics" in entry:
            _check_list(entry["metrics"], KNOWN_METRICS, f"team {team} metrics", "metric")
        if "show_flow" in entry and not isinstance(entry["show_flow"], bool):
            raise ValueError(f"config.json: team {team} show_flow must be a boolean")
    local_tz(cfg.timezone)
    return cfg


def save_config(directory: str, cfg: Config) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, CONFIG_FILE), "w", encoding="utf-8") as f:
        json.dump(cfg.__dict__, f, indent=2, ensure_ascii=False)
        f.write("\n")


def team_key(mapping: dict, team: str) -> str:
    return next((k for k in mapping if k.lower() == team.lower()), team)


def team_value(mapping: dict, team: str, default=None):
    return next((v for k, v in mapping.items() if k.lower() == team.lower()), default)


def count_of(cfg: Config, team: str):
    """The team's counting level ("stories", "tasks") or list of work item types from
    config.json; "stories" when the team has none."""
    return team_value(cfg.teams, team, {}).get("count") or "stories"


def metrics_of(cfg: Config, team: str) -> list[str]:
    entry = team_value(cfg.teams, team, {})
    return list(entry.get("metrics", cfg.manager_metrics))


def show_flow_of(cfg: Config, team: str) -> bool:
    entry = team_value(cfg.teams, team, {})
    return entry.get("show_flow", cfg.show_flow)


@dataclass
class Notes:
    items: dict[int, str] = field(default_factory=dict)
    reasons: dict[str, dict[int, str]] = field(default_factory=dict)
    summary: dict[str, str] = field(default_factory=dict)
    note: dict[str, str] = field(default_factory=dict)
    exec_note: dict[str, str] = field(default_factory=dict)
    po_satisfaction: dict[str, tuple[int, str]] = field(default_factory=dict)
    po_history: dict[str, dict[str, int | None]] = field(default_factory=dict)
    status: dict[str, tuple[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    teams: dict = field(default_factory=dict)
    markers: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    raw: list[str] = field(default_factory=list)
    reported: set | None = None
    copied: set[str] = field(default_factory=set)
    unscoped: set[str] = field(default_factory=set)


_SECTIONS = ("Summary", "Exec note", "PO satisfaction", "Note", "Status", "Actions", "Resolved")
_COLUMNS = ["Person", "Work item", "Problem", "Answer", "Answered"]
_GUIDE = "<!-- guide: {} -->"
_TOP_GUIDE = (
    "Edit this file, then run `bin/ledger report --offline` to update both reports from the cache.",
    "Write only in the text sections and the Answer column; the tool rewrites the rest, these lines too.",
    "Summary, Exec note, PO satisfaction, Status and carry-over answers reach the manager report: no engineer names.",
)
_SECTION_GUIDE = {
    "Summary": 'Manager report, Delivery highlights. 2-4 bullets, no names. Example: "- Delivered upload '
    'validation."',
    "Exec note": 'Manager report, below the card. Optional, no names. Example: "Release moved one week: '
    'waiting on the customer test environment."',
    "PO satisfaction": "Manager report. Optional, no names. 1-5 scale (5 Very satisfied, 4 Satisfied, "
    '3 Mixed, 2 Dissatisfied, 1 Very dissatisfied). Example: "4/5 - Smooth release, '
    'minor UAT delay"',
    "Note": 'DM report only; names are fine. Example: "Agreed pairing on the payment flow next sprint."',
    "Status": 'Leave empty for the computed status. To override: "AMBER - reason" (GREEN, AMBER or RED).',
    "Actions": "Open red and amber problems. Write only in Answer; the tool fills Answered. "
    'Carry-over answers reach the manager report as Reason (no engineer names). Example: "Waiting on '
    'external API".',
    "Resolved": "Answered problems that went away. A row moves back to Actions if its problem returns.",
}
_STATUS = re.compile(r"^(GREEN|AMBER|RED)\s*(?:-\s*(.*))?$", re.I)
_PO_SAT = re.compile(r"^([1-5])(?:\s*/\s*5)?(?:\s*[-–—]\s*(.*))?$")
_KIND_LABELS = {
    "old": "Chase",
    "waiting": "Waiting",
    "stale": "No movement",
    "none": "No work",
    "nothing": "Nothing delivered",
    "wip": "Too much WIP",
    "never_active": "Never active",
    "reopened": "Reopened",
    "shared": "Shared",
    "carry": "Carry-over",
    "held": "On Hold",
    "not_started": "Not started",
}
_LABEL_KINDS = {**{label: kind for kind, label in _KIND_LABELS.items()}, "PO agreement": "carry"}
SEVERITY = {"red": 0, "amber": 1, "grey": 2, "green": 3}


def _section():
    return dict(
        {s: [] if s in ("Actions", "Resolved") else "" for s in _SECTIONS},
        _extra=[],
        _Actions_raw=[],
        _Resolved_raw=[],
    )


def _warning(notes, text):
    if text not in notes.warnings:
        notes.warnings.append(text)


def _cells(line):
    """Split Markdown cells, decoding our writer's escapes and stripping one padding space per side."""
    if not line.startswith("|") or not line.endswith("|"):
        return None
    cells, value, i = [], "", 1
    while i < len(line) - 1:
        c = line[i]
        if c == "\\" and i + 1 < len(line) - 1 and line[i + 1] in "\\|":
            i += 1
            value += line[i]
        elif c == "|":
            cells.append(value)
            value = ""
        else:
            value += c
        i += 1
    cells.append(value)
    return [c[1:-1] if c.startswith(" ") and c.endswith(" ") else c for c in cells]


def _values(notes):
    for attr in ("summary", "exec_note", "po_satisfaction", "note", "status"):
        getattr(notes, attr).clear()
    for team, sec in notes.teams.items():
        notes.summary[team] = "\n".join(
            re.sub(r"^-\s+", "", line.strip()) for line in sec["Summary"].splitlines() if line.strip()
        )
        for heading, attr in (("Exec note", "exec_note"), ("Note", "note")):
            getattr(notes, attr)[team] = sec[heading].strip("\n")
        for heading, attr, pattern, upper in (
            ("Status", "status", _STATUS, True),
            ("PO satisfaction", "po_satisfaction", _PO_SAT, False),
        ):
            text = sec[heading].strip()
            if text:
                match = pattern.fullmatch(text)
                if match:
                    key = match[1].upper() if upper else int(match[1])
                    getattr(notes, attr)[team] = (key, (match[2] or "").strip())
                else:
                    _warning(notes, f"notes.md {team} has invalid {heading}: {text}")


def parse_notes(text: str) -> Notes:
    """Parse Markdown only, retaining unknown or malformed lines verbatim."""
    notes, team, heading = Notes(), None, None
    for number, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        if line.startswith("<!-- guide: ") and line.endswith(" -->"):
            continue
        marker = re.fullmatch(r"<!-- ledger notes: (.*?) -->", line)
        if marker:
            for entry in marker[1].split("; "):
                if "=" in entry:
                    name, path = entry.split("=", 1)
                    notes.markers[name] = path
                elif entry:
                    _warning(notes, f"notes.md malformed marker: {entry}")
            continue
        if line.startswith("# ") and not line.startswith("## "):
            name = line[2:].strip()
            if name and name not in notes.teams:
                team, heading = name, None
                notes.teams[team] = _section()
                continue
            heading = None
        elif line.startswith("## ") and team:
            name = line[3:]
            if name in _SECTIONS and not notes.teams[team].get("_seen_" + name):
                heading = name
                notes.teams[team]["_seen_" + name] = True
                continue
            heading = None
        sec = notes.teams.get(team)
        if sec is not None and heading in ("Actions", "Resolved"):
            cells = _cells(line)
            if (
                cells
                and len(cells) == 5
                and (cells == _COLUMNS or all(re.fullmatch(r":?-+:?", c.strip()) for c in cells))
            ):
                continue
            if cells and len(cells) == 5:
                person, work, problem, answer, answered = cells
                person, work, problem, answered = (v.strip() for v in (person, work, problem, answered))
                answer = answer if answer.strip() else ""
                item = re.match(r"^#(\d+)(?:\s|$)", work)
                if item or work in ("–", "-", ""):
                    sec[heading].append(
                        dict(
                            id=int(item[1]) if item else None,
                            person=person,
                            work=work,
                            problem=problem,
                            answer=answer,
                            answered=answered,
                        )
                    )
                    continue
            if not line:
                continue
            sec["_" + heading + "_raw"].append(line)
        elif sec is not None and heading:
            sec[heading] += line + "\n"
            continue
        elif sec is not None:
            sec["_extra"].append(line)
        else:
            notes.raw.append(line)
        if line:
            _warning(notes, f"notes.md line {number} preserved: {line}")
    _values(notes)
    return notes


def _metadata(text):
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) (red|amber|grey|green)(?: (.*))?", text)
    if match:
        try:
            date.fromisoformat(match[1])
            labels = match[3].split(", ") if match[3] else []
            if set(labels) <= _LABEL_KINDS.keys():
                return dict(
                    date=match[1], level=match[2], kinds=sorted({_LABEL_KINDS[label] for label in labels})
                )
        except ValueError:
            pass
    return None


def _answered(meta):
    if not meta or not meta.get("date"):
        return ""
    suffix = " " + ", ".join(_KIND_LABELS[k] for k in meta["kinds"]) if meta["kinds"] else ""
    return f"{meta['date']} {meta['level']}{suffix}"


def _key(row):
    return (
        f"i:{row['id']}"
        if row["id"] is not None
        else json.dumps([row["person"], row.get("badge", "")], ensure_ascii=False)
    )


def _bind(notes):
    """Infer missing person-row badges from Answered metadata, problem prefixes, or legacy wording."""
    for sec in notes.teams.values():
        for row in sec["Actions"] + sec["Resolved"]:
            if row["id"] is not None:
                continue
            kinds = (_metadata(row["answered"]) or {}).get("kinds", [])
            badge = row.get("badge") or next(
                (_KIND_LABELS[k] for k in ("none", "nothing", "wip") if k in kinds), ""
            )
            prefix = row["problem"].partition(":")[0]
            if not badge and prefix in ("No work", "Nothing delivered", "Too much WIP"):
                badge = prefix
            if not badge:
                text = row["problem"].lower()
                badge = (
                    "No work"
                    if any(t in text for t in ("no work", "no item", "no open"))
                    else "Nothing delivered"
                    if "delivered" in text
                    else "Too much WIP"
                    if "wip" in text or "in progress" in text
                    else ""
                )
            row["badge"] = badge


def _normal_answer(answer):
    return " ".join(answer.split())


def _publishable_reason(meta: dict, closed: str | None, answer: str) -> bool:
    return bool(
        closed
        and meta.get("closed") == closed
        and meta.get("date")
        and _normal_answer(meta.get("answer", "")) == answer
        and meta.get("carry")
        and meta.get("local")
    )


def _reasons(notes: Notes) -> None:
    """Populate per-team publishable carry-over reason maps."""
    notes.reasons.clear()
    for team, sec in notes.teams.items():
        team_reasons: dict[int, str] = {}
        notes.reasons[team] = team_reasons
        if notes.reported is not None and team not in notes.reported:
            continue
        closed = notes.markers.get(team)
        team_state = notes.state.get(team, {})
        for row in sec["Actions"] + sec["Resolved"]:
            if row["id"] is None or not row["answer"]:
                continue
            answer = _normal_answer(row["answer"])
            if not answer:
                continue
            meta = team_state.get(_key(row), {})
            if _publishable_reason(meta, closed, answer):
                team_reasons[row["id"]] = answer


def _normalize_state_entry(meta: dict) -> bool:
    """Strip legacy sidecar keys and map old 'agreed' kinds to 'carry'; return True if a local entry was downgraded."""
    meta.pop("section", None)
    meta.pop("position", None)
    kinds = meta.get("kinds")
    if isinstance(kinds, list) and all(isinstance(k, str) for k in kinds) and "agreed" in kinds:
        was_local = bool(meta.get("local"))
        meta["kinds"] = sorted({"carry" if k == "agreed" else k for k in kinds})
        meta["local"] = False
        return was_local
    return False


def _read_notes(folder: str, *, warn_migration: bool = True) -> Notes:
    with open(os.path.join(folder, "notes.md"), encoding="utf-8-sig") as f:
        notes = parse_notes(f.read())
    path = os.path.join(folder, ".notes-state.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") != 1 or not isinstance(saved.get("teams"), dict):
                raise ValueError("invalid state structure")
            migrated = False
            for entries in saved["teams"].values():
                if not isinstance(entries, dict):
                    raise ValueError("invalid team state")
                for meta in entries.values():
                    if not isinstance(meta, dict) or not isinstance(meta.get("answer"), str):
                        raise ValueError("invalid answer state")
                    migrated = _normalize_state_entry(meta) or migrated
                    if meta.get("date") and _metadata(_answered(meta)) is None:
                        raise ValueError("invalid baseline")
            notes.state = saved["teams"]
            if migrated and warn_migration:
                _warning(
                    notes,
                    "notes.md migrated legacy carry-over state; review carry-over answers before sharing manager_",
                )
        except (ValueError, TypeError, KeyError, AttributeError):
            _warning(notes, "notes.md has malformed .notes-state.json; importing Answered conservatively")
    _bind(notes)
    return notes


def load_report_notes(directory: str, folder: str, selected: dict, iterations: list, warn: Callable) -> Notes:
    """Load the destination or choose an independent, non-future predecessor per team across
    reports/ and legacy top-level folders."""
    ends = {s["path"]: s["end"] for s in iterations}
    exists = os.path.isfile(os.path.join(folder, "notes.md"))
    notes = _read_notes(folder) if exists else Notes()
    target = os.path.abspath(folder)
    discovered: list[tuple[str, Notes]] = []
    po_history: dict[str, dict[str, int | None]] = {}
    roots = [r for r in (directory, os.path.join(directory, "reports")) if os.path.isdir(r)]
    for entry in sorted((e for r in roots for e in os.scandir(r)), key=lambda e: e.path):
        if not entry.is_dir() or os.path.abspath(entry.path) == target:
            continue
        notes_file = os.path.join(entry.path, "notes.md")
        if not os.path.isfile(notes_file):
            continue
        with open(notes_file, encoding="utf-8-sig") as f:
            parsed = parse_notes(f.read())
        discovered.append((entry.path, parsed))
        for team, path in parsed.markers.items():
            if path in ends and team in parsed.teams:
                po_history.setdefault(team.upper(), {})[path] = (
                    parsed.po_satisfaction[team][0] if team in parsed.po_satisfaction else None
                )
            elif not exists and path not in ends:
                _warning(notes, f"notes.md marker for {team} has unknown sprint path {path}; skipped")
    full_cache: dict[str, Notes] = {}
    for team, sprint in selected.items():
        source: Notes | None = notes if exists else None
        source_path: str | None = None
        if not exists and sprint is not None:
            choices = [
                (ends[n.markers[team]], path)
                for path, n in discovered
                if team in n.teams and n.markers.get(team) in ends and ends[n.markers[team]] <= sprint["end"]
            ]
            if choices:
                source_path = max(choices)[1]
                if source_path not in full_cache:
                    full_cache[source_path] = _read_notes(source_path, warn_migration=False)
                source = full_cache[source_path]
        prior = source.markers.get(team) if source else None
        unresolved = bool(prior and prior not in ends)
        if unresolved:
            _warning(notes, f"notes.md marker for {team} has unknown sprint path {prior}; skipped")
        changed = bool(sprint and prior and not unresolved and prior != sprint["path"])
        if source and team in source.teams:
            sec = copy.deepcopy(source.teams[team])
            if exists and sprint is not None and prior != sprint["path"] and sec["PO satisfaction"].strip():
                sec["PO satisfaction"] = ""
                _warning(notes, f"notes.md {team} PO satisfaction cleared because the closed sprint changed")
            if changed and not exists:
                copied = [r for r in sec["Actions"] + sec["Resolved"] if r["answer"]]
                sec = _section()
                sec["Actions"] = copied
                notes.copied.add(team)
            if unresolved or (changed and exists):
                notes.unscoped.add(team)
            notes.teams[team] = sec
            notes.state[team] = copy.deepcopy(source.state.get(team, {}))
            if not exists:
                for meta in notes.state[team].values():
                    meta["local"] = False
            for message in source.warnings:
                _warning(
                    notes,
                    message
                    if exists or source_path is None
                    else f"{os.path.join(source_path, 'notes.md')}: {message}",
                )
        else:
            notes.teams.setdefault(team, _section())
        if sprint:
            notes.markers[team] = sprint["path"]
        elif prior:
            notes.markers[team] = prior
    notes.teams = {
        **{t: notes.teams[t] for t in selected},
        **{t: s for t, s in notes.teams.items() if t not in selected},
    }
    for team in notes.teams:
        if team not in selected:
            _warning(notes, f"notes.md team {team} is not in this report; preserved and excluded")
    for team, path in notes.markers.items():
        if path not in ends:
            _warning(notes, f"notes.md marker for {team} has unknown sprint path {path}; skipped")
    notes.reported = set(selected)
    notes.po_history = po_history
    _values(notes)
    _reasons(notes)
    for message in notes.warnings:
        warn(message)
    return notes


def _observe(notes, team, key, saved, row, closed, today, copied):
    """Observe Answer changes even when a signal disappeared; tool-cell edits never rebase."""
    previous = notes.state[team].get(key)
    answer = _normal_answer(saved.get("answer", ""))
    if not answer:
        return dict(answer="")
    if previous is not None and _normal_answer(previous.get("answer", "")) == answer:
        return dict(previous, answer=answer)
    visible = saved.get("answered", "")
    baseline = _metadata(visible) if previous is None else None
    imported = baseline is not None
    invalid = previous is None and bool(visible) and baseline is None
    if baseline is None and not invalid:
        source = row or previous or {}
        if "level" in source:
            baseline = dict(date=today.isoformat(), level=source["level"], kinds=sorted(source["kinds"]))
    if baseline is None:
        _warning(notes, f"notes.md {team} {key} has invalid Answered; answer preserved without baseline")
    if copied:
        sprint_scope = None
    elif imported:
        sprint_scope = closed
    else:
        sprint_scope = (row or previous or {}).get("sprint_path", (previous or {}).get("sprint", closed))
    carry = (row or previous or {}).get("carry", False)
    if imported and baseline is not None:
        carry = "carry" in baseline["kinds"]
    return dict(
        baseline or {},
        answer=answer,
        closed=None if copied else closed,
        sprint=sprint_scope,
        carry=carry,
        local=not (copied or imported),
    )


def _reconcile_row(
    notes: Notes, team: str, key: str, saved: dict, row: dict, closed: str | None, today: date, unscoped: bool
) -> dict:
    answer = saved.get("answer", "")
    meta = _observe(notes, team, key, saved, row, closed, today, unscoped)
    valid = bool(meta.get("date"))
    worse = False
    if answer and valid:
        higher = SEVERITY[row["level"]] < SEVERITY[meta["level"]]
        new_kind = bool(set(row["kinds"]) - set(meta["kinds"]))
        ongoing = row["open"] or row["id"] is None
        new_sprint = ongoing and (meta.get("sprint") != row["sprint_path"] or meta.get("closed") != closed)
        worse = higher or new_kind or new_sprint
    reason_ready = not row["carry"] or _publishable_reason(meta, closed, _normal_answer(answer))
    row.update(
        answer=answer,
        answered=_answered(meta),
        worse=worse,
        handled=bool(answer and valid and not worse and reason_ready),
    )
    meta.update(person=row["person"], badge=row["badge"], problem=row["problem"])
    notes.state[team][key] = meta
    value = dict(row, work=f"#{row['id']} {row['title']}" if row["id"] is not None else "–")
    if answer and not value["problem"]:
        value["problem"] = saved.get("problem", "")
    return value


def reconcile_notes(notes: Notes, actions: dict, selected: dict, today: date) -> dict:
    """Own section membership and reconcile DM answers against current action rows."""
    _bind(notes)
    notes.reported = set(selected)
    for team, result in actions.items():
        if team not in selected:
            continue
        sec = notes.teams.setdefault(team, _section())
        state = notes.state.setdefault(team, {})
        closed = selected[team]["path"] if selected[team] else None
        is_copied, is_unscoped = team in notes.copied, team in notes.unscoped
        old, duplicates = {}, []
        for saved in sec["Actions"] + sec["Resolved"]:
            key = _key(saved)
            if key in old:
                duplicates.append(saved)
                _warning(notes, f"notes.md {team} has duplicate row {key}; preserved")
            else:
                old[key] = saved
        active, resolved = [], []
        for row in result["rows"]:
            key = _key(row)
            saved = old.pop(key, {})
            if is_copied and row["id"] is not None and not row["open"]:
                saved = {}
            value = _reconcile_row(notes, team, key, saved, row, closed, today, is_copied or is_unscoped)
            if row["level"] in ("red", "amber"):
                active.append(value)
            elif value["answer"]:
                resolved.append(value)
        for key, saved in old.items():
            if not is_copied:
                meta = _observe(notes, team, key, saved, None, closed, today, is_unscoped)
                meta.update(person=saved["person"], badge=saved.get("badge", ""), problem=saved["problem"])
                state[key] = meta
                saved["answered"] = _answered(meta)
                if saved["answer"]:
                    resolved.append(saved)
        sec["Actions"], sec["Resolved"] = active, resolved + duplicates
        notes.copied.discard(team)
        notes.unscoped.discard(team)
        if closed:
            notes.markers[team] = closed
    _values(notes)
    _reasons(notes)
    return actions


def _markdown(notes):
    """Render notes.md including tool-owned guide lines that are rewritten on every run."""
    markers = "; ".join(f"{k}={v}" for k, v in notes.markers.items())
    lines = [f"<!-- ledger notes: {markers} -->"]
    lines.extend(_GUIDE.format(text) for text in _TOP_GUIDE)
    lines.extend(notes.raw)
    for team, sec in notes.teams.items():
        lines.append("# " + team)
        for heading in _SECTIONS:
            lines.extend(["## " + heading, _GUIDE.format(_SECTION_GUIDE[heading])])
            if heading not in ("Actions", "Resolved"):
                lines.extend(sec[heading].splitlines())
                continue
            lines.extend(["| " + " | ".join(_COLUMNS) + " |", "|---|---|---|---|---|"])
            for row in sec[heading]:
                cells: list[str] = [
                    row.get(k, "") for k in ("person", "work", "problem", "answer", "answered")
                ]
                if row["id"] is None and row.get("badge"):
                    prefix = row["badge"] + ": "
                    cells[2] = prefix + cells[2].removeprefix(prefix)

                def _escape_cell(m: re.Match[str]) -> str:
                    return "\\" + m.group(0)

                escaped = [re.sub(r"\\(?=[\\|])|\|", _escape_cell, c) for c in cells]
                lines.append("| " + " | ".join(escaped) + " |")
            lines.extend(sec["_" + heading + "_raw"])
        lines.extend(sec["_extra"])
    return "\n".join(lines) + "\n"


def write_report_notes(folder: str, notes: Notes) -> None:
    """Serialize both files before replacement; persist .notes-state.json first so an interrupted
    notes.md write can repair Answered on a later run without rebasing."""
    text = _markdown(notes)
    state = json.dumps(dict(version=1, teams=notes.state), indent=2, ensure_ascii=False) + "\n"
    os.makedirs(folder, exist_ok=True)
    temporary = []
    try:
        for name, content in (("notes.md", text), (".notes-state.json", state)):
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as f:
                temporary.append((f.name, os.path.join(folder, name)))
                f.write(content)
        for source, target in reversed(temporary):
            os.replace(source, target)
    finally:
        for source, _ in temporary:
            if os.path.exists(source):
                os.unlink(source)


def carry_reasons(notes: Notes, team: str) -> dict[int, str]:
    """Per-team publishable carry-over reasons, falling back to `notes.items` for direct test fixtures."""
    if team in notes.reasons:
        return notes.reasons[team]
    return notes.items if not notes.teams else {}
