"""Fixture builder: synthetic ticket histories in the stored snapshot model."""
import hashlib
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ..config import Config, Roster, load_config, load_roster, validate
from ..home import Home, slug
from ..model import Revision, Snapshot, Sprint, Ticket
from ..workcal import WorkCalendar

_UNSET: Any = object()

DEFAULT_PHASES: List[Dict[str, Any]] = [
    {"state": "New", "phase": "todo"},
    {"state": "Active", "phase": "doing"},
    {"state": "In QA", "phase": "handoff", "wait_label": "QA"},
    {"state": "In Review", "phase": "handoff", "wait_label": "review"},
    {"state": "Closed", "phase": "done"},
    {"state": "Removed", "phase": "removed"},
]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hex(kind: str, name: str) -> str:
    return _sha(kind + ":" + name)[:32]


class TicketBuilder:
    """Accumulates revisions; unspecified fields carry forward."""

    def __init__(self, owner: "PortfolioBuilder", id: int, team: str,
                 type: str, title: str, description: str, acceptance: str,
                 parent: Optional[int], parent_title: str, vendor: bool):
        self._b = owner
        self.id = id
        self.team = team
        self.vendor = vendor
        self.title = title
        self.description = description
        self.acceptance = acceptance
        self.parent_title = parent_title
        self._init = dict(type=type, parent=parent)
        self.revisions: List[Revision] = []
        # Raw (title, description, acceptance) in effect at each revision,
        # parallel to `revisions`; used by FakeAdo to serve real text.
        self.texts: List[Tuple[str, str, str]] = []

    def _append(self, when: str, by: Any, changes: Dict[str, Any]
                ) -> "TicketBuilder":
        ts = self._b.parse_time(when)
        prev = self.revisions[-1] if self.revisions else None
        if prev is not None and ts < prev.changed:
            raise ValueError("ticket %d: revision at %s is earlier than the "
                             "previous one" % (self.id, when))
        if prev is None:
            cur: Dict[str, Any] = dict(
                state="New", reason="", column=None, column_done=None,
                iteration=self._b.backlog_path(self.team),
                area=self._b.area(self.team), assignee=None,
                project="Demo", tags=(), **self._init)
        else:
            cur = {k: getattr(prev, k) for k in (
                "state", "reason", "column", "column_done", "iteration",
                "area", "assignee", "type", "project", "parent", "tags")}
        for k, v in changes.items():
            if v is _UNSET:
                continue
            if k == "iteration":
                v = self._b.iteration_path(self.team, v)
            elif k == "tags":
                v = tuple(v)
            cur[k] = v
        if not self.vendor:
            cur["assignee"] = None
        if by is _UNSET:
            by = cur["assignee"] or self._b.other("sm")
        if not self.vendor:
            by = None
        desc, ac = (self.description, self.acceptance) if self.vendor \
            else ("", "")
        self.texts.append((self.title, desc, ac) if self.vendor
                          else ("", "", ""))
        self.revisions.append(Revision(
            rev=len(self.revisions) + 1, changed=ts, by=by,
            desc_hash=_sha(desc) if self.vendor else "",
            desc_len=len(desc), ac_hash=_sha(ac) if self.vendor else "",
            ac_len=len(ac), **cur))
        return self

    def on(self, when: str, state: Any = _UNSET, iteration: Any = _UNSET,
           assignee: Any = _UNSET, by: Any = _UNSET, reason: Any = _UNSET,
           column: Any = _UNSET, column_done: Any = _UNSET,
           type: Any = _UNSET, project: Any = _UNSET, parent: Any = _UNSET,
           tags: Any = _UNSET, area: Any = _UNSET) -> "TicketBuilder":
        return self._append(when, by, dict(
            state=state, iteration=iteration, assignee=assignee,
            reason=reason, column=column, column_done=column_done,
            type=type, project=project, parent=parent, tags=tags, area=area))

    def edit(self, when: str, description: Optional[str] = None,
             acceptance: Optional[str] = None, title: Optional[str] = None,
             by: Any = _UNSET) -> "TicketBuilder":
        if not self.vendor:
            raise ValueError("squad tickets carry no text")
        if description is not None:
            self.description = description
        if acceptance is not None:
            self.acceptance = acceptance
        if title is not None:
            self.title = title
        return self._append(when, by, {})

    def to_ticket(self) -> Ticket:
        return Ticket(id=self.id, revisions=list(self.revisions),
                      title=self.title, description=self.description,
                      acceptance=self.acceptance,
                      parent_title=self.parent_title)

    def to_squad(self) -> Ticket:
        revs = []
        for r in self.revisions:
            revs.append(Revision(**{**r.__dict__, "by": None,
                                    "assignee": None, "desc_hash": "",
                                    "desc_len": 0, "ac_hash": "",
                                    "ac_len": 0}))
        return Ticket(id=self.id, revisions=revs)


class PortfolioBuilder:
    def __init__(self, tz: str = "Europe/Zurich", unit_level: str = "stories",
                 unit_types: Optional[List[str]] = None,
                 not_delivered_reasons: Optional[List[str]] = None,
                 not_delivered_states: Optional[List[str]] = None,
                 holidays: Optional[List[str]] = None,
                 thresholds: Optional[Dict[str, float]] = None,
                 agent_text_access: str = "none"):
        self.tz = tz
        self._zone = ZoneInfo(tz)
        self._opts = dict(
            unit_level=unit_level, unit_types=unit_types,
            not_delivered_reasons=not_delivered_reasons or [],
            not_delivered_states=not_delivered_states or [],
            holidays=holidays or [], thresholds=thresholds or {},
            agent_text_access=agent_text_access)
        self._cal = WorkCalendar(tz, [date.fromisoformat(h)
                                      for h in (holidays or [])])
        self._teams: Dict[str, Dict[str, Any]] = {}
        self._people: Dict[str, Dict[str, Any]] = {}
        self._tickets: List[TicketBuilder] = []
        self._roles: List[Dict[str, Any]] = []
        self.scope_owner: Optional[str] = None

    # identities
    @staticmethod
    def other(name: str) -> str:
        return "o:" + _hex("other", name)

    @staticmethod
    def service(name: str) -> str:
        return "s:" + _hex("service", name)

    @staticmethod
    def alias_of(name: str) -> str:
        """The invented sign-in name behind other(name)."""
        return name.replace(" ", ".") + "@customer.example"

    def backlog_role(self, id: str, label: str, others: List[str],
                     teams: Optional[List[str]] = None) -> None:
        """A backlog role held by the identities other(name) for each name
        in others; write() records their alias hashes as a collection
        would."""
        self._roles.append(dict(id=id, label=label, others=list(others),
                                teams=list(teams or [])))

    # time and paths
    def parse_time(self, text: str) -> datetime:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                naive = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError("bad time %r, want 'YYYY-MM-DD HH:MM'" % text)
        return naive.replace(tzinfo=self._zone).astimezone(timezone.utc)

    def area(self, team: str) -> str:
        return self._teams[team]["area"]

    def backlog_path(self, team: str) -> str:
        return "Demo\\" + team

    def iteration_path(self, team: str, name: Optional[str]) -> str:
        if name is None:
            return self.backlog_path(team)
        return "Demo\\%s\\%s" % (team, name)

    # declarations
    def team(self, name: str, area: str, sprints: Optional[list] = None,
             phases: Optional[List[Dict[str, Any]]] = None
             ) -> "PortfolioBuilder":
        self._teams[name] = dict(area=area, sprints=list(sprints or []),
                                 phases=phases or DEFAULT_PHASES)
        return self

    def person(self, pid: str, display_name: str, team: str, start: str,
               end: Optional[str] = None, allocation: float = 1.0) -> str:
        self._people[pid] = dict(display_name=display_name, assignments=[],
                                 absences=[])
        self.assign(pid, team, start=start, end=end, allocation=allocation)
        return "p:" + pid

    def assign(self, pid: str, team: str, start: str,
               end: Optional[str] = None, allocation: float = 1.0) -> None:
        a: Dict[str, Any] = {"team": team, "from": start,
                             "allocation": allocation}
        if end:
            a["to"] = end
        self._people[pid]["assignments"].append(a)

    def absence(self, pid: str, start: str, end: str, note: str = "") -> None:
        self._people[pid]["absences"].append(
            {"from": start, "to": end, "note": note})

    def ticket(self, id: int, team: str, type: str = "User Story",
               title: str = "", description: str = "",
               acceptance: str = "", parent: Optional[int] = None,
               parent_title: str = "") -> TicketBuilder:
        t = TicketBuilder(self, id, team, type, title, description,
                          acceptance, parent, parent_title, vendor=True)
        self._tickets.append(t)
        return t

    def squad_ticket(self, id: int, team: str, type: str = "User Story",
                     parent: Optional[int] = None) -> TicketBuilder:
        t = TicketBuilder(self, id, team, type, "", "", "", parent, "",
                          vendor=False)
        self._tickets.append(t)
        return t

    # outputs
    def _config_json(self) -> Dict[str, Any]:
        o = self._opts
        raw: Dict[str, Any] = {
            "ado": {"org_url": "https://dev.azure.com/demo",
                    "project": "Demo"},
            "timezone": self.tz,
            "teams": [{"name": n, "ado_team": n, "area_paths": [t["area"]],
                       "phases": t["phases"]}
                      for n, t in self._teams.items()],
            "unit_level": o["unit_level"],
            "not_delivered": {"states": o["not_delivered_states"],
                              "reasons": o["not_delivered_reasons"]},
            "service_identities": [],
            "holidays": o["holidays"],
            "thresholds": o["thresholds"],
            "agent_text_access": o["agent_text_access"],
        }
        if o["unit_types"]:
            raw["unit_types"] = o["unit_types"]
        if self._roles:
            raw["backlog_roles"] = [
                {"id": r["id"], "label": r["label"],
                 "aliases": [self.alias_of(n) for n in r["others"]],
                 "teams": r["teams"]} for r in self._roles]
        if self.scope_owner:
            raw["labels"] = {"scope_owner": self.scope_owner}
        return raw

    def write_identity_index(self, home: Home) -> None:
        """The identity index a collection would have written for the
        backlog roles' identities."""
        from ..ado.pseudonym import INDEX_FILE, alias_hash, \
            load_or_create_key
        if not self._roles:
            return
        key = load_or_create_key(home)
        home.write_json({self.other(n): [alias_hash(key, self.alias_of(n))]
                         for r in self._roles for n in r["others"]},
                        INDEX_FILE)

    def _roster_json(self) -> Dict[str, Any]:
        return {"people": [dict(id=pid, **p)
                           for pid, p in self._people.items()]}

    def _parsed(self):
        with tempfile.TemporaryDirectory() as d:
            home = Home(Path(d))
            home.write_json(self._config_json(), "config.json")
            home.write_json(self._roster_json(), "roster.json")
            return load_config(home), load_roster(home)

    def config(self) -> Config:
        return self._parsed()[0]

    def roster(self) -> Roster:
        return self._parsed()[1]

    def sprints(self, team: str) -> List[Sprint]:
        out: List[Sprint] = []
        for name, s, f in sorted(self._teams[team]["sprints"],
                                 key=lambda x: x[1]):
            start, end = self._cal.sprint_bounds(date.fromisoformat(s),
                                                 date.fromisoformat(f))
            path = self.iteration_path(team, name)
            out.append(Sprint(team=team, id=slug(path), name=name, path=path,
                              start=start, end=end,
                              period_start=out[-1].end if out else start))
        return out

    def snapshot(self, cutoff: Optional[str] = None,
                 team: Optional[str] = None) -> Snapshot:
        teams = [team] if team else list(self._teams)
        mine = [t for t in self._tickets if t.team in teams]
        if cutoff is not None:
            cut = self.parse_time(cutoff)
        else:
            cut = max([r.changed for t in mine for r in t.revisions]
                      or [datetime.now(timezone.utc)])
        return Snapshot(
            tickets=[t.to_ticket() for t in mine if t.vendor],
            squad=[t.to_squad() for t in mine],
            sprints={n: self.sprints(n) for n in teams},
            meta={"cutoff": cut.isoformat(), "account": "builder",
                  "auth_mode": "builder",
                  "coverage": {"missing": [], "unreadable": []},
                  "tool_version": "test"})

    def write(self, home: Home) -> None:
        cfg, roster = self._parsed()
        validate(cfg, roster)
        home.write_json(self._config_json(), "config.json")
        home.write_json(self._roster_json(), "roster.json")
        self.write_identity_index(home)
        for name in self._teams:
            sps = self.sprints(name)
            sid = sps[-1].id if sps else "current"
            self.snapshot(team=name).save(
                home.team_sprint_dir(name, sid) / "snapshot")
