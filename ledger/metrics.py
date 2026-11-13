"""Sprint figures per team, replayed from the revision cache at fixed instants."""
import math
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

from ledger.config import Config, Notes, Person
from ledger.replay import ACTIVE, History, day_end, day_start, local_tz

P85 = 0.85
CARRIED_FLAG = 2
WAIT_FLAG_DAYS = 3
WAIT_RED_DAYS = 10
STALE_DAYS = 5
WIP_FLAG = 3
GREEN_SHARE = 0.8
TREND = 6
CLOSED = ("Completed", "Removed")


def percentile(values: List[int], p: float) -> Optional[int]:
    """Nearest rank: sorted(v)[ceil(p*n)-1]; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(round(p * len(ordered), 9)) - 1)]


def _stats(values: List[int]) -> dict:
    return {"n": len(values), "median": percentile(values, 0.5), "p85": percentile(values, P85)}


def histories(data: dict, not_delivered: Sequence[str]) -> Dict[int, History]:
    rows = {}  # type: Dict[int, List[dict]]
    for r in data["revisions"]:
        rows.setdefault(r["id"], []).append(r)
    return {i: History(rs, not_delivered) for i, rs in rows.items()}


def _row(h: History, t: Optional[datetime]) -> Optional[dict]:
    """The revision in force at `t`, or the last one when `t` is None."""
    return h.rows[-1] if t is None else h.at(t)


def level_of(h: History, levels, histories, at: Optional[datetime] = None) -> Optional[str]:
    """"stories" or "tasks" at `at` (default: now); an unlisted Bug follows its parent's level."""
    row = _row(h, at)
    if row is None:
        return None
    for level in ("stories", "tasks"):
        if row["type"] in levels.get(level, []):
            return level
    if row["type"] != "Bug":
        return None
    parent = histories.get(row["parent"])
    prow = _row(parent, at) if parent is not None else None
    return "tasks" if prow is not None and prow["type"] in levels.get("stories", []) else "stories"


def _by_team(mapping: dict, team: str, default):
    return next((v for k, v in mapping.items() if k.lower() == team.lower()), default)


def _emails(people: List[Person], team: str) -> set:
    return {e.lower() for p in people if p.team.lower() == team.lower() for e in p.emails}


def _key(sprint: dict):
    return sprint["start"], sprint["path"]


def team_sprints(data: dict, cfg: Config, people: List[Person], team: str,
                 until: Optional[datetime] = None) -> List[dict]:
    """Dated iterations that held one of the team's counted items, from history up to `until`."""
    return _Team(data, cfg, people, team).sprints(until)


def _with(sprints: List[dict], sprint: dict) -> List[dict]:
    rest = [s for s in sprints if s["path"] != sprint["path"]]
    return sorted(rest + [sprint], key=_key)


def is_onboarding(person: Person, team_sprints: List[dict], sprint: dict, n: int) -> bool:
    """In the sprint containing `joined` (or the next one, if `joined` falls in a gap shorter than
    a sprint) or the following n - 1 sprints."""
    if person.joined is None:
        return False
    ordered = _with(team_sprints, sprint)
    first = next((i for i, s in enumerate(ordered) if date.fromisoformat(s["end"]) >= person.joined),
                 None)
    if first is None:
        return False
    start, end = (date.fromisoformat(ordered[first][k]) for k in ("start", "end"))
    if start - person.joined > end - start:  # joined long before any known sprint
        return False
    return first <= ordered.index(sprint) < first + n


def _working_days(person: Person, start: date, end: date) -> List[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5 and (person.joined is None or d >= person.joined) \
                and (person.left is None or d < person.left):
            days.append(d)
        d += timedelta(days=1)
    return days


class _Team:
    def __init__(self, data: dict, cfg: Config, people: List[Person], team: str):
        self.cfg, self.data = cfg, data
        self.tz = local_tz(cfg.timezone)
        self.hist = histories(data, cfg.not_delivered_reasons)
        self.children = {}  # type: Dict[int, set]
        for r in data["revisions"]:
            if r["parent"] is not None:
                self.children.setdefault(r["parent"], set()).add(r["id"])
        self.count = _by_team(cfg.teams, team, {}).get("count", "stories")
        self.emails = _emails(people, team)

    def counted(self, t: Optional[datetime]) -> List[History]:
        return [self.hist[i] for i in sorted(self.hist)
                if level_of(self.hist[i], self.data["levels"], self.hist, t) == self.count]

    def kids(self, h: History, t: Optional[datetime]) -> List[History]:
        """Child items whose parent at `t` is `h` (stories level only)."""
        if self.count != "stories":
            return []
        kids = (self.hist[c] for c in sorted(self.children.get(h.id, ())))
        return [k for k in kids if (_row(k, t) or {}).get("parent") == h.id]

    def sprints(self, until: Optional[datetime]) -> List[dict]:
        def seen(h):
            return [r for r, ts in zip(h.rows, h.times) if until is None or ts <= until]

        paths = set()
        for h in self.counted(until):
            if any((r["assigned"] or "").lower() in self.emails
                   for x in [h] + self.kids(h, until) for r in seen(x)):
                paths |= {r["iteration"] for r in seen(h)}
        return sorted((dict(s) for s in self.data["iterations"]
                       if s["path"] in paths and s.get("start") and s.get("end")), key=_key)

    def instants(self, sprint: dict):
        start, end = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["end"])
        return day_start(start, self.tz), day_end(start, self.tz), day_end(end, self.tz)

    def _assigned(self, h: History, t0, t1) -> bool:
        if any(a.lower() in self.emails for a in h.assignees_active(t1)):
            return True
        if h.first_active is not None and h.first_active <= t1:
            return False
        rows = [h.at(t0), h.at(t1)]
        return any(r and r["assigned"] and r["assigned"].lower() in self.emails for r in rows)

    def ours(self, h: History, t0, t1) -> bool:
        """Ruling D: assignee while active up to t1, else (never active) at t0 or t1; or a child."""
        if self._assigned(h, t0, t1):
            return True
        return any(self._assigned(k, t0, t1) for k in self.kids(h, t1))

    def local(self, t) -> date:
        return t.astimezone(self.tz).date()

    def cycle_days(self, h: History) -> Optional[int]:
        fa, done = h.first_active, h.done_at
        if fa is None or done is None or fa > done:
            return None
        return (self.local(done) - self.local(fa)).days + 1

    def figures(self, sprint: dict) -> Dict[str, list]:
        path = sprint["path"]
        s0, t0, t1 = self.instants(sprint)
        f = {k: [] for k in ("planned", "delivered_planned", "added", "removed", "delivered",
                             "late", "reopened", "carry", "open")}
        for h in self.counted(t1):
            done, row0, row1 = h.done_at, h.at(t0), h.at(t1)
            on_path0 = row0 is not None and row0["iteration"] == path
            planned = on_path0 and row0["category"] not in CLOSED and not (done and done <= t0)
            added = not on_path0 and any(t0 < ts < t1 and r["iteration"] == path
                                         for r, ts in zip(h.rows, h.times))
            delivered = done is not None and s0 <= done < t1
            reopened = done is not None and any(
                s0 <= ts < t1 and ts > done and r["category"] in ACTIVE
                and h.rows[i - 1]["category"] not in ACTIVE
                for i, (r, ts) in enumerate(zip(h.rows, h.times)) if i)
            if not (planned or added or delivered or reopened) or not self.ours(h, t0, t1):
                continue
            for key, hit in (("planned", planned), ("added", added), ("delivered", delivered),
                             ("reopened", reopened)):
                if hit:
                    f[key].append(h.id)
            if not planned:
                continue
            if done is not None and done < t1:
                f["delivered_planned"].append(h.id)
                continue
            f["open"].append(h.id)
            if done is not None:
                f["late"].append(h.id)
            cut = row1["category"] == "Removed" or (
                row1["category"] == "Completed" and row1["reason"] in h.not_delivered)
            if row1["iteration"] != path or cut:  # Rulings F and G
                f["removed"].append(h.id)
            elif row1["category"] not in CLOSED:
                f["carry"].append(h.id)
        return f

    def parent_title(self, h: History, t1) -> str:
        parent = h.at(t1)["parent"]
        if parent is None:
            return "Other"
        title = self.data.get("parents", {}).get(str(parent))
        if title is None and parent in self.hist:
            title = self.hist[parent].title
        return title or "Parent %s" % parent

    def waiting_share(self, ids: List[int]) -> Optional[float]:
        waited = total = 0.0
        for i in ids:
            h = self.hist[i]
            if self.cycle_days(h) is None:
                continue
            waited += h.waiting_seconds(h.first_active, h.done_at, self.cfg.waiting_states)
            total += (h.done_at - h.first_active).total_seconds()
        return waited / total if total else None

    def capacity(self, people: List[Person], team: str, sprints: List[dict], sprint: dict,
                 days_off: Dict[str, Dict[date, float]]) -> dict:
        start, end = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["end"])
        engineers = onboarding = 0
        nominal = available = 0.0
        for p in people:
            days = _working_days(p, start, end) if p.team.lower() == team.lower() else []
            if not days:
                continue
            engineers += 1
            onboarding += is_onboarding(p, sprints, sprint, self.cfg.onboarding_sprints)
            off = sum(days_off.get(p.name, {}).get(d, 0.0) for d in days)
            nominal += len(days) * p.allocation
            available += (len(days) - off) * p.allocation
        return {"engineers": engineers, "onboarding": onboarding,
                "available": available, "nominal": nominal}


def team_sprint(data: dict, cfg: Config, people: List[Person], team: str, sprint: dict,
                notes: Notes, days_off: Dict[str, Dict[date, float]]) -> dict:
    t = _Team(data, cfg, people, team)
    t1 = t.instants(sprint)[2]
    sprints = t.sprints(t1)
    upto = [s for s in _with(sprints, sprint) if _key(s) <= _key(sprint)]
    figs = {s["path"]: t.figures(s) for s in upto}
    f = figs[sprint["path"]]
    end = date.fromisoformat(sprint["end"])

    def cycles(ids):
        return [c for c in (t.cycle_days(t.hist[i]) for i in ids) if c is not None]

    carry = []
    for i in f["carry"]:
        h, row = t.hist[i], t.hist[i].at(t1)
        fa = h.first_active
        agreed, reason = notes.items.get(i, ("unknown", ""))
        carry.append({"id": i, "title": row["title"],
                      "age": (end - t.local(fa)).days + 1 if fa is not None and fa <= t1 else None,
                      "sprints": sum(1 for g in figs.values() if i in g["open"]),
                      "state": row["state"], "waiting": row["state"] in cfg.waiting_states,
                      "agreed": agreed, "reason": reason})
    groups = {}  # type: Dict[str, int]
    for i in sorted(set(f["delivered"]) | set(f["carry"])):
        title = t.parent_title(t.hist[i], t1)
        groups[title] = groups.get(title, 0) + 1
    window = upto[-TREND:]
    window_ids = sorted({i for s in window for i in figs[s["path"]]["delivered"]})
    return {
        "team": _by_team({k: k for k in cfg.teams}, team, team), "count": t.count,
        "sprint": {k: sprint[k] for k in ("name", "path", "start", "end")},
        **{k: f[k] for k in ("planned", "delivered_planned", "added", "removed", "delivered",
                             "late", "reopened")},
        "cycle": _stats(cycles(f["delivered"])), "cycle_window": _stats(cycles(window_ids)),
        "waiting_share": t.waiting_share(f["delivered"]),
        "carry": carry,
        "worked_on": [[k, v] for k, v in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))],
        "trend": [{"sprint": s["name"], "planned": len(figs[s["path"]]["planned"]),
                   "delivered_planned": len(figs[s["path"]]["delivered_planned"]),
                   "delivered": len(figs[s["path"]]["delivered"]),
                   "p85": percentile(cycles(figs[s["path"]]["delivered"]), P85)} for s in window],
        "capacity": t.capacity(people, team, sprints, sprint, days_off),
        "notes": {"summary": _by_team(notes.summary, team, ""), "note": _by_team(notes.note, team, ""),
                  "exec_note": _by_team(notes.exec_note, team, "")},
    }
