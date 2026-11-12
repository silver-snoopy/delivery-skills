"""Shared pieces of the golden demo portfolio: team names, sprint calendars,
the people outside the vendor roster, and small helpers that lay down
routine ticket histories. All names and data are invented."""
from datetime import date, timedelta
from itertools import count, product
from typing import Iterator, List, Optional, Sequence

from .builder import PortfolioBuilder

CLAIMS = "Claims Intake"
PRICING = "Pricing Models"
PLATFORM = "Data Platform"
TZ = "Europe/Zurich"
CUTOFF = "2026-11-07 08:00"
AS_OF = "2026-11-07"
STAFF_START = "2026-01-05"

# Six two-week sprints; the last is 26 Oct - 6 Nov 2026.
_STARTS = [date(2026, 8, 17) + timedelta(weeks=2 * i) for i in range(6)]


def _sprints(prefix: str, first: int, shift_days: int = 0) -> List[tuple]:
    out = []
    for i, s in enumerate(_STARTS):
        s = s + timedelta(days=shift_days)
        out.append(("%s%d" % (prefix, first + i), s.isoformat(),
                    (s + timedelta(days=11)).isoformat()))
    return out


CLAIMS_SPRINTS = _sprints("Sprint ", 32)
PRICING_SPRINTS = _sprints("PM-", 18)
PLATFORM_SPRINTS = _sprints("DP-", 14, shift_days=-7)  # one week earlier

PO = PortfolioBuilder.other("product owner")
SM = PortfolioBuilder.other("scrum master")
QA = PortfolioBuilder.other("customer qa")
REVIEWER = PortfolioBuilder.other("customer tech lead")
INTEGRATION = PortfolioBuilder.other("customer integration team")


def workdays(start: str) -> List[str]:
    """The ten working dates (ISO) of the two-week sprint starting `start`."""
    d, out = date.fromisoformat(start), []
    while len(out) < 10:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class Titles:
    """Unique, readable ticket titles: every action with every object."""

    def __init__(self, actions: Sequence[str], objects: Sequence[str]):
        self._it: Iterator = iter(product(objects, actions))

    def next(self) -> str:
        obj, action = next(self._it)
        return "%s %s" % (action, obj)


def text_for(title: str) -> dict:
    return dict(
        description="%s. Scope agreed in backlog refinement; see the "
                    "linked feature for background." % title,
        acceptance="Given the agreed test data, when the change is "
                   "deployed to the test environment, then: %s works as "
                   "described." % title.lower())


def chain(b: PortfolioBuilder, ids: Sequence[int], team: str,
          sprint: tuple, who: str, titles: Sequence[str],
          parents: Sequence[str], first: int = 0, last: int = 9,
          minute: int = 0) -> None:
    """Tickets worked back to back by `who` between working-day indices
    `first` and `last` of the sprint; each is planned at the sprint start,
    handed to review and closed (delivered) the day its successor starts."""
    name, start, _ = sprint
    days = workdays(start)
    n = len(ids)
    bounds = [first + round(k * (last - first) / n) for k in range(n + 1)]
    for k, tid in enumerate(ids):
        a, z = days[bounds[k]], days[bounds[k + 1]]
        if a == z:
            raise ValueError("chain for %s in %s is too dense" % (who, name))
        t = b.ticket(tid, team=team, title=titles[k],
                     parent=parent_id(parents[k]),
                     parent_title=parents[k], **text_for(titles[k]))
        t.on("%s 08:%02d" % (days[0], minute), state="New", iteration=name,
             assignee=who, by=PO)
        t.on("%s 09:00" % a, state="Active")
        t.on("%s 09:00" % z, state="In Review")
        t.on("%s 15:%02d" % (z, minute), state="Closed", by=REVIEWER)


def squad(b: PortfolioBuilder, ids: Iterator[int], team: str,
          sprint: tuple, n: int) -> None:
    """n customer-squad tickets (no vendor engineer) delivered in the
    sprint; they count only towards squad delivered."""
    name, start, _ = sprint
    days = workdays(start)
    for k in range(n):
        b.squad_ticket(next(ids), team=team).on(
            "%s 08:30" % days[0], iteration=name).on(
            "%s 10:00" % days[1 + k % 5], state="Active").on(
            "%s 16:00" % days[6 + k % 4], state="Closed")


_PARENTS: dict = {}
_PARENT_IDS = count(39001)


def parent_id(title: str) -> Optional[int]:
    if title not in _PARENTS:
        _PARENTS[title] = next(_PARENT_IDS)
    return _PARENTS[title]
