"""Configuration files: config.json, team.csv and the per-sprint notes.txt."""
import csv
import json
import os
import re
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
    name = raw.strip().strip('"').strip()
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
            if email not in person.emails:
                person.emails.append(email)
    return list(people.values())


def write_team(path: str, people: List[Person]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(TEAM_COLUMNS)
        for p in people:
            for email in p.emails:
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
    not_delivered_reasons: List[str] = field(default_factory=lambda: list(DEFAULT_NOT_DELIVERED))
    staff: str = ""
    vacations: str = ""
    name_aliases: Dict[str, str] = field(default_factory=dict)
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
        if (entry or {}).get("count") not in (None, "", "stories", "tasks"):
            raise ValueError("config.json: team %s has count '%s'; use \"stories\" or \"tasks\""
                             % (team, entry["count"]))
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


_ITEM = re.compile(r"^#(\d+)\s+(not agreed|agreed|unknown)\s*:\s*(.*)$", re.I)
_TEAM = re.compile(r"^(.+?)\s+(summary|note|exec note|status)\s*:\s*(.*)$", re.I)
_STATUS = re.compile(r"^(GREEN|AMBER|RED)\s*(?:-\s*(.*))?$", re.I)


def parse_notes(text: str) -> Notes:
    notes = Notes()
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        m = _ITEM.match(line)
        if m:
            notes.items[int(m.group(1))] = (m.group(2).lower(), m.group(3).strip())
            continue
        m = _TEAM.match(line)
        if m:
            team, key, value = m.group(1).strip(), m.group(2).lower(), m.group(3).strip()
            if key == "status":
                s = _STATUS.match(value)
                if s:
                    notes.status[team] = (s.group(1).upper(), (s.group(2) or "").strip())
                    continue
            else:
                {"summary": notes.summary, "note": notes.note,
                 "exec note": notes.exec_note}[key][team] = value
                continue
        notes.warnings.append("notes.txt line %d ignored: %s" % (n, line))
    return notes
