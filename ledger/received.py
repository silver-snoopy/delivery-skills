"""The state of the work when a vendor engineer first received it."""
from datetime import timedelta
from typing import Any, Dict

from .buckets import first_vendor_hold
from .classify import is_member, is_planned
from .config import Config, Roster
from .model import Sprint, Ticket
from .timeline import Timeline
from .workcal import WorkCalendar

_TICK = timedelta(microseconds=1)


def received_state(ticket: Ticket, tl: Timeline, sprint: Sprint,
                   cal: WorkCalendar, team: str, roster: Roster,
                   cfg: Config) -> Dict[str, Any]:
    received = first_vendor_hold(tl, team, roster, cal.tz)
    if received is None:
        return {"received_at": None, "no_criteria": False,
                "text_changes_after": 0, "added_mid_sprint": False,
                "assigned_late": False}
    th = cfg.thresholds
    revs = sorted(tl.revisions, key=lambda r: (r.changed, r.rev))
    at = None
    for r in revs:
        if r.changed <= received:
            at = r
    no_criteria = (at is not None and at.ac_len == 0
                   and at.desc_len < th["no_criteria_desc_len"])
    changes = sum(
        1 for prev, r in zip(revs, revs[1:])
        if received < r.changed < sprint.end
        and (r.desc_hash != prev.desc_hash or r.ac_hash != prev.ac_hash)
        and not (r.by or "").startswith("s:"))
    late = False
    if sprint.start <= received < sprint.end:
        left = cal.working_dates(cal.local_date(received),
                                 cal.local_date(sprint.end - _TICK))
        late = len(left) <= th["assigned_late_days"]
    return {
        "received_at": received.isoformat(),
        "no_criteria": no_criteria,
        "text_changes_after": changes,
        "added_mid_sprint": is_member(tl, sprint) and not is_planned(
            tl, sprint, cal, int(th["planned_grace_days"])),
        "assigned_late": late,
    }
