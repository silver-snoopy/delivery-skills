"""Claims Intake: a steady team (On track) whose last sprint, Sprint 37,
carries most of the edge cases a delivery manager meets."""
from itertools import count

from .builder import PortfolioBuilder
from .portfolio_common import (CLAIMS, CLAIMS_SPRINTS, INTEGRATION, PO,
                               REVIEWER, STAFF_START, Titles, chain,
                               parent_id, squad, text_for, workdays)

PARENTS = ("First notice of loss intake", "Claim document upload",
           "Claim triage rules")
ACTIONS, OBJECTS = (
    ("Validate", "Store", "Show", "Log changes to", "Export", "Prefill",
     "Search by"),
    ("policy number", "loss date", "claimant address", "vehicle details",
     "witness statements", "photo attachments", "repair estimate"))


def claims_intake(b: PortfolioBuilder) -> None:
    b.team(CLAIMS, area="Demo\\" + CLAIMS, sprints=CLAIMS_SPRINTS)
    anna = b.person("anna", "Anna K.", CLAIMS, STAFF_START)
    bence = b.person("bence", "Bence T.", CLAIMS, STAFF_START)
    csilla = b.person("csilla", "Csilla M.", CLAIMS, STAFF_START)
    # Csilla is on planned leave Mon 2 - Thu 5 Nov: "4 days leave" in S37.
    b.absence("csilla", "2026-11-02", "2026-11-05", "Planned leave")

    ids = count(40100)
    squad_ids = count(51000)
    titles = Titles(ACTIONS, OBJECTS).next
    for i, sprint in enumerate(CLAIMS_SPRINTS):
        # Delivered per sprint: Anna 2 (3 in Sprint 33 and 36), Bence 2,
        # Csilla 2 -> 6, 7, 6, 6, 7, and 7 in Sprint 37 with the mid-sprint
        # addition below. No trend rule fires.
        n_anna = 3 if i in (1, 4) else 2
        for who, n, minute in ((anna, n_anna, 1), (bence, 2, 2)):
            chain(b, [next(ids) for _ in range(n)], CLAIMS, sprint, who,
                  [titles() for _ in range(n)],
                  [PARENTS[(i + k) % 3] for k in range(n)], minute=minute)
        if i < 5:
            chain(b, [next(ids), next(ids)], CLAIMS, sprint, csilla,
                  [titles(), titles()], [PARENTS[2], PARENTS[0]], minute=3)
        else:
            # Both delivered before her leave.
            chain(b, [next(ids), 40196], CLAIMS, sprint, csilla,
                  [titles(), "Show excess and deductible split on the "
                   "claim summary"], [PARENTS[0], "Claim summary page"],
                  first=0, last=4, minute=3)
        squad(b, squad_ids, CLAIMS, sprint, 5)
    sprint_37(b, anna, bence)


def _ticket(b, tid, title, parent):
    return b.ticket(tid, team=CLAIMS, title=title, parent=parent_id(parent),
                    parent_title=parent, **text_for(title))


def sprint_37(b: PortfolioBuilder, anna: str, bence: str) -> None:
    name = CLAIMS_SPRINTS[-1][0]
    d = workdays(CLAIMS_SPRINTS[-1][1])  # 26 Oct .. 6 Nov

    # 40191 -> Closed, not delivered: closed as a duplicate.
    _ticket(b, 40191, "Reject claims with a missing loss date",
            "Claim triage rules").on(
        "%s 08:00" % d[0], state="New", iteration=name, assignee=bence,
        by=PO).on(
        "%s 09:00" % d[1], state="Active").on(
        "%s 09:00" % d[2], state="Closed", reason="Duplicate", by=PO)

    # 40192 -> Delivered; added by the product owner mid-sprint (29 Oct).
    _ticket(b, 40192, "Flag urgent claims for same-day callback",
            "Claim triage rules").on(
        "%s 10:00" % d[3], state="New", iteration=name, assignee=bence,
        by=PO).on(
        "%s 11:00" % d[3], state="Active").on(
        "%s 09:00" % d[5], state="In Review").on(
        "%s 15:10" % d[6], state="Closed", by=REVIEWER)

    # 40193 -> Carried over, in progress, with another party: Anna worked
    # it, then the customer's integration team took it over on 3 Nov.
    _ticket(b, 40193, "Send intake events to the policy system",
            "First notice of loss intake").on(
        "%s 08:00" % d[0], state="New", iteration=name, assignee=anna,
        by=PO).on(
        "%s 09:00" % d[0], state="Active").on(
        "%s 09:00" % d[6], assignee=INTEGRATION, by=PO)

    # 40194 -> Active outside a sprint: picked up from the backlog.
    _ticket(b, 40194, "Tidy the intake error messages",
            "First notice of loss intake").on(
        "%s 10:00" % d[4], state="New", iteration=None, assignee=anna,
        by=PO).on(
        "%s 09:00" % d[5], state="Active")

    # 40195 -> Assigned, not started: assigned in the backlog, never begun.
    _ticket(b, 40195, "Archive closed draft claims",
            "Claim document upload").on(
        "%s 11:00" % d[4], state="New", iteration=None, assignee=bence,
        by=PO)

    # 40197 -> Carried over, in progress, held by Anna (1 sprint). Its DM
    # note names nobody. The title holds HTML-looking text and a pipe,
    # which every output must escape.
    _ticket(b, 40197, "Look up claimant address via <AddressService> | "
            "fall back to manual entry",
            "First notice of loss intake").on(
        "%s 08:00" % d[0], state="New", iteration=name, assignee=anna,
        by=PO).on(
        "%s 09:00" % d[7], state="Active")
