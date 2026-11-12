"""Data Platform: sprints offset one week from the other teams, so the
report shows a second date group. Gábor is the only vendor contributor on
three parent features; two tickets come back from review in DP-19, so the
team needs attention."""
from itertools import count

from .builder import PortfolioBuilder
from .portfolio_common import (PLATFORM, PLATFORM_SPRINTS, PO, REVIEWER,
                               STAFF_START, Titles, chain, parent_id, squad,
                               text_for, workdays)

GABOR_PARENTS = ("Nightly policy data load", "Claims event stream",
                 "Reference data refresh")
HANNA_PARENTS = ("Data quality checks", "Reporting warehouse")
ACTIONS = ("Backfill", "Partition", "Monitor", "Deduplicate", "Index",
           "Document", "Retry")
OBJECTS = ("policy snapshots", "claim events", "currency rates",
           "postcode tables", "broker records", "payment batches")


def data_platform(b: PortfolioBuilder) -> None:
    b.team(PLATFORM, area="Demo\\" + PLATFORM, sprints=PLATFORM_SPRINTS)
    gabor = b.person("gabor", "Gábor S.", PLATFORM, STAFF_START)
    hanna = b.person("hanna", "Hanna L.", PLATFORM, STAFF_START)

    ids = count(40900)
    squad_ids = count(53000)
    titles = Titles(ACTIONS, OBJECTS).next
    # Two delivered tickets each per sprint -> 4, 4, 4, 4, 4, 4.
    for i, sprint in enumerate(PLATFORM_SPRINTS):
        first = 0 if i < 5 else 4  # in DP-19 the returned tickets come first
        chain(b, [next(ids) for _ in range(2 if i < 5 else 1)], PLATFORM,
              sprint, gabor, [titles(), titles()],
              [GABOR_PARENTS[(2 * i + k) % 3] for k in range(2)],
              first=first, minute=1)
        chain(b, [next(ids) for _ in range(2 if i < 5 else 1)], PLATFORM,
              sprint, hanna, [titles(), titles()],
              [HANNA_PARENTS[k] for k in range(2)], first=first, minute=2)
        squad(b, squad_ids, PLATFORM, sprint, 5)
    sprint_dp19(b, gabor, hanna)


def sprint_dp19(b: PortfolioBuilder, gabor: str, hanna: str) -> None:
    name, start, _ = PLATFORM_SPRINTS[-1]
    d = workdays(start)  # 19 Oct .. 30 Oct
    # 40951, 40952 -> Delivered after one return from review each (22 Oct):
    # two returned tickets -> rule "Returns" -> Needs attention.
    for tid, who, title, parent in (
            (40951, gabor, "Retry failed nightly policy loads",
             GABOR_PARENTS[0]),
            (40952, hanna, "Check row counts after each warehouse load",
             HANNA_PARENTS[0])):
        b.ticket(tid, team=PLATFORM, title=title, parent=parent_id(parent),
                 parent_title=parent, **text_for(title)).on(
            "%s 08:00" % d[0], state="New", iteration=name, assignee=who,
            by=PO).on(
            "%s 09:00" % d[0], state="Active").on(
            "%s 09:00" % d[2], state="In Review").on(
            "%s 09:00" % d[3], state="Active", by=REVIEWER).on(
            "%s 09:00" % d[4], state="In Review").on(
            "%s 14:00" % d[4], state="Closed", by=REVIEWER)
