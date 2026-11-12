"""Working-day calendar: all duration arithmetic in the tool goes through here."""
import math
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, List, Tuple
from zoneinfo import ZoneInfo

from ledger.errors import LedgerError

_UTC = timezone.utc


def _require_aware(ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime: timezone-aware datetime required")


def display_days(x: float) -> float:
    """Round to the nearest 0.5, halves rounding up."""
    return math.floor(x * 2 + 0.5) / 2


class WorkCalendar:
    def __init__(self, tz_name: str, holidays: Iterable[date]):
        try:
            self.tz = ZoneInfo(tz_name)
        except (KeyError, ValueError, OSError) as exc:  # ZoneInfoNotFoundError is a KeyError
            raise LedgerError(
                "Unknown time zone '%s'." % tz_name,
                "Use an IANA name such as Europe/Zurich. If the system has no time zone "
                "data, the tzdata package is needed, but nothing may be installed here, "
                "so ask for the zone data to be provided.",
            ) from exc
        self.holidays = frozenset(holidays)

    def is_working(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def day_start(self, d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=self.tz).astimezone(_UTC)

    def local_date(self, ts: datetime) -> date:
        _require_aware(ts)
        return ts.astimezone(self.tz).date()

    def working_days(self, start: datetime, end: datetime) -> float:
        _require_aware(start)
        _require_aware(end)
        if end <= start:
            return 0.0
        total = 0.0
        d = self.local_date(start)
        last = self.local_date(end)
        while d <= last:
            if self.is_working(d):
                lo = self.day_start(d)
                hi = self.day_start(d + timedelta(days=1))
                overlap = (min(end, hi) - max(start, lo)).total_seconds()
                if overlap > 0:
                    total += overlap / (hi - lo).total_seconds()
            d += timedelta(days=1)
        return total

    def working_dates(self, first: date, last: date) -> List[date]:
        out = []
        d = first
        while d <= last:
            if self.is_working(d):
                out.append(d)
            d += timedelta(days=1)
        return out

    def add_working_days(self, d: date, n: int) -> date:
        if n < 0:
            raise ValueError("n must be >= 0")
        while n > 0:
            d += timedelta(days=1)
            if self.is_working(d):
                n -= 1
        return d

    def sprint_bounds(self, start_date: date, finish_date: date) -> Tuple[datetime, datetime]:
        return self.day_start(start_date), self.day_start(finish_date + timedelta(days=1))
