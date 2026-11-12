"""Config and roster: the typed, validated view of config.json and roster.json."""
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .errors import LedgerError
from .home import Home, slug

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "window_sprints": 6, "planned_grace_days": 1, "no_criteria_desc_len": 40,
    "assigned_late_days": 3, "carry_flag_sprints": 3, "returns_flag": 2,
    "backward_flag": 2, "trend_window": 3, "trend_runs": 3,
    "nothing_sprints": 2, "nothing_min_availability": 0.5, "quiet_days": 5,
    "status_returned_tickets": 2, "long_wait_days": 5,
    "long_wait_sprint_ends": 2, "unready_min": 3, "unready_share": 0.3334,
    "midsprint_min": 3, "nothing_in_progress_days": 3, "capacity_share": 0.7,
    "one_contributor_min_tickets": 3, "worked_min_days": 0.02,
    "bulk_edit_count": 10, "bulk_edit_seconds": 60,
    "load_history_min": 3, "load_ratio": 1.5, "load_min_excess": 2,
}

PHASES = ("todo", "doing", "handoff", "done", "removed")
AGENT_TEXT_ACCESS = ("full", "titles", "none")
UNIT_LEVELS = ("stories", "tasks")
DEFAULT_UNIT_TYPES = ("User Story", "Product Backlog Item", "Bug")
SETUP_HINT = "Run the delivery-setup skill to create it."


@dataclass(frozen=True)
class PhaseRule:
    state: str
    phase: str
    column: Optional[str] = None
    column_done: Optional[bool] = None
    wait_label: Optional[str] = None


@dataclass(frozen=True)
class Team:
    name: str
    ado_team: str
    area_paths: Tuple[str, ...]
    phases: Tuple[PhaseRule, ...]
    column_field: Optional[str] = None
    done_field: Optional[str] = None
    exclude_iterations: Tuple[str, ...] = ()


@dataclass(frozen=True)
class BacklogRole:
    """A customer-side role that decides sprint scope, such as the product
    owner. `aliases` are sign-in or display names; they are compared with
    board identities only as keyed hashes and never stored with the data.
    An empty `teams` means every team."""
    id: str
    label: str
    aliases: Tuple[str, ...]
    teams: Tuple[str, ...] = ()

    def applies_to(self, team: str) -> bool:
        return not self.teams or team in self.teams


@dataclass(frozen=True)
class Config:
    org_url: str
    project: str
    timezone: str
    teams: Tuple[Team, ...]
    unit_level: str
    unit_types: Tuple[str, ...]
    not_delivered_states: FrozenSet[str]
    not_delivered_reasons: FrozenSet[str]
    service_identities: Tuple[str, ...]
    holidays: FrozenSet[date]
    thresholds: Dict[str, float]
    vendor_label: str
    agent_text_access: str
    backlog_roles: Tuple[BacklogRole, ...] = ()
    scope_owner: str = "the customer"

    def team(self, name: str) -> Team:
        for t in self.teams:
            if t.name == name:
                return t
        raise LedgerError("unknown team %r" % name,
                          "Teams in config.json: %s."
                          % ", ".join(t.name for t in self.teams))

    def team_for_area(self, area: str) -> Optional[str]:
        """Team owning the longest configured area-path prefix, or None."""
        norm = area.lower().rstrip("\\")
        best, best_len = None, -1
        for t in self.teams:
            for prefix in t.area_paths:
                p = prefix.lower().rstrip("\\")
                if (norm == p or norm.startswith(p + "\\")) and len(p) > best_len:
                    best, best_len = t.name, len(p)
        return best


@dataclass(frozen=True)
class Assignment:
    team: str
    start: date
    end: Optional[date]
    allocation: float

    def covers(self, d: date) -> bool:
        return self.start <= d and (self.end is None or d <= self.end)

    def overlaps(self, start: date, end: Optional[date]) -> bool:
        return ((self.end is None or start <= self.end)
                and (end is None or self.start <= end))


@dataclass(frozen=True)
class Absence:
    start: date
    end: date
    note: str


@dataclass(frozen=True)
class Person:
    id: str
    display_name: str
    identity_ids: Tuple[str, ...]
    aliases: Tuple[str, ...]
    assignments: Tuple[Assignment, ...]
    absences: Tuple[Absence, ...]


class Roster:
    def __init__(self, people: List[Person]):
        self.people = people

    def person(self, pid: str) -> Person:
        for p in self.people:
            if p.id == pid:
                return p
        raise LedgerError("unknown person %r" % pid,
                          "Check roster.json for this id.")

    def match_identity(self, identity_id: Optional[str],
                       names: Iterable[str]) -> Optional[str]:
        """Actor string for the roster person matching id, else alias."""
        if identity_id:
            for p in self.people:
                if identity_id in p.identity_ids:
                    return "p:" + p.id
        wanted = {n.lower() for n in names if n}
        for p in self.people:
            if wanted & {a.lower() for a in p.aliases}:
                return "p:" + p.id
        return None

    def assignment_on(self, pid: str, team: str,
                      d: date) -> Optional[Assignment]:
        for p in self.people:
            if p.id != pid:
                continue
            for a in p.assignments:
                if a.team == team and a.allocation > 0 and a.covers(d):
                    return a
        return None

    def vendor_on(self, actor: Optional[str], team: str, at: datetime,
                  tz: ZoneInfo) -> bool:
        if not actor or not actor.startswith("p:"):
            return False
        local = at.astimezone(tz).date()
        return self.assignment_on(actor[2:], team, local) is not None

    def people_on(self, team: str, start: date, end: date) -> List[Person]:
        return [p for p in self.people
                if any(a.team == team and a.allocation > 0
                       and a.overlaps(start, end) for a in p.assignments)]


def _require(data: Any, key: str, where: str) -> Any:
    if not isinstance(data, dict) or key not in data:
        raise LedgerError("%s is missing %r" % (where, key),
                          "Fix it, or re-run the delivery-setup skill.")
    return data[key]


def _date(text: Any, where: str) -> date:
    try:
        return date.fromisoformat(str(text))
    except ValueError:
        raise LedgerError("%s: %r is not a YYYY-MM-DD date" % (where, text))


def _json_text(value: Any) -> str:
    import json
    return json.dumps(value)[:40]


def _load(home: Home, name: str) -> Any:
    data = home.read_json(name)
    if data is None:
        raise LedgerError("%s not found in %s" % (name, home.root),
                          SETUP_HINT)
    return data


def _team(raw: Dict[str, Any]) -> Team:
    where = "team %r" % raw.get("name")
    rules = tuple(
        PhaseRule(_require(p, "state", where), _require(p, "phase", where),
                  p.get("column"), p.get("column_done"), p.get("wait_label"))
        for p in raw.get("phases", []))
    exclude = raw.get("exclude_iterations") or []
    if not isinstance(exclude, list) or not all(
            isinstance(x, str) for x in exclude):
        raise LedgerError(
            "%s: exclude_iterations must be a list of iteration names or "
            "paths" % where,
            "For example \"exclude_iterations\": [\"PI 5\"].")
    return Team(_require(raw, "name", "a team"),
                _require(raw, "ado_team", where),
                tuple(_require(raw, "area_paths", where)), rules,
                raw.get("column_field"), raw.get("done_field"),
                tuple(exclude))


_ROLE_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def _backlog_roles(raw: Any) -> Tuple[BacklogRole, ...]:
    if raw is None:
        return ()
    hint = ('For example "backlog_roles": [{"id": "po", "label": "Product '
            'owner", "aliases": ["po@customer.example"], "teams": '
            '["Claims"]}].')
    if not isinstance(raw, list):
        raise LedgerError("backlog_roles must be a list", hint)
    out = []
    for r in raw:
        if not isinstance(r, dict):
            raise LedgerError("each backlog role must be an object", hint)
        rid = r.get("id")
        if not isinstance(rid, str) or not _ROLE_ID.match(rid):
            raise LedgerError(
                "backlog role id %s is not valid" % _json_text(rid),
                "Use lower-case letters, digits and _ (up to 24), starting "
                "with a letter, such as \"po\" or \"ba\".")
        label = r.get("label")
        aliases = r.get("aliases")
        teams = r.get("teams", [])
        if not isinstance(label, str) or not label.strip():
            raise LedgerError("backlog role %r has no label" % rid, hint)
        if not isinstance(aliases, list) or not aliases or not all(
                isinstance(a, str) and a.strip() for a in aliases):
            raise LedgerError("backlog role %r needs a list of sign-in names "
                              "in \"aliases\"" % rid, hint)
        if not isinstance(teams, list) or not all(
                isinstance(t, str) for t in teams):
            raise LedgerError("backlog role %r: teams must be a list of team "
                              "names" % rid, hint)
        out.append(BacklogRole(rid, label.strip(),
                               tuple(a.strip() for a in aliases),
                               tuple(teams)))
    ids = [r.id for r in out]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        raise LedgerError("backlog role id(s) used more than once: %s"
                          % ", ".join(twice), "Give every role its own id.")
    return tuple(out)


def load_config(home: Home) -> Config:
    raw = _load(home, "config.json")
    ado = _require(raw, "ado", "config.json")
    tzname = _require(raw, "timezone", "config.json")
    try:
        ZoneInfo(tzname)
    except Exception:
        raise LedgerError("timezone %r is not valid" % tzname,
                          "The zone name could not be loaded; use an IANA "
                          "name such as Europe/Budapest.")
    thresholds = dict(DEFAULT_THRESHOLDS)
    given = raw.get("thresholds") or {}
    unknown = sorted(set(given) - set(DEFAULT_THRESHOLDS))
    if unknown:
        raise LedgerError("unknown threshold(s): %s" % ", ".join(unknown),
                          "Valid keys: %s." % ", ".join(DEFAULT_THRESHOLDS))
    wrong = sorted(k for k, v in given.items()
                   if isinstance(v, bool) or not isinstance(v, (int, float)))
    if wrong:
        raise LedgerError(
            "threshold(s) must be a number: %s" % ", ".join(
                "%s (%s)" % (k, _json_text(given[k])) for k in wrong),
            "Write the value without quotes in config.json, for example "
            "\"%s\": %s." % (wrong[0], DEFAULT_THRESHOLDS[wrong[0]]))
    thresholds.update(given)
    nd = raw.get("not_delivered") or {}
    return Config(
        org_url=_require(ado, "org_url", "config.json ado"),
        project=_require(ado, "project", "config.json ado"),
        timezone=tzname,
        teams=tuple(_team(t) for t in _require(raw, "teams", "config.json")),
        unit_level=raw.get("unit_level", "stories"),
        unit_types=tuple(raw.get("unit_types") or DEFAULT_UNIT_TYPES),
        not_delivered_states=frozenset(nd.get("states", [])),
        not_delivered_reasons=frozenset(nd.get("reasons", [])),
        service_identities=tuple(raw.get("service_identities", [])),
        holidays=frozenset(_date(h, "holidays") for h in raw.get("holidays", [])),
        thresholds=thresholds,
        vendor_label=(raw.get("labels") or {}).get("vendor", "Vendor team"),
        agent_text_access=raw.get("agent_text_access", "none"),
        backlog_roles=_backlog_roles(raw.get("backlog_roles")),
        scope_owner=str((raw.get("labels") or {}).get(
            "scope_owner") or "the customer").strip(),
    )


def load_roster(home: Home) -> Roster:
    raw = _load(home, "roster.json")
    people = []
    for r in _require(raw, "people", "roster.json"):
        pid = _require(r, "id", "a roster person")
        where = "person %r" % pid
        assignments = tuple(
            Assignment(_require(a, "team", where),
                       _date(_require(a, "from", where), where),
                       _date(a["to"], where) if a.get("to") else None,
                       float(a.get("allocation", 1.0)))
            for a in r.get("assignments", []))
        absences = tuple(
            Absence(_date(_require(a, "from", where), where),
                    _date(_require(a, "to", where), where),
                    a.get("note", ""))
            for a in r.get("absences", []))
        people.append(Person(pid, r.get("display_name", pid),
                             tuple(r.get("identity_ids", [])),
                             tuple(r.get("aliases", [])), assignments,
                             absences))
    return Roster(people)


def validate(cfg: Config, roster: Roster) -> None:
    if cfg.unit_level not in UNIT_LEVELS:
        raise LedgerError("unit_level %r is not valid" % cfg.unit_level,
                          "Use one of: %s." % ", ".join(UNIT_LEVELS))
    if cfg.agent_text_access not in AGENT_TEXT_ACCESS:
        raise LedgerError(
            "agent_text_access %r is not valid" % cfg.agent_text_access,
            "Use one of: %s." % ", ".join(AGENT_TEXT_ACCESS))
    for t in cfg.teams:
        for rule in t.phases:
            if rule.phase not in PHASES:
                raise LedgerError(
                    "team %s: phase %r for state %r is not valid"
                    % (t.name, rule.phase, rule.state),
                    "Use one of: %s." % ", ".join(PHASES))
    names = {t.name for t in cfg.teams}
    for r in cfg.backlog_roles:
        unknown = sorted(set(r.teams) - names)
        if unknown:
            raise LedgerError(
                "backlog role %r names unknown team(s): %s"
                % (r.id, ", ".join(unknown)),
                "Teams in config.json: %s." % ", ".join(sorted(names)))
        vendor = sorted({a for a in r.aliases
                         if roster.match_identity(None, [a])})
        if vendor:
            raise LedgerError(
                "backlog role %r lists a roster engineer: %s"
                % (r.id, ", ".join(vendor)),
                "A backlog role is a customer-side person; vendor engineers "
                "are counted as vendor engineers already.")
    seen: Dict[str, str] = {}
    for t in cfg.teams:
        if t.name in seen.values():
            raise LedgerError(
                "team name %r is used more than once in config.json"
                % t.name, "Give every team its own name.")
        folder = slug(t.name)
        if folder in seen:
            raise LedgerError(
                "teams %r and %r would be stored in the same folder (%s)"
                % (seen[folder], t.name, folder),
                "Rename one of them so the names differ in letters or "
                "digits, not only in case, spaces or punctuation.")
        seen[folder] = t.name
    ids = [p.id for p in roster.people]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        raise LedgerError(
            "roster person id(s) used more than once: %s" % ", ".join(twice),
            "Give every person in roster.json their own id.")
    for p in roster.people:
        for a in p.assignments:
            if a.end is not None and a.end < a.start:
                raise LedgerError(
                    "person %s: the assignment to %s ends on %s, before it "
                    "starts on %s" % (p.id, a.team, a.end.isoformat(),
                                      a.start.isoformat()),
                    "Correct the \"from\" and \"to\" dates in roster.json.")
        for ab in p.absences:
            if ab.end < ab.start:
                raise LedgerError(
                    "person %s: an absence ends on %s, before it starts on "
                    "%s" % (p.id, ab.end.isoformat(), ab.start.isoformat()),
                    "Correct the \"from\" and \"to\" dates in roster.json.")
    for i, a in enumerate(cfg.teams):
        for b in cfg.teams[i + 1:]:
            for pa in a.area_paths:
                for pb in b.area_paths:
                    x, y = pa.lower().rstrip("\\"), pb.lower().rstrip("\\")
                    if x == y or x.startswith(y + "\\") or y.startswith(x + "\\"):
                        raise LedgerError(
                            "area paths overlap: %s (%s) and %s (%s)"
                            % (pa, a.name, pb, b.name),
                            "Each area path may belong to one team only.")
    names = {t.name for t in cfg.teams}
    for p in roster.people:
        for a in p.assignments:
            if a.team not in names:
                raise LedgerError(
                    "person %s is assigned to unknown team %r" % (p.id, a.team),
                    "Teams in config.json: %s." % ", ".join(sorted(names)))
        for a in p.assignments:
            total = sum(b.allocation for b in p.assignments
                        if b.covers(a.start))
            if total > 1.0 + 1e-9:
                raise LedgerError(
                    "person %s has total allocation %.2f on %s (more than "
                    "1.0 across teams)" % (p.id, total, a.start.isoformat()),
                    "Shorten an assignment or lower its allocation.")
