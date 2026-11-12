"""Folds a ticket's revisions into intervals and phase-change events."""
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from typing import (Callable, Dict, Iterable, List, Optional, Set, Tuple)

from .model import Phase, Revision, Ticket
from .phases import PhaseMapper


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime
    phase: Phase
    wait_label: Optional[str]
    state: str
    holder: Optional[str]
    iteration: str
    area: str
    not_delivered: bool


@dataclass(frozen=True)
class Event:
    at: datetime
    rev: int
    by: Optional[str]
    from_phase: Optional[Phase]
    to_phase: Phase
    admin: bool
    backward: bool
    is_return: bool
    reopen: bool
    to_handoff: bool
    holder_before: Optional[str]


@dataclass
class Timeline:
    ticket_id: int
    intervals: List[Interval]
    events: List[Event]
    revisions: List[Revision]

    def at(self, ts: datetime) -> Optional[Interval]:
        for i in self.intervals:
            if i.start <= ts < i.end:
                return i
        return None

    def phase_at(self, ts: datetime) -> Optional[Phase]:
        i = self.at(ts)
        return i.phase if i is not None else None

    def clip(self, start: datetime, end: datetime) -> List[Interval]:
        out = []
        for i in self.intervals:
            s, e = max(i.start, start), min(i.end, end)
            if s < e:
                out.append(replace(i, start=s, end=e))
        return out

    def first(self, pred: Callable[[Interval], bool]) -> Optional[Interval]:
        for i in self.intervals:
            if pred(i):
                return i
        return None


def find_bulk_edits(tickets: Iterable[Ticket], count: int,
                    seconds: int) -> Set[Tuple[int, int]]:
    """(ticket id, rev) of every revision inside a window of `seconds`
    (inclusive) in which one actor changed at least `count` distinct
    tickets. First revisions and revisions with no actor are ignored."""
    by_actor: Dict[str, List[Tuple[datetime, int, int]]] = defaultdict(list)
    for t in tickets:
        for r in t.revisions:
            if r.rev > 1 and r.by is not None:
                by_actor[r.by].append((r.changed, t.id, r.rev))
    out: Set[Tuple[int, int]] = set()
    for edits in by_actor.values():
        edits.sort()
        j = 0
        for i in range(len(edits)):
            # every qualifying window is covered by the maximal window
            # that starts at its earliest edit
            j = max(j, i)
            while (j + 1 < len(edits) and (edits[j + 1][0] - edits[i][0])
                   .total_seconds() <= seconds):
                j += 1
            window = edits[i:j + 1]
            if len({tid for _, tid, _ in window}) >= count:
                out.update((tid, rev) for _, tid, rev in window)
    return out


def _is_admin(ticket_id: int, rev: Revision, prev: Optional[Revision],
              bulk: Set[Tuple[int, int]]) -> bool:
    if (ticket_id, rev.rev) in bulk:
        return True
    if rev.by is not None and rev.by.startswith("s:"):
        return True
    return prev is not None and (rev.project != prev.project
                                 or rev.type != prev.type)


def build_timeline(ticket: Ticket, mapper, cutoff: datetime,
                   bulk: Set[Tuple[int, int]]) -> Timeline:
    """Revisions at or after `cutoff` are ignored (Timeline.revisions holds
    only those before it); the last interval ends at `cutoff`. Revisions
    sharing a timestamp are applied in rev order."""
    revs = sorted((r for r in ticket.revisions if r.changed < cutoff),
                  key=lambda r: (r.changed, r.rev))
    intervals: List[Interval] = []
    events: List[Event] = []
    cur: Optional[Interval] = None
    prev: Optional[Revision] = None
    per_area = getattr(mapper, "for_area", None)
    for r in revs:
        m = per_area(r.area) if per_area is not None else mapper
        phase, wait = m.map(r.state, r.column, r.column_done)
        nd = m.not_delivered(r.state, r.reason)
        if cur is None or phase != cur.phase:
            from_phase = cur.phase if cur is not None else None
            admin = _is_admin(ticket.id, r, prev, bulk)
            removal = Phase.REMOVED in (phase, from_phase)
            backward = (from_phase is not None and not admin
                        and not removal and phase < from_phase)
            is_return = backward and from_phase in (Phase.HANDOFF,
                                                    Phase.DONE)
            events.append(Event(
                at=r.changed, rev=r.rev, by=r.by, from_phase=from_phase,
                to_phase=phase, admin=admin, backward=backward,
                is_return=is_return,
                reopen=is_return and from_phase == Phase.DONE,
                to_handoff=(phase == Phase.HANDOFF
                            and from_phase != Phase.HANDOFF),
                holder_before=cur.holder if cur is not None else None))
        # not_delivered only matters in DONE, so only there does it split
        if cur is None or (phase, wait, r.state, r.assignee, r.iteration,
                           r.area) != (cur.phase, cur.wait_label, cur.state,
                                       cur.holder, cur.iteration, cur.area) \
                or (phase == Phase.DONE and nd != cur.not_delivered):
            if cur is not None:
                cur = replace(cur, end=r.changed)
                if cur.start < cur.end:
                    intervals.append(cur)
            cur = Interval(start=r.changed, end=cutoff, phase=phase,
                           wait_label=wait, state=r.state, holder=r.assignee,
                           iteration=r.iteration, area=r.area,
                           not_delivered=nd)
        prev = r
    if cur is not None and cur.start < cur.end:
        intervals.append(cur)
    return Timeline(ticket_id=ticket.id, intervals=intervals, events=events,
                    revisions=revs)
