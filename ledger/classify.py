"""Per-ticket, per-sprint classification: membership, outcome, involvement,
carryover age and who worked on it."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from .config import Roster
from .model import Phase, Sprint
from .timeline import Interval, Timeline
from .workcal import WorkCalendar

OUTCOMES = ("delivered", "closed_not_delivered", "reclosed",
            "awaiting_acceptance", "carried_in_progress",
            "carried_not_started", "active_outside_sprint",
            "assigned_outside_sprint")

_TICK = timedelta(microseconds=1)


def _last_instant(sprint: Sprint) -> datetime:
    """The last instant inside the half-open sprint [start, end)."""
    return sprint.end - _TICK


def _local_dates(start: datetime, end: datetime, tz: ZoneInfo) -> List[date]:
    """Local calendar dates touched by the half-open span [start, end)."""
    if end <= start:
        return []
    d, last = start.astimezone(tz).date(), (end - _TICK).astimezone(tz).date()
    out = []
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


def _noon(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(12), tzinfo=tz)


def _vendor_held(i: Interval, start: datetime, end: datetime, team: str,
                 roster: Roster, cal: WorkCalendar) -> bool:
    """True when the holder of i is on team's roster on some local working
    day (weekends and configured holidays excluded) that the part of i
    inside [start, end) covers."""
    s, e = max(i.start, start), min(i.end, end)
    return any(cal.is_working(d)
               and roster.vendor_on(i.holder, team, _noon(d, cal.tz), cal.tz)
               for d in _local_dates(s, e, cal.tz))


def held_by_vendor_at_end(tl: Timeline, sprint: Sprint, team: str,
                          roster: Roster, cal: WorkCalendar) -> bool:
    """True when, at the sprint's last instant, the ticket's holder is a
    roster person on team on that local date (working day or not)."""
    at = _last_instant(sprint)
    i = tl.at(at)
    return i is not None and roster.vendor_on(i.holder, team, at, cal.tz)


def is_member(tl: Timeline, sprint: Sprint) -> bool:
    return any(i.iteration == sprint.path
               for i in tl.clip(sprint.start, sprint.end))


def is_planned(tl: Timeline, sprint: Sprint, cal: WorkCalendar,
               grace_days: int) -> bool:
    """A member before the end of the grace_days-th working day of the sprint
    (the first working day is day 1)."""
    d = cal.local_date(sprint.start)
    while not cal.is_working(d):
        d += timedelta(days=1)
    d = cal.add_working_days(d, max(grace_days, 1) - 1)
    cut = min(cal.day_start(d + timedelta(days=1)), sprint.end)
    return any(i.iteration == sprint.path for i in tl.clip(sprint.start, cut))


def left_sprint(tl: Timeline, sprint: Sprint,
                team_sprints: Sequence[Sprint]) -> bool:
    """A member that, at the sprint's last instant, is neither in this nor a
    later sprint of the team, and is not done."""
    if not is_member(tl, sprint):
        return False
    last = tl.at(_last_instant(sprint))
    if last is None or last.phase == Phase.DONE:
        return False
    later = {s.path for s in team_sprints if s.start > sprint.start}
    return last.iteration != sprint.path and last.iteration not in later


def involvement(tl: Timeline, sprint: Sprint, team: str, roster: Roster,
                cal: WorkCalendar, min_days: float = 0.0) -> Optional[str]:
    """"worked", "assigned" or None. Callers must also check outcome(): a
    ticket whose outcome is None is not in play, whatever this returns.

    "worked" needs the vendor's recorded DOING hold before the sprint's end
    (all roster people on team together, working days) to reach min_days.
    A shorter hold inside the sprint's period counts as "assigned"."""
    if tl.at(_last_instant(sprint)) is None:
        return None
    held = any(i.phase == Phase.DOING and i.start < sprint.end
               and _vendor_held(i, i.start, sprint.end, team, roster, cal)
               for i in tl.intervals)
    if held and (min_days <= 0 or sum(workers(
            tl, sprint.end, team, roster, cal).values()) >= min_days):
        return "worked"
    for i in tl.clip(sprint.period_start, sprint.end):
        if i.phase in (Phase.TODO, Phase.DOING) and _vendor_held(
                i, i.start, i.end, team, roster, cal):
            return "assigned"
    return None


def _done_at_end(tl: Timeline, sprint: Sprint) -> bool:
    return tl.phase_at(_last_instant(sprint)) == Phase.DONE


def outcome(tl: Timeline, sprint: Sprint,
            earlier: Sequence[Sprint]) -> Optional[str]:
    """The ticket's single outcome for the sprint, from its phase at the
    sprint's last instant. earlier: the same team's sprints before this one."""
    last = tl.at(_last_instant(sprint))
    if last is None or last.phase == Phase.REMOVED:
        return None
    if last.phase == Phase.DONE:
        changed = any(sprint.period_start <= e.at < sprint.end
                      for e in tl.events)
        if tl.phase_at(sprint.period_start) == Phase.DONE and not changed:
            return None
        if last.not_delivered:
            return "closed_not_delivered"
        if any(_done_at_end(tl, s) and not tl.at(_last_instant(s)).not_delivered
               for s in earlier):
            return "reclosed"
        return "delivered"
    if last.phase == Phase.HANDOFF:
        return "awaiting_acceptance"
    member = is_member(tl, sprint)
    if last.phase == Phase.DOING:
        return "carried_in_progress" if member else "active_outside_sprint"
    return "carried_not_started" if member else "assigned_outside_sprint"


@dataclass
class CarryAge:
    sprints_carried: int
    carried_since: Optional[str]
    trail: List[str] = field(default_factory=list)
    sprints_waiting: int = 0


def carry_age(tl: Timeline, sprints_upto: Sequence[Sprint], team: str,
              roster: Roster, cal: WorkCalendar) -> CarryAge:
    """Counts from the first sprint (oldest first) in which a roster person
    on team held the ticket, in any phase, through the last sprint given.
    A sprint adds to sprints_carried only when its outcome is carried_* and
    a roster person on team holds the ticket at the sprint's end;
    carried_since is the first such sprint. sprints_waiting counts
    awaiting_acceptance whoever holds the ticket."""
    first = None
    for idx, s in enumerate(sprints_upto):
        if any(_vendor_held(i, i.start, i.end, team, roster, cal)
               for i in tl.clip(s.period_start, s.end)):
            first = idx
            break
    if first is None:
        return CarryAge(0, None, [], 0)
    age = CarryAge(0, None, [], 0)
    for idx in range(first, len(sprints_upto)):
        s = sprints_upto[idx]
        o = outcome(tl, s, sprints_upto[:idx])
        if o in ("carried_in_progress", "carried_not_started") and \
                held_by_vendor_at_end(tl, s, team, roster, cal):
            age.sprints_carried += 1
            if age.carried_since is None:
                age.carried_since = s.name
        elif o == "awaiting_acceptance":
            age.sprints_waiting += 1
        if is_member(tl, s):
            age.trail.append(s.name)
    return age


def workers(tl: Timeline, upto: datetime, team: str, roster: Roster,
            cal: WorkCalendar) -> Dict[str, float]:
    """Roster person id -> working days holding the ticket in DOING while on
    team's roster, before upto."""
    out: Dict[str, float] = {}
    for i in tl.intervals:
        if i.phase != Phase.DOING or not i.holder \
                or not i.holder.startswith("p:") or i.start >= upto:
            continue
        end = min(i.end, upto)
        for d in _local_dates(i.start, end, cal.tz):
            if not roster.vendor_on(i.holder, team, _noon(d, cal.tz), cal.tz):
                continue
            lo = max(i.start, cal.day_start(d))
            hi = min(end, cal.day_start(d + timedelta(days=1)))
            days = cal.working_days(lo, hi)
            if days > 0:
                out[i.holder[2:]] = out.get(i.holder[2:], 0.0) + days
    return out
