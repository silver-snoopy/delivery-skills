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
MAX_SPRINT_DAYS = 42  # longer dated iterations are releases or PIs, not sprints
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


def is_sprint(it: dict) -> bool:
    """A dated iteration no longer than MAX_SPRINT_DAYS."""
    if not (it.get("start") and it.get("end")):
        return False
    days = (date.fromisoformat(it["end"]) - date.fromisoformat(it["start"])).days + 1
    return days <= MAX_SPRINT_DAYS


def _by_team(mapping: dict, team: str, default):
    return next((v for k, v in mapping.items() if k.lower() == team.lower()), default)


def count_of(cfg: Config, team: str) -> str:
    """The team's counting level from config.json; "stories" when the team has none."""
    return _by_team(cfg.teams, team, {}).get("count") or "stories"


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


def _workdays_after(a: date, b: date) -> int:
    """Mon-Fri days after `a` up to and including `b`."""
    return sum(1 for k in range(1, (b - a).days + 1) if (a + timedelta(days=k)).weekday() < 5)


def _is_waiting(row: dict, states: Sequence[str]) -> bool:
    return row["state"] in states or any(str(t).lower() == "blocked" for t in row["tags"] or [])


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
        self.count = count_of(cfg, team)
        self.emails = _emails(people, team)
        self.sprint_at = {s["path"]: s for s in data["iterations"] if is_sprint(s)}

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
        return sorted((dict(s) for p, s in self.sprint_at.items() if p in paths), key=_key)

    def instants(self, sprint: dict):
        start, end = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["end"])
        return day_start(start, self.tz), day_end(start, self.tz), day_end(end, self.tz)

    @staticmethod
    def _assigned(h: History, t0, t1, emails: set) -> bool:
        if any(a.lower() in emails for a in h.assignees_active(t1)):
            return True
        if h.first_active is not None and h.first_active <= t1:
            return False
        rows = [h.at(t0), h.at(t1)]
        return any(r and r["assigned"] and r["assigned"].lower() in emails for r in rows)

    def ours(self, h: History, t0, t1, emails: Optional[set] = None) -> bool:
        """Ours: one of `emails` (default: the team's) was the assignee while the item was active
        up to t1; an item never active by t1 counts by its assignee at t0 or t1. At the stories
        level the same test on a child item also makes the item ours."""
        emails = self.emails if emails is None else emails
        return any(self._assigned(x, t0, t1, emails) for x in [h] + self.kids(h, t1))

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
            # Carry-over: still open on this sprint or on a later sprint at the end. Removed: cut,
            # or moved to a path that is not a later sprint (backlog, area root, release).
            later = self.sprint_at.get(row1["iteration"])
            moved = row1["iteration"] != path and not (later and later["start"] > sprint["start"])
            if moved or h.cut(row1):
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

    def members(self, people: List[Person], team: str, sprints: List[dict], sprint: dict,
                days_off: Dict[str, Dict[date, float]]) -> List[dict]:
        """Team members with working days in the sprint, ordered by name, with their capacity."""
        start, end = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["end"])
        found = []
        for p in sorted(people, key=lambda p: p.name):
            days = _working_days(p, start, end) if p.team.lower() == team.lower() else []
            if days:
                off = sum(days_off.get(p.name, {}).get(d, 0.0) for d in days)
                found.append({"person": p, "nominal": len(days) * p.allocation,
                              "available": (len(days) - off) * p.allocation,
                              "onboarding": is_onboarding(p, sprints, sprint,
                                                          self.cfg.onboarding_sprints)})
        return found

    def mine(self, h: History, emails: set, t1) -> bool:
        """Assignee while active up to t1, or of a child whose parent at t1 is `h`."""
        return any(a.lower() in emails for x in [h] + self.kids(h, t1)
                   for a in x.assignees_active(t1))

    def active_during(self, h: History, s0, t1) -> bool:
        return any(r is not None and r["category"] in ACTIVE
                   for x in [h] + self.kids(h, t1)
                   for r in [x.at(s0)] + [r for r, ts in zip(x.rows, x.times) if s0 <= ts <= t1])

    def open_age(self, h: History, t1, end: date) -> Optional[int]:
        row, fa = h.at(t1), h.first_active
        if row is None or row["category"] in CLOSED or fa is None or fa > t1:
            return None
        return (end - self.local(fa)).days + 1

    def assigned_any(self, emails: set, s0, t1) -> bool:
        """Any revision in [s0, t1] assigned to `emails`, or an open item assigned to them at s0."""
        for h in self.hist.values():
            row = h.at(s0)
            if row is not None and row["category"] not in CLOSED \
                    and (row["assigned"] or "").lower() in emails:
                return True
            if any(s0 <= ts <= t1 and (r["assigned"] or "").lower() in emails
                   for r, ts in zip(h.rows, h.times)):
                return True
        return False

    def person(self, m: dict, f: dict, counted: List[History], sprint: dict) -> dict:
        p, (s0, _, t1) = m["person"], self.instants(sprint)
        end, emails = date.fromisoformat(sprint["end"]), {e.lower() for e in p.emails}
        mine = [h for h in counted if self.mine(h, emails, t1)]
        delivered = [h.id for h in mine if h.id in f["delivered"]]
        ages = [(self.open_age(h, t1, end), h.id) for h in mine]
        oldest = max(((a, -i) for a, i in ages if a is not None), default=None)
        return {"name": p.name, "available": m["available"], "nominal": m["nominal"],
                "delivered": delivered,
                "in_progress": [h.id for h in mine if h.at(t1)["category"] in ACTIVE],
                "oldest": [-oldest[1], oldest[0]] if oldest else None,
                "median": percentile([c for c in (self.cycle_days(self.hist[i]) for i in delivered)
                                      if c is not None], 0.5),
                "items": [h.id for h in mine if h.id in delivered or self.active_during(h, s0, t1)],
                "onboarding": m["onboarding"],
                "no_assignment": not self.assigned_any(emails, s0, t1)}

    def since(self, h: History, t1, waiting: bool) -> Optional[date]:
        """Local date the item last entered a waiting state (or Blocked), or last changed
        (counting changes to its children at the stories level)."""
        rows = [(r, ts) for r, ts in zip(h.rows, h.times) if ts <= t1]
        if not waiting:
            return self.local(max(ts for x in [h] + self.kids(h, t1) for ts in x.times
                                  if ts <= t1))
        start = None
        for r, ts in reversed(rows):
            if not _is_waiting(r, self.cfg.waiting_states):
                break
            start = ts
        return self.local(start) if start is not None else None

    def rest(self, members: List[dict], counted: List[History], sprint: dict) -> Optional[dict]:
        """Delivered items at the team's level under members' main areas that are not ours."""
        s0, t0, t1 = self.instants(sprint)
        areas = set()
        for m in members:
            emails = {e.lower() for e in m["person"].emails}
            seen = {}  # type: Dict[str, int]
            for h in self.hist.values():
                for r, ts in zip(h.rows, h.times):
                    if ts <= t1 and (r["assigned"] or "").lower() in emails:
                        seen[r["area"]] = seen.get(r["area"], 0) + 1
            if seen:
                areas.add(min(seen.items(), key=lambda kv: (-kv[1], kv[0]))[0])
        ids = [h.id for h in counted if h.at(t1)["area"] in areas and h.done_at is not None
               and s0 <= h.done_at < t1 and not self.ours(h, t0, t1)]
        if not ids:
            return None
        cycles = [c for c in (self.cycle_days(self.hist[i]) for i in ids) if c is not None]
        def workers(h):  # engineers on the children (stories level), else the item's assignees
            kids = {a.lower() for k in self.kids(h, t1) for a in k.assignees_active(t1)}
            return kids or {a.lower() for a in h.assignees_active(t1)}

        return {"assignees": len({a for i in ids for a in workers(self.hist[i])}),
                "delivered": len(ids), "median": percentile(cycles, 0.5),
                "p85": percentile(cycles, P85), "waiting_share": self.waiting_share(ids)}

    def unknown(self, people: List[Person], counted: List[History], sprint: dict) -> List[tuple]:
        """(id, email) for assignees on the sprint path in a roster domain but not on the roster,
        on the team's counted items and their children."""
        s0, _, t1 = self.instants(sprint)
        roster = {e.lower() for p in people for e in p.emails}
        domains = {e.split("@")[-1] for e in roster}
        found = set()
        for h in {x.id: x for c in counted for x in [c] + self.kids(c, t1)}.values():
            rows = [h.at(s0)] + [r for r, ts in zip(h.rows, h.times) if s0 < ts <= t1]
            for r in rows:
                email = ((r or {}).get("assigned") or "").lower()
                if r and r["iteration"] == sprint["path"] and email not in roster \
                        and email.split("@")[-1] in domains:
                    found.add((h.id, email))
        return sorted(found)


ASK = {"old": "Ask what is blocking it.",
       "agreed": "Confirm with the PO and record it in notes.txt.",
       "waiting": "Raise it with the PO.",
       "stale": "Ask whether it is moving and the board is behind, or it is stuck.",
       "none": "Find out what they worked on; if it was off-board work, add it to notes.txt.",
       "nothing": "Ask what is blocking them.",
       "wip": "Ask which item to finish first.",
       "reopened": "Ask why it was reopened.",
       "hygiene": "Fix the board or add the email to team.csv.",
       "never_active": "Ask them to move items through Active on the board.",
       "shared": "Check that both teams should count it."}
_LEVELS = ("red", "amber", "grey")


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _ids(ids) -> str:
    return ", ".join("#%d" % i for i in ids)


def _attention(t: _Team, f: dict, carry: List[dict], people: List[dict], waits: Dict[int, tuple],
               stale: Dict[int, tuple], unknown: List[tuple], p85: Optional[int],
               open_ages: Dict[int, int], shared: Dict[int, List[str]]) -> List[dict]:
    """DM flags at sprint end, ordered red, amber, grey; then by item id, person name, text."""
    found = []

    def add(level, ask, text, item=None, name=""):
        found.append((_LEVELS.index(level), item is None, item or 0, name, text,
                      {"level": level, "text": text, "ask": ASK[ask]}))

    def old(age):
        return p85 is not None and age is not None and age > p85

    red_carry = set()
    for c in carry:
        if old(c["age"]) and c["sprints"] >= CARRIED_FLAG:
            red_carry.add(c["id"])
            add("red", "old", "#%d has been carried over for %d sprints and is %d days old, older "
                "than the squad's P85 of %d days." % (c["id"], c["sprints"], c["age"], p85), c["id"])
        elif c["sprints"] >= CARRIED_FLAG:
            add("amber", "old", "#%d has been carried over for %d sprints."
                % (c["id"], c["sprints"]), c["id"])
        if c["agreed"] != "agreed":
            if c["agreed"] == "not agreed":
                why = "and was not agreed with the PO"
            else:
                why = "and whether the PO agreed is unknown" if c["reason"] \
                    else "with no note in notes.txt"
            add("amber", "agreed", "#%d was carried over %s." % (c["id"], why), c["id"])
    for i, age in sorted(open_ages.items()):
        if old(age) and i not in red_carry:
            add("amber", "old", "#%d is %d days old, older than the squad's P85 of %d days."
                % (i, age, p85), i)
    for i, (what, days) in sorted(waits.items()):
        if days > WAIT_FLAG_DAYS:
            add("amber", "waiting", "#%d has waited %s for %d working days." % (i, what, days), i)
    for i, (state, days) in sorted(stale.items()):
        if days >= STALE_DAYS:
            add("amber", "stale", "#%d is %s with no board change for %d working days."
                % (i, state, days), i)
    for p in people:
        if p["onboarding"] or p["available"] <= 0:
            pass
        elif p["no_assignment"]:
            add("red", "none", "%s had %g available days but no item assigned during the sprint."
                % (p["name"], p["available"]), name=p["name"])
        elif not p["delivered"]:
            add("amber", "nothing", "%s had %g available days and delivered nothing."
                % (p["name"], p["available"]), name=p["name"])
        if len(p["in_progress"]) > WIP_FLAG:
            add("amber", "wip", "%s has %s in progress: %s." % (
                p["name"], _plural(len(p["in_progress"]), "item"), _ids(p["in_progress"])),
                name=p["name"])
    for i in f["delivered"]:
        fa, done = t.hist[i].first_active, t.hist[i].done_at
        if fa is None or fa > done:
            add("grey", "never_active", "#%d was closed without ever being in progress." % i, i)
    for i in f["reopened"]:
        add("grey", "reopened", "#%d was reopened." % i, i)
    for i, email in unknown:
        add("grey", "hygiene", "#%d is assigned to %s, which is not in team.csv." % (i, email), i)
    for i, names in sorted(shared.items()):
        add("grey", "shared", "#%d is counted for teams %s and %s." % (
            i, ", ".join(names[:-1]), names[-1]), i)
    return [entry[-1] for entry in sorted(found, key=lambda e: e[:-1])]


def _status(f: dict, carry: List[dict], p85: Optional[int], waits: Dict[int, tuple],
            manual: Optional[tuple]) -> dict:
    """GREEN/AMBER/RED by the share of planned work delivered and old unagreed carry-overs."""
    if manual is not None:
        return {"colour": manual[0], "reason": manual[1], "manual": True}
    denom = len(f["planned"]) - len(f["removed"])
    removed = (", not counting %s" % _plural(len(f["removed"]), "removed item")
               if f["removed"] else "")
    if denom:
        share = len(f["delivered_planned"]) / denom
        parts = ["%d%% of planned work items delivered%s"
                 % (math.floor(share * 100), removed)]
    else:
        share = 1.0
        parts = ["All planned work items were removed from the sprint" if f["removed"]
                 else "There were no planned work items"]
    if p85 is None:  # no age to judge: the agreement test applies to every carry-over
        parts.append("too few finished work items in the last %d sprints to judge age" % TREND)
        old, kind = [c["id"] for c in carry], "carry-over"
    else:
        old = [c["id"] for c in carry if c["age"] is not None and c["age"] > p85]
        kind = "old carry-over"
    bad = [c["id"] for c in carry if c["id"] in old and c["agreed"] != "agreed"]
    many = len(old) > 1
    if not old:
        if p85 is not None:
            parts.append("no carry-over is older than the squad's P85")
    elif not bad:
        parts.append("the %s %s%s (%s) %s agreed with the PO" % (
            len(old) if many else "one", kind, "s" if many else "", _ids(old),
            "were" if many else "was"))
    elif len(bad) == len(old):
        parts.append("the %s %s%s (%s) %s not recorded as agreed with the PO" % (
            len(old) if many else "one", kind, "s" if many else "", _ids(old),
            "are" if many else "is"))
    else:
        parts.append("%d of %d %ss (%s) %s not recorded as agreed with the PO" % (
            len(bad), len(old), kind, _ids(bad), "are" if len(bad) > 1 else "is"))
    long_waits = [(i, d) for i, (_, d) in sorted(waits.items()) if d > WAIT_RED_DAYS]
    parts += ["#%d has been waiting (in a waiting state or blocked) for %d working days" % w
              for w in long_waits]
    met = (share >= GREEN_SHARE) + (not bad)
    colour = "RED" if long_waits or met == 0 else ("GREEN" if met == 2 else "AMBER")
    return {"colour": colour, "reason": "; ".join(parts) + ".", "manual": False}


def team_sprint(data: dict, cfg: Config, people: List[Person], team: str, sprint: dict,
                notes: Notes, days_off: Dict[str, Dict[date, float]]) -> dict:
    t = _Team(data, cfg, people, team)
    _, t0, t1 = t.instants(sprint)
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
    cycle_window = _stats(cycles(window_ids))
    counted = t.counted(t1)
    members = t.members(people, team, sprints, sprint, days_off)
    persons = [t.person(m, f, counted, sprint) for m in members]
    others = {o: _emails(people, o) for o in sorted({p.team for p in people if p.team})
              if o.lower() != team.lower() and count_of(cfg, o) == t.count}
    shared = {}  # type: Dict[int, List[str]]
    for i in sorted(set(f["planned"] + f["added"] + f["delivered"] + f["reopened"])):
        also = [o for o, emails in others.items() if t.ours(t.hist[i], t0, t1, emails)]
        if also:
            shared[i] = sorted(also + [team.upper()])
    open_ages, waits, stale = {}, {}, {}  # type: Dict[int, int], Dict[int, tuple], Dict[int, tuple]
    for h in counted:
        if h.id not in f["carry"] and not t.mine(h, t.emails, t1):
            continue
        row, age = h.at(t1), t.open_age(h, t1, end)
        if row["category"] in CLOSED:
            continue
        if age is not None:
            open_ages[h.id] = age
        if _is_waiting(row, cfg.waiting_states):
            what = "in %s" % row["state"] if row["state"] in cfg.waiting_states \
                else "with the Blocked tag"
            waits[h.id] = (what, _workdays_after(t.since(h, t1, True), end))
        elif row["category"] in ACTIVE:
            stale[h.id] = (row["state"], _workdays_after(t.since(h, t1, False), end))
    return {
        "team": team, "count": t.count,
        "sprint": {k: sprint[k] for k in ("name", "path", "start", "end")},
        **{k: f[k] for k in ("planned", "delivered_planned", "added", "removed", "delivered",
                             "late", "reopened")},
        "cycle": _stats(cycles(f["delivered"])), "cycle_window": cycle_window,
        "waiting_share": t.waiting_share(f["delivered"]),
        "carry": carry,
        "worked_on": [[k, v] for k, v in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))],
        "trend": [{"sprint": s["name"], "planned": len(figs[s["path"]]["planned"]),
                   "delivered_planned": len(figs[s["path"]]["delivered_planned"]),
                   "delivered": len(figs[s["path"]]["delivered"]),
                   "p85": percentile(cycles(figs[s["path"]]["delivered"]), P85)} for s in window],
        "capacity": {"engineers": len(members),
                     "onboarding": sum(m["onboarding"] for m in members),
                     "available": sum(m["available"] for m in members),
                     "nominal": sum(m["nominal"] for m in members)},
        "people": persons,
        "rest": t.rest(members, counted, sprint),
        "attention": _attention(t, f, carry, persons, waits, stale, t.unknown(people, counted, sprint),
                                cycle_window["p85"], open_ages, shared),
        "status": _status(f, carry, cycle_window["p85"], waits,
                          _by_team(notes.status, team, None)),
        "notes": {"summary": _by_team(notes.summary, team, ""), "note": _by_team(notes.note, team, ""),
                  "exec_note": _by_team(notes.exec_note, team, "")},
    }
