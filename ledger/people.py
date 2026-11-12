"""Per-person, per-sprint observations for the delivery manager's report.

Every figure is measured against the days the person was actually
available to the team, so leave, part-time allocation and moving between
teams never read as missing delivery."""
import statistics
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set

from .model import Phase, Sprint
from .timeline import Timeline
from .workcal import WorkCalendar


def _sprint_dates(sprint: Sprint, cal: WorkCalendar) -> List[date]:
    return cal.working_dates(
        cal.local_date(sprint.start),
        cal.local_date(sprint.end - timedelta(microseconds=1)))


def _allocation(roster, pid: str, team: str, d: date) -> float:
    a = roster.assignment_on(pid, team, d)
    return a.allocation if a is not None else 0.0


def _held(timelines: Dict[int, Timeline], actor: str, phase: Phase,
          d: date, cal: WorkCalendar) -> Set[int]:
    lo, hi = cal.day_start(d), cal.day_start(d + timedelta(days=1))
    return {tid for tid, tl in timelines.items()
            if any(i.holder == actor and i.phase == phase
                   and i.start < hi and lo < i.end for i in tl.intervals)}


def _pickups(timelines: Dict[int, Timeline], actor: str, sprint: Sprint,
             cal: WorkCalendar) -> List[float]:
    samples = []
    for tl in timelines.values():
        ivs = tl.intervals
        for k, iv in enumerate(ivs):
            prev = ivs[k - 1] if k > 0 else None
            if (iv.holder != actor or iv.phase != Phase.TODO
                    or (prev is not None and prev.holder == actor)
                    or not sprint.period_start <= iv.start < sprint.end):
                continue
            for nxt in ivs[k:]:
                if nxt.holder != actor:
                    break
                if nxt.phase == Phase.DOING:
                    samples.append(cal.working_days(iv.start, nxt.start))
                    break
    return samples


def _first_delivery(timelines: Dict[int, Timeline], actor: str,
                    before: datetime) -> Optional[datetime]:
    best = None
    for tl in timelines.values():
        worked = None
        for iv in tl.intervals:
            if iv.start >= before:
                break
            if iv.holder == actor and iv.phase == Phase.DOING \
                    and worked is None:
                worked = iv.start
        if worked is None:
            continue
        for ev in tl.events:
            if (ev.to_phase == Phase.DONE and ev.from_phase != Phase.DONE
                    and worked < ev.at < before):
                if best is None or ev.at < best:
                    best = ev.at
                break
    return best


def person_observations(pid: str, team: str, sprint: Sprint,
                        timelines: Dict[int, Timeline], cal: WorkCalendar,
                        roster, cfg, team_sprints: Sequence[Sprint]
                        ) -> Dict[str, Any]:
    actor = "p:" + pid
    person = roster.person(pid)
    dates = _sprint_dates(sprint, cal)

    def absent(d: date) -> bool:
        return any(a.start <= d <= a.end for a in person.absences)

    nominal = available = nothing = 0.0
    todo_max = conc_max = 0
    conc: List[int] = []
    allocs: List[float] = []
    for d in dates:
        alloc = _allocation(roster, pid, team, d)
        nominal += alloc
        if alloc > 0:
            allocs.append(alloc)
        if alloc <= 0 or absent(d):
            continue
        available += alloc
        doing = len(_held(timelines, actor, Phase.DOING, d, cal))
        conc.append(doing)
        conc_max = max(conc_max, doing)
        if doing == 0:
            nothing += alloc
            todo_max = max(todo_max,
                           len(_held(timelines, actor, Phase.TODO, d, cal)))

    pickups = _pickups(timelines, actor, sprint, cal)

    change_dates = set()
    last = None
    for tl in timelines.values():
        for r in tl.revisions:
            if r.by != actor or r.changed >= sprint.end:
                continue
            if last is None or r.changed > last:
                last = r.changed
            if r.changed >= sprint.period_start:
                change_dates.add(cal.local_date(r.changed))

    idx = list(team_sprints).index(sprint)
    window = int(cfg.thresholds["window_sprints"])
    first_in_window = team_sprints[max(0, idx + 1 - window)]
    first_start = min(a.start for a in person.assignments)
    is_joiner = first_start >= cal.local_date(first_in_window.start)
    joiner_days = None
    if is_joiner:
        delivered = _first_delivery(timelines, actor, sprint.end)
        if delivered is not None:
            joiner_days = cal.working_days(cal.day_start(first_start),
                                           delivered)

    def footing(s: Sprint):
        ds = _sprint_dates(s, cal)
        if not ds:
            return None
        a = roster.assignment_on(pid, team, ds[0])
        return a.allocation if a is not None else None

    brk = idx > 0 and footing(sprint) != footing(team_sprints[idx - 1])

    return {
        "available_days": available,
        "nominal_days": nominal,
        # the share of the person's time on this team on their assigned
        # days (the mean, should it change inside the sprint)
        "allocation": round(sum(allocs) / len(allocs), 2) if allocs
        else None,
        "days_nothing_in_progress": nothing,
        "todo_assigned_max": todo_max,
        "concurrency_avg": sum(conc) / len(conc) if conc else 0.0,
        "concurrency_max": conc_max,
        "pickup_median": statistics.median(pickups) if pickups else None,
        "board_change_days": len(change_dates),
        "last_board_change": last and cal.local_date(last).isoformat(),
        "is_joiner": is_joiner,
        "joiner_days_to_first_delivery": joiner_days,
        "comparison_break": bool(brk),
    }
