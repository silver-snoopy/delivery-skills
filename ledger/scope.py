"""Who decided the sprint's scope: which party placed each ticket in the
sprint, added it after the start, or took it out again.

The board records who saved each change (`System.ChangedBy`). A vendor
engineer rarely chooses what enters a customer's sprint; these figures
show it from the record instead of leaving "planned" to read as the
vendor's own commitment. A party is one of PARTIES, never a person."""
from datetime import datetime
from typing import List, Optional, Sequence

from .model import Revision, Sprint
from .timeline import Timeline

PARTIES = ("other_party", "vendor", "automation", "not_recorded")


def party(actor: Optional[str]) -> str:
    """The party behind a stored actor string: a roster person ("p:") is
    the vendor, a configured service identity ("s:") is automation, any
    other identity ("o:") is another party; no actor is not recorded."""
    if not actor:
        return "not_recorded"
    if actor.startswith("p:"):
        return "vendor"
    if actor.startswith("s:"):
        return "automation"
    return "other_party"


def _ordered(tl: Timeline) -> List[Revision]:
    return sorted(tl.revisions, key=lambda r: (r.changed, r.rev))


def placed_by(tl: Timeline, sprint: Sprint) -> Optional[Revision]:
    """The revision that put the ticket in the sprint: the start of the
    first run of revisions on the sprint's path that is in force at some
    instant inside [sprint.start, sprint.end). None when the ticket was
    never in the sprint while it ran."""
    revs = _ordered(tl)
    entry = None
    for k, r in enumerate(revs):
        on_path = r.iteration == sprint.path
        if not on_path:
            entry = None
            continue
        if entry is None:
            entry = r
        until: Optional[datetime] = revs[k + 1].changed \
            if k + 1 < len(revs) else None
        if r.changed < sprint.end and (until is None or until > sprint.start):
            return entry
    return None


def removed_by(tl: Timeline, sprint: Sprint) -> Optional[Revision]:
    """The last revision before the sprint's end that moved the ticket off
    the sprint's path, or None."""
    out = None
    prev = None
    for r in _ordered(tl):
        if r.changed >= sprint.end:
            break
        if prev is not None and prev.iteration == sprint.path \
                and r.iteration != sprint.path:
            out = r
        prev = r
    return out


def split(parties: Sequence[str]) -> dict:
    """Counts per party, every party present (zero when none)."""
    return {p: sum(1 for x in parties if x == p) for p in PARTIES}
