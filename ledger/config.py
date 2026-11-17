"""Configuration files: config.json, team.csv and the per-sprint notes.md."""
import csv
import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass, field, fields
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

from ledger.replay import local_tz

DEFAULT_NOT_DELIVERED = ["Removed", "Won't Fix", "Won't Do", "Cut", "Duplicate", "Obsolete", "Deferred",
                         "Abandoned"]
CONFIG_FILE = "config.json"
TEAM_COLUMNS = ["name", "email", "team", "joined", "left", "allocation"]


@dataclass
class Person:
    name: str
    emails: List[str]
    team: str
    joined: Optional[date]
    left: Optional[date]
    allocation: float = 1.0


def _clean_name(raw: str) -> str:
    name = re.sub(r"\s*\([^()]*\)$", "", raw.strip().strip('"').strip())
    if "," in name:
        last, first = name.split(",", 1)
        name = "%s %s" % (first.strip(), last.strip())
    return " ".join(name.split())


def parse_staff(text: str, warnings: Optional[List[str]] = None) -> List[Tuple[str, str]]:
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
            warnings.append("staff.txt entry '%s' has no email address; skipped" % chunk)
        pairs += [(name, email) for name, email in found if "@" in email]
    return pairs


def merge_staff(pairs: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    merged: Dict[str, List[str]] = {}
    for name, email in pairs:
        emails = merged.setdefault(name, [])
        if email not in emails:
            emails.append(email)
    return merged


def _date(text: str) -> Optional[date]:
    text = (text or "").strip()
    return date.fromisoformat(text) if text else None


def read_team(path: str) -> List[Person]:
    people: Dict[str, Person] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row["name"].strip()
            email = row["email"].strip().lower()
            person = people.get(name)
            if person is None:
                alloc = (row.get("allocation") or "").strip()
                person = Person(name, [], row["team"].strip().upper(), _date(row.get("joined")),
                                _date(row.get("left")), float(alloc) if alloc else 1.0)
                people[name] = person
            if email and email not in person.emails:
                person.emails.append(email)
    return list(people.values())


def write_team(path: str, people: List[Person]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(TEAM_COLUMNS)
        for p in people:
            for email in p.emails or [""]:
                w.writerow([p.name, email, p.team,
                            p.joined.isoformat() if p.joined else "",
                            p.left.isoformat() if p.left else "",
                            "%g" % p.allocation])


def email_index(people: List[Person]) -> Dict[str, Person]:
    return {e.lower(): p for p in people for e in p.emails}


@dataclass
class Config:
    organization: str = ""
    project: str = ""
    query: str = ""
    timezone: str = "UTC"
    teams: Dict[str, Dict[str, str]] = field(default_factory=dict)
    onboarding_sprints: int = 2
    half_day_codes: List[str] = field(default_factory=list)
    waiting_states: List[str] = field(default_factory=list)
    hold_states: List[str] = field(default_factory=list)
    not_delivered_reasons: List[str] = field(default_factory=lambda: list(DEFAULT_NOT_DELIVERED))
    staff: str = ""
    vacations: str = ""
    name_aliases: Dict[str, str] = field(default_factory=dict)
    squad_teams: Dict[str, str] = field(default_factory=dict)
    history_days: int = 200


def load_config(directory: str, warn: Callable[[str], None] = lambda line: None) -> Config:
    """Read config.json; unknown keys are passed to `warn`, a bad count or time zone raises."""
    with open(os.path.join(directory, CONFIG_FILE), encoding="utf-8-sig") as f:
        data = json.load(f)
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(data) - known)
    if unknown:
        warn("config.json keys not recognised and ignored: %s" % ", ".join(unknown))
    cfg = Config(**{k: v for k, v in data.items() if k in known})
    for team, entry in cfg.teams.items():
        c = (entry or {}).get("count")
        is_list = isinstance(c, list) and c and all(isinstance(t, str) and t.strip() for t in c)
        if not is_list and c not in (None, "", "stories", "tasks"):
            raise ValueError("config.json: team %s has count '%s'; use \"stories\", \"tasks\" or a "
                             "list of work item types" % (team, c))
    local_tz(cfg.timezone)
    return cfg


def save_config(directory: str, cfg: Config) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, CONFIG_FILE), "w", encoding="utf-8") as f:
        json.dump(cfg.__dict__, f, indent=2, ensure_ascii=False)
        f.write("\n")


@dataclass
class Notes:
    items: Dict[int, Tuple[str, str]] = field(default_factory=dict)
    summary: Dict[str, str] = field(default_factory=dict)
    note: Dict[str, str] = field(default_factory=dict)
    exec_note: Dict[str, str] = field(default_factory=dict)
    status: Dict[str, Tuple[str, str]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    teams: dict = field(default_factory=dict)
    markers: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    raw: List[str] = field(default_factory=list)
    reported: Optional[set] = None


_SECTIONS = ("Summary", "Exec note", "Note", "Status", "Actions", "Resolved")
_COLUMNS = ["Person", "Work item", "Problem", "Answer", "Answered"]
_STATUS = re.compile(r"^(GREEN|AMBER|RED)\s*(?:-\s*(.*))?$", re.I)
_KIND_LABELS = {"old": "Chase", "agreed": "PO agreement", "waiting": "Waiting",
                "stale": "No movement", "none": "No work", "nothing": "Nothing delivered",
                "wip": "Too much WIP", "never_active": "Never active", "reopened": "Reopened",
                "shared": "Shared", "carry": "Carry-over", "held": "On Hold",
                "not_started": "Not started"}
_LABEL_KINDS = {label: kind for kind, label in _KIND_LABELS.items()}
_LEVELS = ("green", "grey", "amber", "red")


def _section():
    return dict({s: [] if s in ("Actions", "Resolved") else "" for s in _SECTIONS},
                _extra=[], _Actions_raw=[], _Resolved_raw=[], _items={})


def _warning(notes, text):
    if text not in notes.warnings:
        notes.warnings.append(text)


def _cells(line):
    """Split Markdown cells, decoding only the escapes emitted by our writer."""
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
    # Exactly one space on each side belongs to the table's formatting.
    return [c[1:-1] if c.startswith(" ") and c.endswith(" ") else c for c in cells]


def _values(notes):
    for attr in ("summary", "exec_note", "note", "status"):
        getattr(notes, attr).clear()
    for team, sec in notes.teams.items():
        notes.summary[team] = "\n".join(re.sub(r"^-\s+", "", line.strip())
                                          for line in sec["Summary"].splitlines() if line.strip())
        for heading, attr in (("Exec note", "exec_note"), ("Note", "note")):
            getattr(notes, attr)[team] = sec[heading].strip("\n")
        text = sec["Status"].strip()
        if text:
            match = _STATUS.fullmatch(text)
            if match:
                notes.status[team] = (match[1].upper(), (match[2] or "").strip())
            else:
                _warning(notes, "notes.md %s has invalid Status: %s" % (team, text))


def parse_notes(text: str) -> Notes:
    """Parse Markdown only, retaining unknown or malformed lines verbatim."""
    notes, team, heading = Notes(), None, None
    for number, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        marker = re.fullmatch(r"<!-- ledger notes: (.*?) -->", line)
        if marker:
            for entry in marker[1].split("; "):
                if "=" in entry:
                    name, path = entry.split("=", 1)
                    notes.markers[name] = path
                elif entry:
                    _warning(notes, "notes.md malformed marker: %s" % entry)
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
            if cells and len(cells) == 5 and (cells == _COLUMNS or all(
                    re.fullmatch(r":?-+:?", c.strip()) for c in cells)):
                continue
            if cells and len(cells) == 5:
                person, work, problem, answer, answered = cells
                person, work, problem, answered = (v.strip() for v in (person, work, problem, answered))
                answer = answer if answer.strip() else ""
                item = re.match(r"^#(\d+)(?:\s|$)", work)
                if item or work in ("–", "-", ""):
                    sec[heading].append(dict(id=int(item[1]) if item else None, person=person,
                                             work=work, problem=problem, answer=answer, answered=answered))
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
            _warning(notes, "notes.md line %d preserved: %s" % (number, line))
    _values(notes)
    return notes


def _metadata(text):
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}) (red|amber|grey|green)(?: (.*))?", text)
    if match:
        try:
            date.fromisoformat(match[1])
            labels = match[3].split(", ") if match[3] else []
            if set(labels) <= _LABEL_KINDS.keys():
                return dict(date=match[1], level=match[2],
                            kinds=sorted({_LABEL_KINDS[label] for label in labels}))
        except ValueError:
            pass
    return None


def _answered(meta):
    if not meta or not meta.get("date"):
        return ""
    return "%s %s%s" % (meta["date"], meta["level"],
                         " " + ", ".join(_KIND_LABELS[k] for k in meta["kinds"]) if meta["kinds"] else "")


def _key(row):
    return "i:%d" % row["id"] if row["id"] is not None else json.dumps(
        [row["person"], row.get("badge", "")], ensure_ascii=False)


def _bind(notes):
    for sec in notes.teams.values():
        for row in sec["Actions"] + sec["Resolved"]:
            if row["id"] is not None:
                continue
            kinds = (_metadata(row["answered"]) or {}).get("kinds", [])
            badge = row.get("badge") or next((
                _KIND_LABELS[k] for k in ("none", "nothing", "wip") if k in kinds), "")
            prefix = row["problem"].partition(":")[0]
            if not badge and prefix in ("No work", "Nothing delivered", "Too much WIP"):
                badge = prefix
            if not badge:
                # Import older notes that have neither Answered metadata nor a badge prefix.
                text = row["problem"].lower()
                badge = ("No work" if any(t in text for t in ("no work", "no item", "no open")) else
                         "Nothing delivered" if "delivered" in text else
                         "Too much WIP" if "wip" in text or "in progress" in text else "")
            row["badge"] = badge


def _normal_answer(answer):
    return " ".join(answer.split())


def _agreements(notes):
    notes.items.clear()
    owners = {}
    for team, sec in notes.teams.items():
        sec["_items"] = {}
        if notes.reported is not None and team not in notes.reported:
            continue
        for row in sec["Actions"] + sec["Resolved"]:
            meta = notes.state.get(team, {}).get(_key(row), {})
            if row["id"] is None or not row["answer"]:
                continue
            answer = _normal_answer(row["answer"])
            match = re.match(r"^(agreed|not agreed|unknown):\s*(.*)$", answer, re.I | re.S)
            valid = (meta.get("closed") == notes.markers.get(team) and meta.get("date")
                     and _normal_answer(meta.get("answer", "")) == answer and meta.get("carry"))
            if not valid:
                continue
            reason = match[2] if match and meta.get("local") else ""
            value = (match[1].lower() if match else "unknown", reason)
            sec["_items"][row["id"]] = value
            owners.setdefault(row["id"], []).append(value)
    # Ambiguous ids must be consumed through notes_for_team.
    notes.items.update({i: values[0] for i, values in owners.items() if len(values) == 1})


def _read_notes(folder):
    with open(os.path.join(folder, "notes.md"), encoding="utf-8-sig") as f:
        notes = parse_notes(f.read())
    path = os.path.join(folder, ".notes-state.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            if saved.get("version") != 1 or not isinstance(saved.get("teams"), dict):
                raise ValueError("invalid state structure")
            for team, entries in saved["teams"].items():
                if not isinstance(entries, dict):
                    raise ValueError("invalid team state")
                for key, meta in entries.items():
                    if not isinstance(meta, dict) or not isinstance(meta.get("answer"), str):
                        raise ValueError("invalid answer state")
                    meta.pop("section", None)
                    meta.pop("position", None)
                    if meta.get("date") and _metadata(_answered(meta)) is None:
                        raise ValueError("invalid baseline")
            notes.state = saved["teams"]
        except (ValueError, TypeError, KeyError, AttributeError):
            _warning(notes, "notes.md has malformed .notes-state.json; importing Answered conservatively")
    _bind(notes)
    return notes


def load_report_notes(directory: str, folder: str, selected: dict, iterations: list,
                      warn: Callable) -> Notes:
    """Load the destination or choose an independent, non-future predecessor per team."""
    ends = {s["path"]: s["end"] for s in iterations}
    exists = os.path.isfile(os.path.join(folder, "notes.md"))
    notes = _read_notes(folder) if exists else Notes()
    sources = []
    if not exists and os.path.isdir(directory):
        for entry in sorted(os.scandir(directory), key=lambda e: e.path):
            if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "notes.md")):
                source = _read_notes(entry.path)
                sources.append((entry.path, source))
                for team, path in source.markers.items():
                    if path not in ends:
                        _warning(notes, "notes.md marker for %s has unknown sprint path %s; skipped" % (team, path))
    for team, sprint in selected.items():
        source = notes if exists else None
        if not exists and sprint is not None:
            choices = [(ends[n.markers[team]], path, n) for path, n in sources
                       if team in n.teams and n.markers.get(team) in ends
                       and ends[n.markers[team]] <= sprint["end"]]
            source_path, source = max(choices, key=lambda c: c[:2])[1:] if choices else (None, None)
        prior = source.markers.get(team) if source else None
        unresolved = bool(prior and prior not in ends)
        if unresolved:
            _warning(notes, "notes.md marker for %s has unknown sprint path %s; skipped" % (team, prior))
        changed = bool(sprint and prior and not unresolved and prior != sprint["path"])
        if source and team in source.teams:
            sec = copy.deepcopy(source.teams[team])
            if changed and not exists:
                copied = [r for r in sec["Actions"] + sec["Resolved"] if r["answer"]]
                sec = _section()
                sec["Actions"] = copied
                sec["_copied"] = True
            if unresolved or (changed and exists):
                sec["_unscoped"] = True
            notes.teams[team] = sec
            notes.state[team] = copy.deepcopy(source.state.get(team, {}))
            if not exists:
                for meta in notes.state[team].values():
                    meta["local"] = False
            for message in source.warnings:
                _warning(notes, message if exists else "%s: %s" % (
                    os.path.join(source_path, "notes.md"), message))
        else:
            notes.teams.setdefault(team, _section())
        if sprint:
            notes.markers[team] = sprint["path"]
        elif prior:
            notes.markers[team] = prior
    notes.teams = {**{t: notes.teams[t] for t in selected},
                   **{t: s for t, s in notes.teams.items() if t not in selected}}
    for team in notes.teams:
        if team not in selected:
            _warning(notes, "notes.md team %s is not in this report; preserved and excluded" % team)
    for team, path in notes.markers.items():
        if path not in ends:
            _warning(notes, "notes.md marker for %s has unknown sprint path %s; skipped" % (team, path))
    notes.reported = set(selected)
    _values(notes)
    _agreements(notes)
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
        _warning(notes, "notes.md %s %s has invalid Answered; answer preserved without baseline" % (team, key))
    if copied:
        sprint_scope = None
    elif imported:
        sprint_scope = closed
    else:
        sprint_scope = (row or previous or {}).get("sprint_path", (previous or {}).get("sprint", closed))
    carry = (row or previous or {}).get("carry", False)
    if imported:
        carry = bool(set(baseline["kinds"]) & {"carry", "agreed"})
    return dict(baseline or {}, answer=answer, closed=None if copied else closed,
                sprint=sprint_scope, carry=carry, local=not (copied or imported))


def reconcile_notes(notes: Notes, actions: dict, selected: dict, today: date,
                    observed: Optional[dict] = None) -> dict:
    """Own section membership; `observed` reuses raw baselines for the final signal view."""
    _bind(notes)
    notes.reported = set(selected)
    for team, result in actions.items():
        if team not in selected:
            continue
        sec = notes.teams.setdefault(team, _section())
        state = notes.state.setdefault(team, {})
        closed = selected[team]["path"] if selected[team] else None
        old = {}
        duplicates = []
        for saved in sec["Actions"] + sec["Resolved"]:
            key = _key(saved)
            if key in old:
                duplicates.append(saved)
                _warning(notes, "notes.md %s has duplicate row %s; preserved" % (team, key))
            else:
                old[key] = saved
        observed_rows = {_key(r): r for r in observed[team]["rows"]} if observed else None
        active, resolved = [], []
        for row in result["rows"]:
            key = _key(row)
            saved = old.pop(key, {})
            if sec.get("_copied") and row["id"] is not None and not row["open"]:
                saved = {}
            answer = saved.get("answer", "")
            if observed_rows is None:
                meta = _observe(notes, team, key, saved, row, closed, today,
                                sec.get("_copied") or sec.get("_unscoped"))
                valid = bool(meta.get("date"))
                worse = False
                if answer and valid:
                    higher = _LEVELS.index(row["level"]) > _LEVELS.index(meta["level"])
                    new_kind = bool(set(row["kinds"]) - set(meta["kinds"]))
                    ongoing = row["open"] or row["id"] is None
                    new_sprint = ongoing and (meta.get("sprint") != row["sprint_path"]
                                              or meta.get("closed") != closed)
                    worse = higher or new_kind or new_sprint
                row.update(answer=answer, answered=_answered(meta), worse=worse,
                           handled=bool(answer and valid and not worse))
                meta.update(person=row["person"], badge=row["badge"], problem=row["problem"])
                state[key] = meta
            else:
                source = observed_rows.get(key, {})
                row.update(answer=answer, answered=source.get("answered", ""),
                           worse=source.get("worse", False), handled=source.get("handled", False))
            value = dict(row, work="#%d %s" % (row["id"], row["title"]) if row["id"] is not None else "–")
            if answer and not value["problem"]:
                value["problem"] = saved.get("problem", "")
            if row["level"] in ("red", "amber"):
                active.append(value)
            elif answer:
                resolved.append(value)
        for key, saved in old.items():
            if not sec.get("_copied"):
                if observed_rows is None:
                    meta = _observe(notes, team, key, saved, None, closed, today, sec.get("_unscoped"))
                    meta.update(person=saved["person"], badge=saved.get("badge", ""), problem=saved["problem"])
                    state[key] = meta
                    saved["answered"] = _answered(meta)
                if saved["answer"]:
                    resolved.append(saved)
        sec["Actions"], sec["Resolved"] = active, resolved + duplicates
        sec.pop("_copied", None)
        sec.pop("_unscoped", None)
        if closed:
            notes.markers[team] = closed
    _values(notes)
    _agreements(notes)
    return actions


def _markdown(notes):
    lines = ["<!-- ledger notes: %s -->" % "; ".join("%s=%s" % pair for pair in notes.markers.items())]
    lines.extend(notes.raw)
    for team, sec in notes.teams.items():
        lines.append("# " + team)
        for heading in _SECTIONS:
            lines.append("## " + heading)
            if heading not in ("Actions", "Resolved"):
                lines.extend(sec[heading].splitlines())
                continue
            lines.extend(["| " + " | ".join(_COLUMNS) + " |", "|---|---|---|---|---|"])
            for row in sec[heading]:
                cells = [row.get(k, "") for k in ("person", "work", "problem", "answer", "answered")]
                if row["id"] is None and row.get("badge"):
                    prefix = row["badge"] + ": "
                    cells[2] = prefix + cells[2].removeprefix(prefix)
                escaped = [re.sub(r"\\(?=[\\|])|\|", lambda m: "\\" + m[0], c) for c in cells]
                lines.append("| " + " | ".join(escaped) + " |")
            lines.extend(sec["_" + heading + "_raw"])
        lines.extend(sec["_extra"])
    return "\n".join(lines) + "\n"


def write_report_notes(folder: str, notes: Notes) -> None:
    """Serialize both files before replacement; the observed answer repairs interrupted pairs."""
    text = _markdown(notes)
    state = json.dumps(dict(version=1, teams=notes.state), indent=2, ensure_ascii=False) + "\n"
    os.makedirs(folder, exist_ok=True)
    temporary = []
    try:
        for name, content in (("notes.md", text), (".notes-state.json", state)):
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as f:
                temporary.append((f.name, os.path.join(folder, name)))
                f.write(content)
        # Answer text is unchanged by this write. Persist its provenance first so a
        # failed notes replacement can repair Answered on a later day without rebasing.
        for source, target in reversed(temporary):
            os.replace(source, target)
    finally:
        for source, _ in temporary:
            if os.path.exists(source):
                os.unlink(source)


def notes_for_team(notes: Notes, team: str) -> Notes:
    result = copy.copy(notes)
    result.items = dict(notes.teams.get(team, {}).get("_items", {}))
    return result
