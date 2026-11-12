"""Pricing Models: the team at risk. The rate table versioning ticket
#40655 keeps coming back from QA and review with rewritten acceptance
criteria, and delivery falls sprint after sprint."""
from itertools import count

from .builder import PortfolioBuilder
from .portfolio_common import (PO, PRICING, PRICING_SPRINTS, QA, REVIEWER,
                               SM, STAFF_START, Titles, chain, parent_id,
                               squad, text_for, workdays)

PARENTS = ("Rate table versioning", "Premium calculation",
           "Tariff publishing")
ACTIONS = ("Recalculate", "Version", "Validate", "Publish", "Compare",
           "Round", "Audit")
OBJECTS = ("base rates", "regional loadings", "no-claims discount",
           "young driver surcharge", "fleet tariff", "tariff calendar")

# Delivered per sprint, PM-18 .. PM-23 (PM-23 handled in sprint_pm23):
# Dávid 3,3,3,2,1,1 and Emese 2,2,2,2,1,1, Ferenc (from PM-22) 1,0
# -> team 5,5,5,4,3,2. Deliberate trend outcomes: each of the last three
# team counts is below the mean of the three before it, so the team gets
# "Fewer delivered than usual", and so does Dávid; Emese's 2 in PM-21 is
# not below 2, so she does not.
DAVID = (3, 3, 3, 2, 1)
EMESE = (2, 2, 2, 2, 1)


def pricing_models(b: PortfolioBuilder) -> None:
    b.team(PRICING, area="Demo\\" + PRICING, sprints=PRICING_SPRINTS)
    david = b.person("david", "Dávid R.", PRICING, STAFF_START)
    emese = b.person("emese", "Emese V.", PRICING, STAFF_START)
    # Ferenc joins in PM-22 (12 Oct): "Joined in PM-22".
    ferenc = b.person("ferenc", "Ferenc P.", PRICING, "2026-10-12")

    ids = count(40600)
    squad_ids = count(52000)
    titles = Titles(ACTIONS, OBJECTS).next
    for i, sprint in enumerate(PRICING_SPRINTS[:5]):
        for who, n, minute in ((david, DAVID[i], 1), (emese, EMESE[i], 2)):
            chain(b, [next(ids) for _ in range(n)], PRICING, sprint, who,
                  [titles() for _ in range(n)],
                  [PARENTS[1 + (i + k) % 2] for k in range(n)],
                  minute=minute)
        if i == 4:
            # Ferenc's first ticket, from his third day; delivered.
            chain(b, [next(ids)], PRICING, sprint, ferenc, [titles()],
                  [PARENTS[2]], first=2, minute=3)
        squad(b, squad_ids, PRICING, sprint, 5)
    squad(b, squad_ids, PRICING, PRICING_SPRINTS[5], 5)
    rate_table_ticket(b, david)
    sprint_pm23(b, david, emese, ferenc, titles, next(ids))


def _ticket(b, tid, title, parent):
    return b.ticket(tid, team=PRICING, title=title, parent=parent_id(parent),
                    parent_title=parent, **text_for(title))


def rate_table_ticket(b: PortfolioBuilder, david: str) -> None:
    """#40655 -> Carried over, in progress, held by Dávid at the end of
    PM-21, PM-22 and PM-23 (sprints_carried 3); awaiting QA at the end of
    PM-20. Returned three times; after each return the product owner
    rewrites the acceptance criteria. Whole life to the cut-off: 11.625
    working days in progress with Dávid against 15 waiting on QA and 7
    waiting on review."""
    t = _ticket(b, 40655, "Version the rate tables by effective date",
                "Rate table versioning")
    t.on("2026-09-21 09:00", state="New", iteration="PM-20",
         assignee=david, by=PO)
    t.on("2026-09-22 09:00", state="Active")                  # 3 days
    t.on("2026-09-25 09:00", state="In QA")                   # QA 9 days
    t.on("2026-09-26 10:00", iteration="PM-21", by=SM)
    t.on("2026-10-08 09:00", state="Active", by=QA)           # return 1
    t.edit("2026-10-08 10:00", by=PO, acceptance=(
        "Given two rate tables with different effective dates, when a "
        "quote is priced, then the table in effect on the quote date is "
        "used."))
    t.on("2026-10-10 10:00", iteration="PM-22", by=SM)        # 3 days
    t.on("2026-10-13 09:00", state="In QA")                   # QA 6 days
    t.on("2026-10-21 09:00", state="Active", by=QA)           # return 2
    t.edit("2026-10-21 10:00", by=PO, acceptance=(
        "Given rate tables with overlapping effective dates, when a quote "
        "is priced, then the most recently approved table wins and the "
        "choice is logged."))
    t.on("2026-10-24 10:00", iteration="PM-23", by=SM)        # 4 days
    t.on("2026-10-27 09:00", state="In Review")               # review 7
    t.on("2026-11-05 09:00", state="Active", by=REVIEWER)     # return 3
    t.edit("2026-11-05 10:00", by=PO, acceptance=(
        "Given rate tables with overlapping effective dates, when a quote "
        "is priced or re-priced, then the table approved last before the "
        "quote date wins, the choice is logged, and back-dated approvals "
        "are rejected."))


def sprint_pm23(b: PortfolioBuilder, david: str, emese: str, ferenc: str,
                titles, routine_id: int) -> None:
    sprint = PRICING_SPRINTS[-1]
    name = sprint[0]
    d = workdays(sprint[1])  # 26 Oct .. 6 Nov

    # Dávid's one delivery, worked while #40655 waits on review (27 Oct -
    # 4 Nov), so he has no day with nothing in progress.
    chain(b, [routine_id], PRICING, sprint, david, [titles()], [PARENTS[1]],
          first=1, last=7, minute=1)

    # 40662 -> Delivered by Emese after one return from QA (29 Oct). With
    # #40655 this makes two returned tickets in PM-23: rule "Returns".
    _ticket(b, 40662, "Round premiums to the nearest five cents",
            "Premium calculation").on(
        "%s 08:00" % d[0], state="New", iteration=name, assignee=emese,
        by=PO).on(
        "%s 09:00" % d[0], state="Active").on(
        "%s 09:00" % d[2], state="In QA").on(
        "%s 09:00" % d[3], state="Active", by=QA).on(
        "%s 09:00" % d[5], state="In QA").on(
        "%s 15:00" % d[6], state="Closed", by=QA)

    # 40663 -> Carried over, in progress (1 sprint). Emese waited for it to
    # be refined: nothing in progress on 3, 4 and 5 Nov -> "3 days with
    # nothing in progress".
    _ticket(b, 40663, "Compare tariff versions side by side",
            "Tariff publishing").on(
        "%s 08:00" % d[0], state="New", iteration=name, assignee=emese,
        by=PO).on(
        "%s 09:00" % d[9], state="Active")

    # 40671 -> Finished, awaiting acceptance: Ferenc hands it to review on
    # 28 Oct and it is still waiting at the end of the sprint (7.5 days).
    _ticket(b, 40671, "Publish the tariff calendar to the broker portal",
            "Tariff publishing").on(
        "%s 08:00" % d[0], state="New", iteration=name, assignee=ferenc,
        by=PO).on(
        "%s 09:00" % d[0], state="Active").on(
        "%s 09:00" % d[2], state="In Review")

    # 40672 -> Carried over, in progress (1 sprint), held by Ferenc.
    _ticket(b, 40672, "Audit regional loadings against the filed tariff",
            "Premium calculation").on(
        "%s 08:00" % d[0], state="New", iteration=name, assignee=ferenc,
        by=PO).on(
        "%s 09:00" % d[2], state="Active")
