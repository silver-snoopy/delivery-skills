"""Data model and snapshot files: the seam between collection and analysis."""
import enum
import json
import os
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class Phase(enum.IntEnum):
    REMOVED = -1
    TODO = 0
    DOING = 1
    HANDOFF = 2
    DONE = 3


def _dt_out(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def _dt_in(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


@dataclass
class Revision:
    rev: int
    changed: datetime
    by: Optional[str]
    state: str
    reason: str
    column: Optional[str]
    column_done: Optional[bool]
    iteration: str
    area: str
    assignee: Optional[str]
    type: str
    project: str
    parent: Optional[int]
    tags: Tuple[str, ...]
    desc_hash: str
    desc_len: int
    ac_hash: str
    ac_len: int

    def to_json(self) -> Dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["changed"] = _dt_out(self.changed)
        d["tags"] = list(self.tags)
        return d

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Revision":
        d = dict(d)
        d["changed"] = _dt_in(d["changed"])
        d["tags"] = tuple(d["tags"])
        return cls(**d)


@dataclass
class Ticket:
    id: int
    revisions: List[Revision]
    title: str = ""
    description: str = ""
    acceptance: str = ""
    parent_title: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "title": self.title,
                "description": self.description,
                "acceptance": self.acceptance,
                "parent_title": self.parent_title,
                "revisions": [r.to_json() for r in self.revisions]}

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Ticket":
        return cls(id=d["id"],
                   revisions=[Revision.from_json(r) for r in d["revisions"]],
                   title=d.get("title", ""),
                   description=d.get("description", ""),
                   acceptance=d.get("acceptance", ""),
                   parent_title=d.get("parent_title", ""))


@dataclass(frozen=True)
class Sprint:
    team: str
    id: str
    name: str
    path: str
    start: datetime
    end: datetime
    period_start: datetime

    def to_json(self) -> Dict[str, Any]:
        return {"team": self.team, "id": self.id, "name": self.name,
                "path": self.path, "start": _dt_out(self.start),
                "end": _dt_out(self.end),
                "period_start": _dt_out(self.period_start)}

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "Sprint":
        return cls(team=d["team"], id=d["id"], name=d["name"], path=d["path"],
                   start=_dt_in(d["start"]), end=_dt_in(d["end"]),
                   period_start=_dt_in(d["period_start"]))


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(str(tmp), 0o600)
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise


def _jsonl(tickets: List[Ticket]) -> str:
    return "".join(json.dumps(t.to_json(), ensure_ascii=False) + "\n"
                   for t in tickets)


def _read_jsonl(path: Path) -> List[Ticket]:
    with open(str(path), encoding="utf-8") as f:
        return [Ticket.from_json(json.loads(line))
                for line in f if line.strip()]


@dataclass
class Snapshot:
    tickets: List[Ticket]
    squad: List[Ticket]
    sprints: Dict[str, List[Sprint]]
    meta: Dict[str, Any]

    def save(self, dir: Path) -> None:
        dir = Path(dir)
        missing = []
        d = dir
        while not d.exists() and d != d.parent:
            missing.append(d)
            d = d.parent
        for m in reversed(missing):  # every new directory private
            m.mkdir(mode=0o700, exist_ok=True)
            os.chmod(str(m), 0o700)
        _write_atomic(dir / "tickets.jsonl", _jsonl(self.tickets))
        _write_atomic(dir / "squad.jsonl", _jsonl(self.squad))
        _write_atomic(dir / "sprints.json", json.dumps(
            {team: [s.to_json() for s in sps]
             for team, sps in self.sprints.items()},
            ensure_ascii=False, indent=2))
        _write_atomic(dir / "meta.json",
                      json.dumps(self.meta, ensure_ascii=False, indent=2))

    @classmethod
    def load(cls, dir: Path) -> "Snapshot":
        dir = Path(dir)
        with open(str(dir / "sprints.json"), encoding="utf-8") as f:
            raw = json.load(f)
        with open(str(dir / "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        return cls(tickets=_read_jsonl(dir / "tickets.jsonl"),
                   squad=_read_jsonl(dir / "squad.jsonl"),
                   sprints={t: [Sprint.from_json(s) for s in sps]
                            for t, sps in raw.items()},
                   meta=meta)
