"""Replay a work item's revision rows: when it became active, was done, waited or moved."""
import re
import warnings
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Dict, List, Optional, Sequence, Set

ACTIVE = ("InProgress", "Resolved")
_TS = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?$")


def local_tz(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # missing tz data, unknown name or no zoneinfo module
        warnings.warn("time zone %r is not available; using UTC" % name)
        return timezone.utc


def parse_ts(text: str) -> datetime:
    """ISO timestamp (Z, offset or none; any fraction length) as an aware UTC datetime."""
    m = _TS.match(text.strip())
    if not m:
        raise ValueError("bad timestamp: %r" % text)
    y, mo, d, h, mi, s, frac, zone = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro, tzinfo=timezone.utc)
    if zone and zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        digits = zone[1:].replace(":", "")
        dt -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:]))
    return dt


def day_start(d: date, tz: tzinfo) -> datetime:
    """UTC instant of 00:00 local on `d`."""
    return datetime(d.year, d.month, d.day, tzinfo=tz).astimezone(timezone.utc)


def day_end(d: date, tz: tzinfo) -> datetime:
    """UTC instant of 24:00 local on `d` (midnight starting the next day)."""
    return day_start(d + timedelta(days=1), tz)


class History:
    def __init__(self, rows: List[dict], not_delivered: Sequence[str]):
        self.rows = sorted(rows, key=lambda r: r["rev"])
        self.times = [parse_ts(r["changed"]) for r in self.rows]
        self.not_delivered = {r.lower() for r in not_delivered}
        last = self.rows[-1]
        self.id = last["id"]
        self.type = last["type"]
        self.title = last["title"]
        self.parent = last["parent"]

    def at(self, t: datetime) -> Optional[dict]:
        found = None
        for row, ts in zip(self.rows, self.times):
            if ts <= t:
                found = row
        return found

    @property
    def first_active(self) -> Optional[datetime]:
        for row, ts in zip(self.rows, self.times):
            if row["category"] in ACTIVE:
                return ts
        return None

    def cut(self, row: dict) -> bool:
        """Closed without delivery: category Removed, or a not-delivered reason (any case)."""
        return row["category"] == "Removed" or (
            row["category"] == "Completed" and (row["reason"] or "").lower() in self.not_delivered)

    @property
    def done_at(self) -> Optional[datetime]:
        for row, ts in zip(self.rows, self.times):
            if row["category"] == "Completed" and not self.cut(row):
                return ts
        return None

    @property
    def reopened(self) -> bool:
        done = self.done_at
        return done is not None and any(
            row["category"] in ACTIVE and ts > done for row, ts in zip(self.rows, self.times))

    def assignees_active(self, until: datetime) -> Set[str]:
        """Assignees recorded on revisions up to `until` while the item was active."""
        return {row["assigned"] for row, ts in zip(self.rows, self.times)
                if ts <= until and row["category"] in ACTIVE and row["assigned"]}

    def waiting_seconds(self, start: datetime, end: datetime, waiting_states: Sequence[str]) -> float:
        states = set(waiting_states)
        total = 0.0
        for i, (row, ts) in enumerate(zip(self.rows, self.times)):
            nxt = self.times[i + 1] if i + 1 < len(self.rows) else end
            lo, hi = max(ts, start), min(nxt, end)
            blocked = any(str(t).lower() == "blocked" for t in row["tags"] or [])
            if hi > lo and (row["state"] in states or blocked):
                total += (hi - lo).total_seconds()
        return total

    def last_change(self, before: datetime) -> Optional[datetime]:
        """Time of the last revision at or before `before`."""
        found = [ts for ts in self.times if ts <= before]
        return max(found) if found else None

    def was_in(self, path: str, t: datetime) -> bool:
        row = self.at(t)
        return row is not None and row["iteration"] == path
