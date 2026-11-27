"""Sprint figures per team, replayed from the revision cache at fixed instants."""
import math
import re
from statistics import median
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from ledger.config import (Config, Notes, Person, count_of, metrics_of, show_flow_of,
                           team_value as _by_team)
from ledger.replay import ACTIVE, History, day_end, day_start, is_held, is_waiting, local_tz, parse_ts

P85 = 0.85
CARRIED_FLAG = 2
WAIT_FLAG_DAYS = 3
WAIT_RED_DAYS = 10
STALE_DAYS = 5
WIP_FLAG = 3
NOT_STARTED_DAY = 3
GREEN_SHARE = 0.8
TREND = 6
NOW = 3
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


def _emails(people: List[Person], team: str) -> set:
    return {e.lower() for p in people if p.team.lower() == team.lower() for e in p.emails if e}


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
        self.names = {e.lower(): p.name for p in people if p.team.lower() == team.lower()
                      for e in p.emails if e}
        self.sprint_at = {s["path"]: s for s in data["iterations"] if is_sprint(s)}

    def is_counted(self, h: History, t: Optional[datetime]) -> bool:
        if isinstance(self.count, list):
            return (_row(h, t) or {}).get("type") in self.count
        return level_of(h, self.data["levels"], self.hist, t) == self.count

    def counted(self, t: Optional[datetime]) -> List[History]:
        return [self.hist[i] for i in sorted(self.hist) if self.is_counted(self.hist[i], t)]

    def kids(self, h: History, t: Optional[datetime]) -> List[History]:
        """Child items whose parent at `t` is `h`; none when counting tasks. With a type list,
        children that are counted themselves are left out, so no work is counted twice."""
        if self.count == "tasks":
            return []
        kids = (self.hist[c] for c in sorted(self.children.get(h.id, ())))
        found = [k for k in kids if (_row(k, t) or {}).get("parent") == h.id]
        if isinstance(self.count, list):
            found = [k for k in found if not self.is_counted(k, t)]
        return found

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
        row = h.at(t1)
        return bool(row and row["assigned"] and row["assigned"].lower() in emails)

    def ours(self, h: History, t0, t1, emails: Optional[set] = None) -> bool:
        """Ours: one of `emails` (default: the team's) was the assignee while the item was active
        up to t1; an item never active by t1 counts by its assignee at t1. At the stories
        level the same test on a child item also makes the item ours."""
        emails = self.emails if emails is None else emails
        return any(self._assigned(x, t0, t1, emails) for x in [h] + self.kids(h, t1))

    def _ours_at(self, h: History, t) -> bool:
        """The assignee of `h`, or of one of its children, at `t` is one of the team."""
        return any((r["assigned"] or "").lower() in self.emails
                   for x in [h] + self.kids(h, t) for r in [x.at(t)] if r is not None)

    @staticmethod
    def closed_in(h: History, path: str) -> bool:
        """Delivered while on the iteration `path` (credit goes to the iteration at close)."""
        return h.done_at is not None and h.at(h.done_at)["iteration"] == path

    def held_days(self, h: History, sprint: dict) -> int:
        """Mon-Fri local dates from the sprint's first day to its last day (inclusive) on which
        `h` was held (in a hold state, or tagged Blocked) at any moment of that date."""
        start, end = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["end"])
        spans = [(ts, h.times[i + 1] if i + 1 < len(h.times) else None)
                 for i, (r, ts) in enumerate(zip(h.rows, h.times))
                 if is_held(r, self.cfg.hold_states)]
        count, d = 0, start
        while d <= end:
            if d.weekday() < 5:
                lo, hi = day_start(d, self.tz), day_end(d, self.tz)
                if any(a < hi and (b is None or b > lo) for a, b in spans):
                    count += 1
            d += timedelta(days=1)
        return count

    def local(self, t) -> date:
        return t.astimezone(self.tz).date()

    def cycle_days(self, h: History) -> Optional[int]:
        fa, done = h.first_active, h.done_at
        if fa is None or done is None or fa > done:
            return None
        return (self.local(done) - self.local(fa)).days + 1

    def build_span(self, h: History) -> Optional[Tuple[datetime, datetime]]:
        """First active ownership to first test entry, capped at the delivered close."""
        done = h.done_at
        if done is None:
            return None
        times = set(h.times)
        for c in self.children.get(h.id, ()):
            child = self.hist[c]
            times.update(t for t in child.times if child in self.kids(h, t))
        start = next((t for t in sorted(times) if (h.at(t) or {}).get("category") in ACTIVE
                      and self._ours_at(h, t)), None)
        if start is None:
            return None
        # An entry at start (including creation in test) is an immediate handoff.
        handoff = next((t for i, (r, t) in enumerate(zip(h.rows, h.times))
                        if start <= t < done and r["state"] in self.cfg.test_states
                        and (not i or h.rows[i - 1]["state"] not in self.cfg.test_states)), done)
        return (start, handoff) if start < handoff else None

    def build_seconds(self, h: History) -> Optional[float]:
        """Build-span seconds excluding the union of waiting, hold and Blocked."""
        span = self.build_span(h)
        if span is None:
            return None
        start, end = span
        excluded = h.seconds_where(start, end, lambda r:
                                   is_waiting(r, self.cfg.waiting_states)
                                   or r["state"] in self.cfg.hold_states)
        return (end - start).total_seconds() - excluded

    def time_split(self, h: History) -> Optional[Tuple[float, float, float]]:
        """Building, waiting and remaining seconds from first active to delivered close."""
        if self.cycle_days(h) is None:
            return None
        building = self.build_seconds(h) or 0.0
        waiting = h.waiting_seconds(h.first_active, h.done_at, self.cfg.waiting_states)
        total = (h.done_at - h.first_active).total_seconds()
        return building, waiting, max(0.0, total - building - waiting)

    def waiting_states_seen(self, h: History) -> List[str]:
        """Configured waiting states visited during the cycle, in config order."""
        if self.cycle_days(h) is None:
            return []
        seen = {r["state"] for r, t in zip(h.rows, h.times) if h.first_active <= t < h.done_at}
        return [state for state in self.cfg.waiting_states if state in seen]

    def owner(self, h: History, at: datetime) -> str:
        """Current assignee, newest open child's assignee, then last ours assignee."""
        row = h.at(at)
        direct = self.names.get((row["assigned"] or "").lower()) if row else None
        if direct:
            return direct
        kids = self.kids(h, at)
        current = [(k.last_change(at), -k.id, self.names[(r["assigned"] or "").lower()])
                   for k in kids for r in [k.at(at)] if r["category"] not in CLOSED
                   and (r["assigned"] or "").lower() in self.names]
        if current:
            return max(current)[-1]
        past = [(ts, -x.id, self.names[(r["assigned"] or "").lower()])
                for x in [h] + kids for r, ts in zip(x.rows, x.times)
                if ts <= at and (r["assigned"] or "").lower() in self.names]
        return max(past)[-1] if past else ""

    def figures(self, sprint: dict, until: Optional[datetime] = None) -> Dict[str, list]:
        path = sprint["path"]
        s0, t0, t1 = self.instants(sprint)
        if until is not None:
            t0, t1 = min(t0, until), until
        f = {k: [] for k in ("planned", "delivered_planned", "added", "removed", "delivered",
                             "late", "reopened", "carry", "open")}
        for h in self.counted(t1):
            done, row0, row1 = h.done_at, h.at(t0), h.at(t1)
            on_path0 = row0 is not None and row0["iteration"] == path
            ours0 = on_path0 and self._ours_at(h, t0)
            open0 = on_path0 and row0["category"] not in CLOSED and not (done and done <= t0)
            planned = ours0 and open0
            # Ours at t0 is the commitment snapshot; on the sprint at t0 but not yet ours there
            # is added, even if it becomes ours (and is delivered) later.
            added = (not on_path0 and any(t0 < ts < t1 and r["iteration"] == path
                                          for r, ts in zip(h.rows, h.times))) \
                or (open0 and not ours0)
            delivered = self.closed_in(h, path)
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
            if delivered:
                f["delivered_planned"].append(h.id)
                if done >= t1:
                    f["late"].append(h.id)
                continue
            f["open"].append(h.id)
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
        items = [h.id for h in mine if h.id in delivered or self.active_during(h, s0, t1)]
        still_open = [h.id for h in mine if h.at(t1)["category"] not in CLOSED]
        held = [[i, self.held_days(self.hist[i], sprint)] for i in sorted(set(items) | set(still_open))]
        return {"name": p.name, "available": m["available"], "nominal": m["nominal"],
                "delivered": delivered,
                "in_progress": [h.id for h in mine if h.at(t1)["category"] in ACTIVE],
                "oldest": [-oldest[1], oldest[0]] if oldest else None,
                "median": percentile([c for c in (self.cycle_days(self.hist[i]) for i in delivered)
                                      if c is not None], 0.5),
                "items": items, "held": [x for x in held if x[1] > 0],
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
            if not is_waiting(r, self.cfg.waiting_states):
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
        ids = [h.id for h in counted if h.at(t1)["area"] in areas
               and self.closed_in(h, sprint["path"]) and not self.ours(h, t0, t1)]
        if not ids:
            return None
        cycles = [c for c in (self.cycle_days(self.hist[i]) for i in ids) if c is not None]
        def workers(h):  # engineers on the children (stories level), else the item's assignees
            kids = {a.lower() for k in self.kids(h, t1) for a in k.assignees_active(t1)}
            return kids or {a.lower() for a in h.assignees_active(t1)}

        return {"assignees": len({a for i in ids for a in workers(self.hist[i])}),
                "delivered": len(ids), "median": percentile(cycles, 0.5),
                "p85": percentile(cycles, P85), "waiting_share": self.waiting_share(ids)}


ASK = {"old": "Ask what is blocking it.",
       "held": "Ask what is blocking it.",
       "not_started": "Ask what is blocking it.",
       "agreed": "Confirm with the PO and record it in notes.md.",
       "waiting": "Raise it with the PO.",
       "stale": "Ask whether it is moving and the board is behind, or it is stuck.",
       "none": "Find out what they worked on; if it was off-board work, add it to notes.md.",
       "nothing": "Ask what is blocking them.",
       "wip": "Ask which item to finish first.",
       "reopened": "Ask why it was reopened.",
       "never_active": "Ask them to move items through Active on the board.",
       "shared": "Check that both teams should count it."}
_LEVELS = ("red", "amber", "grey", "green")
_BADGES = {"old": "Chase", "carry": "Carry-over", "agreed": "Carry-over",
           "waiting": "Waiting", "held": "On Hold", "stale": "No movement",
           "not_started": "Not started", "none": "No work", "nothing": "Nothing delivered",
           "wip": "Too much WIP", "never_active": "Check", "reopened": "Check", "shared": "Check"}


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _ids(ids) -> str:
    return ", ".join("#%d" % i for i in ids)


def _attention(t: _Team, f: dict, carry: List[dict], people: List[dict], waits: Dict[int, tuple],
               stale: Dict[int, tuple], p85: Optional[int],
               open_ages: Dict[int, int], shared: Dict[int, List[str]], at: datetime) -> List[dict]:
    """DM flags at sprint end, ordered red, amber, grey; then by item id, person name, text."""
    found = []

    def add(level, ask, text, item=None, name="", kind=None, badge=None):
        found.append((_LEVELS.index(level), item is None, item or 0, name, text,
                      {"level": level, "text": text, "ask": ASK[ask],
                       "id": item, "person": t.owner(t.hist[item], at) if item is not None else name,
                       "kind": kind or ask, "badge": badge or _BADGES[kind or ask]}))

    def old(age):
        return p85 is not None and age is not None and age > p85

    red_carry = set()
    for c in carry:
        if old(c["age"]) and c["sprints"] >= CARRIED_FLAG:
            red_carry.add(c["id"])
            add("red", "old", "#%d has been carried over for %d sprints and is %d days old, older "
                "than the squad's P85 of %d days." % (c["id"], c["sprints"], c["age"], p85),
                c["id"], kind="carry")
        elif c["sprints"] >= CARRIED_FLAG:
            add("amber", "old", "#%d has been carried over for %d sprints."
                % (c["id"], c["sprints"]), c["id"], kind="carry")
        if c["agreed"] != "agreed":
            if c["agreed"] == "not agreed":
                why = "and was not agreed with the PO"
            else:
                why = "and whether the PO agreed is unknown" if c["reason"] \
                    else "with no note in notes.md"
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
        expected_to_deliver = not p["onboarding"] and p["available"] > 0
        if expected_to_deliver and p["no_assignment"]:
            add("red", "none", "%s had %g available days but no item assigned during the sprint."
                % (p["name"], p["available"]), name=p["name"])
        elif expected_to_deliver and not p["delivered"]:
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

    def first_pass(ids):
        scored = len(ids)
        returned = sorted({i for i in ids if t.hist[i].returned(cfg.test_states)})
        return {"scored": scored, "returned": returned,
                "share": (scored - len(returned)) / scored if scored else None}

    passes = {path: first_pass(g["delivered"]) for path, g in figs.items()}
    carry = []
    for i in f["carry"]:
        h, row = t.hist[i], t.hist[i].at(t1)
        fa = h.first_active
        agreed, reason = notes.items.get(i, ("unknown", ""))
        carry.append({"id": i, "title": row["title"],
                      "age": (end - t.local(fa)).days + 1 if fa is not None and fa <= t1 else None,
                      "sprints": sum(1 for g in figs.values() if i in g["open"]),
                      "state": row["state"], "waiting": row["state"] in cfg.waiting_states,
                      "agreed": agreed, "reason": reason, "held": t.held_days(h, sprint)})
    groups = {}  # type: Dict[str, int]
    for i in sorted(set(f["delivered"]) | set(f["carry"])):
        title = t.parent_title(t.hist[i], t1)
        groups[title] = groups.get(title, 0) + 1
    window = upto[-TREND:]
    now, before = window[-NOW:], window[-2 * NOW:-NOW]
    window_ids = sorted({i for s in window for i in figs[s["path"]]["delivered"]})
    # With no return, test-state use is the evidence that the board records testing.
    tested = [i for i in window_ids if any(r["state"] in cfg.test_states for r in t.hist[i].rows)]
    cycle_window = _stats(cycles(window_ids))
    window_members = {s["path"]: t.members(people, team, sprints, s, days_off) for s in window}
    builds = {s["path"]: [seconds / 86400 for i in figs[s["path"]]["delivered"]
                          for seconds in [t.build_seconds(t.hist[i])] if seconds is not None]
              for s in window}

    def build(group):
        values = [v for s in group for v in builds[s["path"]]]
        return {"median": median(values) if values else None, "n": len(values)}

    def throughput(group):
        available = sum(m["available"] for s in group for m in window_members[s["path"]])
        delivered = sum(len(figs[s["path"]]["delivered"]) for s in group)
        return delivered / available * 10 if available else None

    def pooled_pass(group):
        return first_pass([i for s in group for i in figs[s["path"]]["delivered"]])

    flow = None
    splits = [split for i in window_ids for split in [t.time_split(t.hist[i])]
              if split is not None]
    if splits and cycle_window["median"] is not None:
        total = cycle_window["median"]
        seconds = sum(sum(split) for split in splits)
        # Parts are whole percentages of the pooled time; the remainder goes to other.
        building, waiting = [round(100 * sum(split[k] for split in splits) / seconds)
                             if seconds else 0 for k in (0, 1)]
        other = 100 - building - waiting
        if other < 0:
            waiting, other = waiting + other, 0
        seen = {state for i in window_ids for state in t.waiting_states_seen(t.hist[i])}
        flow = {"total": total, "building": building, "waiting": waiting, "other": other,
                "states": [state for state in cfg.waiting_states if state in seen]}
    counted = t.counted(t1)
    members = window_members[sprint["path"]]
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
        if is_waiting(row, cfg.waiting_states):
            what = "in %s" % row["state"] if row["state"] in cfg.waiting_states \
                else "with the Blocked tag"
            waits[h.id] = (what, _workdays_after(t.since(h, t1, True), end))
        elif row["state"] not in cfg.hold_states and row["category"] in ACTIVE:
            stale[h.id] = (row["state"], _workdays_after(t.since(h, t1, False), end))
    def title_at(i):
        return (t.hist[i].at(t1) or {}).get("title") or t.hist[i].title or ""

    worked = sorted({i for p in persons for i in p["items"]})
    worked.sort(key=lambda i: (i not in f["delivered"], i))
    return {
        "team": team, "count": ", ".join(t.count) if isinstance(t.count, list) else t.count,
        "manager_metrics": metrics_of(cfg, team), "show_flow": show_flow_of(cfg, team),
        "sprint": {k: sprint[k] for k in ("name", "path", "start", "end")},
        **{k: f[k] for k in ("planned", "delivered_planned", "added", "removed", "delivered",
                             "late", "reopened")},
        "cycle": _stats(cycles(f["delivered"])), "cycle_window": cycle_window,
        "windows": {"now": [s["name"] for s in now], "before": [s["name"] for s in before]},
        "build": {**build(now), "before": build(before)["median"]},
        "throughput": {"per10": throughput(now), "before": throughput(before)},
        "flow": flow,
        "first_pass": {**pooled_pass(now), "before": pooled_pass(before)["share"],
                       "tracked": any(passes[s["path"]]["returned"] for s in window)
                       or 0 < len(window_ids) <= 2 * len(tested)},
        "waiting_share": t.waiting_share(f["delivered"]),
        "carry": carry,
        "delivered_items": [{"id": i, "title": title_at(i), "planned": i in f["planned"],
                             "late": i in f["late"]}
                            for i in sorted(f["delivered"])],
        "worked_items": [{"id": i, "title": title_at(i), "delivered": i in f["delivered"]}
                         for i in worked],
        "worked_on": [[k, v] for k, v in sorted(groups.items(), key=lambda kv: (-kv[1], kv[0]))],
        "trend": [{"sprint": s["name"], "planned": len(figs[s["path"]]["planned"]),
                   "delivered_planned": len(figs[s["path"]]["delivered_planned"]),
                   "delivered": len(figs[s["path"]]["delivered"]),
                   "first_pass": passes[s["path"]]["share"],
                   "build": build([s])["median"], "per10": throughput([s])} for s in window],
        "capacity": {"engineers": len(members),
                     "onboarding": sum(m["onboarding"] for m in members),
                     "available": sum(m["available"] for m in members),
                     "nominal": sum(m["nominal"] for m in members)},
        "people": persons,
        "rest": t.rest(members, counted, sprint),
        "attention": _attention(t, f, carry, persons, waits, stale, cycle_window["p85"], open_ages,
                                shared, t1),
        "status": _status(f, carry, cycle_window["p85"], waits,
                          _by_team(notes.status, team, None)),
        "notes": {"summary": _by_team(notes.summary, team, ""), "note": _by_team(notes.note, team, ""),
                  "exec_note": _by_team(notes.exec_note, team, "")},
    }


def _running(t: _Team, sprint: dict, now: datetime, members: List[dict],
             days_off: dict, p85: Optional[int]) -> tuple:
    """Current figures and raw signals; `t` contains no revisions later than `now`."""
    start, end = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["end"])
    yesterday = t.local(now - timedelta(microseconds=1))
    day = _workdays_after(start - timedelta(days=1), yesterday)
    f = t.figures(sprint, now)
    current = {"sprint": dict(sprint), "day": day,
               "days": _workdays_after(start - timedelta(days=1), end),
               **{k: f[k] for k in ("planned", "delivered_planned", "added", "removed", "delivered")},
               "held": [], "waiting": []}
    ages, waits, stale, opened = {}, {}, {}, []
    for h in t.counted(now):
        row = h.at(now)
        if row["category"] in CLOSED or not t.ours(h, now, now):
            continue
        opened.append(h)
        age = t.open_age(h, now, yesterday)
        if age is not None:
            ages[h.id] = age
        if is_waiting(row, t.cfg.waiting_states):
            current["waiting"].append(h.id)
            what = "in %s" % row["state"] if row["state"] in t.cfg.waiting_states \
                else "with the Blocked tag"
            waits[h.id] = (what, _workdays_after(t.since(h, now, True), yesterday))
        elif row["state"] not in t.cfg.hold_states and any(
                x.at(now)["category"] in ACTIVE for x in [h] + t.kids(h, now)):
            stale[h.id] = (row["state"], _workdays_after(t.since(h, now, False), yesterday))
        if row["state"] in t.cfg.hold_states:
            current["held"].append(h.id)
    signals = _attention(t, f, [], [], waits, stale, p85, ages, {}, now)
    # Added work can also cross a sprint boundary; the closed commitment's carry list
    # deliberately contains planned work only, so it cannot identify every carried-in item.
    carried = set()
    for previous in t.sprints(now):
        if previous["end"] >= sprint["start"]:
            continue
        _, planned_at, boundary = t.instants(previous)
        for h in opened:
            row = h.at(boundary)
            onward = t.sprint_at.get(row["iteration"]) if row else None
            if row and row["category"] not in CLOSED and onward and \
                    onward["start"] >= previous["start"] and t.ours(h, planned_at, boundary) and any(
                        ts <= boundary and r["iteration"] == previous["path"]
                        for r, ts in zip(h.rows, h.times)):
                carried.add(h.id)
    for signal in signals:
        if (signal["kind"] == "old" and signal["id"] in carried) or (
                signal["kind"] == "waiting" and waits[signal["id"]][1] > WAIT_RED_DAYS):
            signal.update(level="red", badge="Stuck")

    def add(kind, text, h=None, name="", level="amber", badge=None):
        signals.append({"id": h.id if h else None, "person": t.owner(h, now) if h else name,
                        "kind": kind, "badge": badge or _BADGES[kind], "level": level,
                        "text": text, "ask": ASK[kind]})

    for h in opened:
        row = h.at(now)
        if h.id in current["held"]:
            since = None
            for r, ts in reversed(list(zip(h.rows, h.times))):
                if r["state"] not in t.cfg.hold_states:
                    break
                since = ts
            days = _workdays_after(t.local(since), yesterday)
            stuck = days > WAIT_RED_DAYS
            add("held", "#%d is On Hold for %d working days." % (h.id, days), h,
                level="red" if stuck else "amber", badge="Stuck" if stuck else None)
        if h.id in f["planned"] and day >= NOT_STARTED_DAY and all(
                x.at(now)["category"] == "Proposed" for x in [h] + t.kids(h, now)):
            add("not_started", "#%d is still Proposed after working day %d." % (h.id, day), h)
    for m in members:
        p = m["person"]
        emails = {e.lower() for e in p.emails if e}
        remaining = _working_days(p, max(start, t.local(now)), end)
        available = sum(1 - days_off.get(p.name, {}).get(d, 0) for d in remaining) * p.allocation
        assigned = any(r["category"] not in CLOSED and (r["assigned"] or "").lower() in emails
                       for h in t.hist.values() for r in [h.at(now)] if r is not None)
        if not m["onboarding"] and available > 0 and not assigned:
            add("none", "%s has %g available days left but no open item assigned." % (
                p.name, available), name=p.name, level="red")
        wip = [h.id for h in opened if t.owner(h, now) == p.name and any(
            x.at(now)["category"] in ACTIVE for x in [h] + t.kids(h, now))]
        if len(wip) > WIP_FLAG:
            add("wip", "%s has %s in progress: %s." % (
                p.name, _plural(len(wip), "item"), _ids(wip)), name=p.name)
    return current, signals, opened


def _action_problem(row: dict) -> str:
    """Compact the action view only; shared attention sentences stay unchanged."""
    fragments = []
    subject = '#%d ' % row['id'] if row['id'] is not None else row['person'] + ' '
    for sentence in row['problem'].split('; '):
        text = sentence.removeprefix(subject).removesuffix('.')
        if text.startswith('was carried over '):
            agreement = {'with no note in notes.md': 'no PO agreement recorded',
                         'and whether the PO agreed is unknown': 'PO agreement unknown',
                         'and was not agreed with the PO': 'not agreed with the PO'}
            detail = text.removeprefix('was carried over ')
            text = agreement.get(detail, detail)
            if 'carry' not in row['kinds']:
                text = 'carried over, ' + text
            fragments.append((3, text))
            continue
        if row['id'] is None and row['kinds'] in (['none'], ['nothing']):
            available = text.split(' available days', 1)[0].split(' ', 1)[1]
            if row['kinds'] == ['nothing']:
                text = 'delivered nothing, %s available days' % available
            elif ' available days left ' in text:
                text = 'no open item, %s available days left' % available
            else:
                text = 'no item assigned during the sprint, %s available days' % available
        text = text.replace('has been carried over for ', 'carried over ').replace(
            ' sprints and is ', ' sprints; ').replace(
            ", older than the squad's P85 of ", ', squad P85 ')
        text = re.sub(r'^(?:has |is |was )', '', text)
        text = re.sub(r' for (\d+) working days$', r' \1 working days', text)
        text = re.sub(r'(?<![\d.])1 ((?:working |available )?days|sprints|items)\b',
                      lambda m: '1 ' + m[1][:-1], text)
        for fragment in filter(None, text.split('; ')):
            rank = 1 if fragment.startswith('carried over ') else 2 if re.match(
                r'\d+ days? old,', fragment) else 0
            if (rank, fragment) not in fragments:
                fragments.append((rank, fragment))
    return '; '.join(text for _, text in sorted(fragments, key=lambda f: f[0]))


def team_actions(data: dict, cfg: Config, people: List[Person], team: str,
                 closed: Optional[dict], running: Optional[dict], today: date,
                 days_off: dict) -> dict:
    """Raw DM actions, separate from closed figures and from answer reconciliation."""
    now = day_start(today, local_tz(cfg.timezone))
    snapshot = data
    if running:
        snapshot = dict(data, revisions=[r for r in data["revisions"]
                                        if parse_ts(r["changed"]) <= now])
    t = _Team(snapshot, cfg, people, team)
    current, signals, opened = None, [], []
    sprint = running or (closed["sprint"] if closed else None)
    if sprint is None:
        return {"people": [], "rows": [], "current": None}
    at = now if running else t.instants(sprint)[-1]
    p85 = closed["cycle_window"]["p85"] if closed else None
    if running:
        members = t.members(people, team, t.sprints(now), sprint, days_off)
        persons = [{"name": m["person"].name, "available": m["available"],
                    "nominal": m["nominal"], "onboarding": m["onboarding"]} for m in members]
        current, signals, opened = _running(t, sprint, now, members, days_off, p85)
    else:
        persons = [dict(p) for p in closed["people"]]
    carry = {c["id"]: c for c in closed["carry"]} if closed else {}
    rows = {}

    def row_for(i, name, source):
        key = i if i is not None else (name, source["kind"])
        if key not in rows:
            h = t.hist.get(i)
            row = h.at(at) if h else None
            is_open = bool(row and row["category"] not in CLOSED)
            rows[key] = {"id": i, "person": name, "title": (row["title"] or "") if row else "",
                         "sprint": "Now" if running else sprint["name"],
                         "sprint_path": sprint["path"], "level": "green", "badge": "On track" if is_open else "Done",
                         "kinds": [], "problem": "", "ask": "",
                         "age": t.open_age(h, at, t.local(at - timedelta(microseconds=1)))
                         if is_open else None, "open": is_open, "carry": i in carry}
        return rows[key]

    def merge(signal):
        i = signal["id"]
        name = t.owner(t.hist[i], at) if i in t.hist and t.hist[i].at(at) else signal["person"]
        row = row_for(i, name, signal)
        if _LEVELS.index(signal["level"]) < _LEVELS.index(row["level"]):
            row.update(level=signal["level"], badge=signal["badge"])
        row["kinds"] = sorted(set(row["kinds"] + [signal["kind"]]))
        for key, value in (("problem", signal["text"]), ("ask", signal["ask"])):
            separator = "; " if key == "problem" else "\n"
            if value and value not in row[key].split(separator):
                row[key] = separator.join(filter(None, [row[key], value]))
        return row

    # Severity ties use a fixed badge order, with item ids providing the final row order.
    priority = ("Stuck", "Carry-over", "Waiting", "On Hold", "Chase", "No movement",
                "Not started", "No work", "Nothing delivered", "Too much WIP", "Check")
    open_ids = {h.id for h in opened}
    if closed:
        eligible = {i for i, c in carry.items() if c["agreed"] != "agreed" or (
            p85 is not None and c["age"] is not None and c["age"] > p85)}
        for signal in closed["attention"]:
            if not running or (signal["id"] in eligible and
                               signal["kind"] in ("carry", "agreed", "old")):
                value = dict(signal)
                if running and signal["id"] in open_ids and signal["kind"] in ("old", "carry"):
                    i = signal["id"]
                    age = t.open_age(t.hist[i], at, t.local(at - timedelta(microseconds=1)))
                    previous = carry[i]["age"]
                    if previous is not None and age is not None:
                        value["text"] = value["text"].replace("%d days old" % previous, "%d days old" % age)
                signals.append(value)
    signals.sort(key=lambda s: (_LEVELS.index(s["level"]), priority.index(s["badge"])))
    for signal in signals:
        row = merge(signal)
        if running and signal["id"] in carry and signal["id"] not in open_ids:
            row.update(sprint=closed["sprint"]["name"], sprint_path=closed["sprint"]["path"])
    for h in opened:
        row_for(h.id, t.owner(h, at), {})
    for i in current["delivered"] if current else []:  # Done rows: delivered in the running sprint
        row_for(i, t.owner(t.hist[i], at), {})
    for row in rows.values():
        row["problem"] = _action_problem(row)
        row["ask"] = row["ask"].replace("\n", " ")
    # History-only ownership can refer to a roster member who left before this sprint.
    known = {p["name"] for p in persons}
    for name in sorted({r["person"] for r in rows.values()} - known):
        persons.append({"name": name, "available": 0, "nominal": 0, "onboarding": False})
    worst = {p["name"]: min((_LEVELS.index(r["level"]) for r in rows.values()
                            if r["person"] == p["name"]), default=_LEVELS.index("green")) for p in persons}
    persons.sort(key=lambda p: (worst[p["name"]], p["name"]))
    order = {p["name"]: n for n, p in enumerate(persons)}
    ordered = sorted(rows.values(), key=lambda r: (order[r["person"]], _LEVELS.index(r["level"]),
                                                  not r["open"], r["id"] is None, r["id"] or 0, r["kinds"]))
    return {"people": persons, "rows": ordered, "current": current}
