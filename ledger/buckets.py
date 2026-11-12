"""Where a ticket's time went: with a vendor engineer, with another party,
unassigned, waiting on a hand-off, or parked."""
from datetime import date, datetime, time, timedelta
from typing import Dict, Iterator, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .config import Roster
from .model import Phase
from .timeline import Interval, Timeline
from .workcal import WorkCalendar

_TICK = timedelta(microseconds=1)


def local_day_pieces(start: datetime, end: datetime, tz: ZoneInfo
                     ) -> List[Tuple[date, datetime, datetime]]:
    """[start, end) cut at local midnights: (local date, piece start, piece
    end) for every local date the span touches."""
    out: List[Tuple[date, datetime, datetime]] = []
    if end <= start:
        return out
    d, last = start.astimezone(tz).date(), (end - _TICK).astimezone(tz).date()
    while d <= last:
        lo = datetime.combine(d, time(0), tzinfo=tz)
        hi = datetime.combine(d + timedelta(days=1), time(0), tzinfo=tz)
        out.append((d, max(start, lo), min(end, hi)))
        d += timedelta(days=1)
    return out


def _vendor_on_date(holder: Optional[str], team: str, d: date,
                    roster: Roster, tz: ZoneInfo) -> bool:
    return roster.vendor_on(holder, team,
                            datetime.combine(d, time(12), tzinfo=tz), tz)


def first_vendor_hold(tl: Timeline, team: str, roster: Roster,
                      tz: ZoneInfo) -> Optional[datetime]:
    """The first instant, in a phase where work can be done or is being
    waited on, at which the holder is a roster person on team on that
    local date. Being assigned to a ticket that is done or removed is not
    receiving it."""
    for i in tl.intervals:
        if i.phase in (Phase.DONE, Phase.REMOVED):
            continue
        for d, lo, _ in local_day_pieces(i.start, i.end, tz):
            if _vendor_on_date(i.holder, team, d, roster, tz):
                return lo
    return None


def _bucket(i: Interval, d: date, doing_before: bool, team: str,
            roster: Roster, tz: ZoneInfo) -> Optional[str]:
    if i.phase == Phase.DOING:
        if i.holder is None:
            return "unassigned"
        if _vendor_on_date(i.holder, team, d, roster, tz):
            return "vendor"
        return "other_party"
    if i.phase == Phase.HANDOFF:
        return "waiting:" + (i.wait_label or "other")
    if i.phase == Phase.TODO and doing_before:
        return "parked"
    return None


def _counted(tl: Timeline, start: datetime, end: datetime,
             cal: WorkCalendar, team: str, roster: Roster
             ) -> Iterator[Tuple[str, float]]:
    """(bucket, working days) for every counted local-day piece."""
    first = first_vendor_hold(tl, team, roster, cal.tz)
    if first is None:
        return
    lo = max(start, first)
    doing_before = False
    for i in tl.intervals:
        if i.start < end and i.end > lo:
            for d, s, e in local_day_pieces(max(i.start, lo),
                                            min(i.end, end), cal.tz):
                b = _bucket(i, d, doing_before, team, roster, cal.tz)
                if b is not None:
                    yield b, cal.working_days(s, e)
        if i.phase == Phase.DOING:
            doing_before = True


def time_split(tl: Timeline, start: datetime, end: datetime,
               cal: WorkCalendar, team: str, roster: Roster
               ) -> Dict[str, float]:
    """Raw fractional working days per bucket inside [start, end), from the
    first vendor hold on. Only buckets above zero are returned."""
    out: Dict[str, float] = {}
    for b, days in _counted(tl, start, end, cal, team, roster):
        if days > 0:
            out[b] = out.get(b, 0.0) + days
    return out


def classified_days(tl: Timeline, start: datetime, end: datetime,
                    cal: WorkCalendar, team: str, roster: Roster) -> float:
    """Working days in counted phases; equals the sum of time_split."""
    return sum(days for _, days in _counted(tl, start, end, cal, team,
                                            roster))
