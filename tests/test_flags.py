import unittest
from datetime import date

from ledger.config import Notes, Person
from tests.fixture import Board
from ledger.metrics import _status
from tests.test_metrics import IVAN, LENA, MAYA, PEOPLE, PO, S28, S29, board, run

NADIA, TOM, JANE = "nadia@vendor.example", "tom.h2@vendor.example", "jane@customer.example"
S27 = "Proj\\S27"
GONE = [PEOPLE[0], Person("Ivan Holt", [IVAN], "ALFA", None, date(2026, 10, 26))]


def flags(t, level=None, ask=None):
    return [a for a in t["attention"] if (level is None or a["level"] == level)
            and (ask is None or a["ask"] == ask)]


def item_flags(t):
    return [a for a in t["attention"] if a["text"].startswith("#")]


def person(t, name):
    return next(p for p in t["people"] if p["name"] == name)


def closed(b, i, who=MAYA, area="Proj\\Claims", start="2026-10-27 09:00",
           end="2026-11-03 09:00"):
    return b.item(i, area=area).at(start, iteration=S29, assigned=who, state="Active") \
        .at(end, state="Closed")


class PeopleFlagTests(unittest.TestCase):
    def test_no_assignment_is_red_unless_onboarding(self):
        b = board()
        closed(b, 1)
        b.item(2).at("2026-10-20 09:00", iteration=S28, assigned=IVAN, state="Active") \
            .at("2026-10-22 09:00", state="Closed")
        people = PEOPLE + [Person("Nadia Kern", [NADIA], "ALFA", date(2026, 10, 28), None)]
        t = run(b, "ALFA", "S29", people=people)
        self.assertEqual([p["name"] for p in t["people"]], ["Ivan Holt", "Maya Brook", "Nadia Kern"])
        self.assertEqual([(p["no_assignment"], p["onboarding"]) for p in t["people"]],
                         [(True, False), (False, False), (True, True)])
        self.assertEqual(flags(t, "red"), [
            {"level": "red", "text": "Ivan Holt had 9 available days but no item assigned "
                                     "during the sprint.",
             "ask": "Find out what they worked on; if it was off-board work, add it to notes.txt."}])
        # assigned at the sprint start, with no change during the sprint, counts as assigned
        b.item(3).at("2026-10-23 09:00", iteration=S29, assigned=IVAN)
        t = run(b, "ALFA", "S29", people=people)
        self.assertFalse(person(t, "Ivan Holt")["no_assignment"])
        self.assertEqual(flags(t, "red"), [])

    def test_nothing_delivered_amber_after_onboarding(self):
        b = board().sprint("S27", "2026-09-28", "2026-10-09")
        closed(b, 1)
        b.item(2).at("2026-09-28 09:00", iteration=S27, assigned=MAYA, state="Active") \
            .at("2026-10-02 09:00", state="Closed")
        b.item(4).at("2026-10-14 09:00", iteration=S28, assigned=MAYA, state="Active") \
            .at("2026-10-16 09:00", state="Closed")
        b.item(3).at("2026-11-02 09:00", iteration=S29, assigned=NADIA, state="Active")
        ask = "Ask what is blocking them."
        nadia = Person("Nadia Kern", [NADIA], "ALFA", date(2026, 9, 30), None)
        t = run(b, "ALFA", "S29", people=[PEOPLE[0], nadia])
        self.assertEqual(person(t, "Nadia Kern"), {
            "name": "Nadia Kern", "available": 9.0, "nominal": 9.0, "delivered": [],
            "in_progress": [3], "oldest": [3, 6], "median": None, "items": [3],
            "onboarding": False, "no_assignment": False})
        self.assertEqual(person(t, "Maya Brook")["delivered"], [1])
        self.assertEqual(person(t, "Maya Brook")["median"], 8)
        self.assertEqual([a for a in flags(t) if a["ask"] == ask], [
            {"level": "amber", "text": "Nadia Kern had 9 available days and delivered nothing.",
             "ask": ask}])
        nadia.joined = date(2026, 10, 28)
        t = run(b, "ALFA", "S29", people=[PEOPLE[0], nadia])
        self.assertTrue(person(t, "Nadia Kern")["onboarding"])
        self.assertEqual([a for a in flags(t) if a["ask"] == ask], [])

    def test_wip_over_three(self):
        b = board()
        for i in (1, 2, 3, 4):
            b.item(i).at("2026-11-05 09:00", iteration=S29, assigned=MAYA, state="Active")
        for i in (5, 6, 7):
            b.item(i).at("2026-11-05 09:00", iteration=S29, assigned=IVAN, state="Active")
        t = run(b, "ALFA", "S29")
        self.assertEqual(person(t, "Maya Brook")["in_progress"], [1, 2, 3, 4])
        self.assertEqual(flags(t, ask="Ask which item to finish first."), [
            {"level": "amber", "text": "Maya Brook has 4 items in progress: #1, #2, #3, #4.",
             "ask": "Ask which item to finish first."}])


class ItemFlagTests(unittest.TestCase):
    def test_waiting_more_than_three_working_days(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-02 09:00", state="Ready for QA")
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-05 09:00", state="Ready for QA")
        b.item(3).at("2026-10-27 09:00", iteration=S29, assigned=IVAN, state="Active") \
            .at("2026-11-02 09:00", tags=["Blocked"])
        t = run(b, "ALFA", "S29")
        self.assertEqual(flags(t, ask="Raise it with the PO."), [
            {"level": "amber", "text": "#1 has waited in Ready for QA for 4 working days.",
             "ask": "Raise it with the PO."},
            {"level": "amber", "text": "#3 has waited with the Blocked tag for 4 working days.",
             "ask": "Raise it with the PO."}])

    def test_stale_active_item(self):
        b = board()
        ask = "Ask whether it is moving and the board is behind, or it is stuck."
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-30 09:00")
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-02 09:00")
        t = run(b, "ALFA", "S29")
        self.assertEqual(flags(t, ask=ask), [
            {"level": "amber", "text": "#1 is Active with no board change for 5 working days.",
             "ask": ask}])

    def test_stale_counts_child_changes_at_stories_level(self):
        b = board()
        ask = "Ask whether it is moving and the board is behind, or it is stuck."
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active")
        b.item(2, "Task", parent=1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA,
                                       state="Active").at("2026-11-04 09:00", state="Closed")
        self.assertEqual(flags(run(b, "ALFA", "S29"), ask=ask), [])
        b.item(3).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active")
        self.assertEqual([a["text"] for a in flags(run(b, "ALFA", "S29"), ask=ask)],
                         ["#3 is Active with no board change for 8 working days."])

    def test_unknown_assignee_only_in_the_team_that_counts_the_item(self):
        b = board()  # ALFA and BETA share the iteration path Proj\S29
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active")
        b.item(2, "Task").at("2026-10-27 09:00", iteration=S29, assigned=LENA, state="Active")
        b.item(3, "Task").at("2026-10-27 09:00", iteration=S29, assigned=TOM, state="Active")
        texts = lambda team: [a["text"] for a in flags(run(b, team, "S29"), "grey")]
        self.assertEqual(texts("ALFA"), [])
        self.assertEqual(texts("BETA"), ["#3 is assigned to tom.h2@vendor.example, which is not "
                                         "in team.csv."])

    def test_unknown_roster_domain_assignee_is_grey(self):
        b = board()
        closed(b, 1)
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=TOM, state="Active")
        b.item(3).at("2026-10-27 09:00", iteration=S29, assigned=JANE, state="Active")
        t = run(b, "ALFA", "S29")
        self.assertEqual(flags(t, "grey"), [
            {"level": "grey", "text": "#2 is assigned to tom.h2@vendor.example, which is not "
                                      "in team.csv.",
             "ask": "Fix the board or add the email to team.csv."}])

    def test_done_without_active_and_reopened_are_grey(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA) \
            .at("2026-11-02 09:00", state="Closed")
        b.item(2).at("2026-10-13 09:00", iteration=S28, assigned=MAYA, state="Active") \
            .at("2026-10-20 09:00", state="Closed").at("2026-10-28 09:00", state="Active") \
            .at("2026-11-02 09:00", state="Closed")
        t = run(b, "ALFA", "S29")
        self.assertEqual([(a["level"], a["text"], a["ask"]) for a in flags(t, "grey")], [
            ("grey", "#1 was closed without ever being in progress.",
             "Ask them to move items through Active on the board."),
            ("grey", "#2 was reopened.", "Ask why it was reopened.")])

    def test_old_and_carried_items(self):
        b = board()
        for i, day in ((1, "16"), (2, "20"), (3, "21")):  # fast deliveries set P85 = 3 days
            b.item(10 + i).at("2026-10-%s 09:00" % day, iteration=S28, assigned=IVAN,
                              state="Active").at("2026-10-%d 09:00" % (int(day) + 2), state="Closed")
        b.item(1).at("2026-10-13 09:00", iteration=S28, assigned=MAYA, state="Active") \
            .at("2026-10-26 09:00", iteration=S29).at("2026-11-06 09:00")
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-06 09:00")
        notes = Notes(items={1: ("agreed", "waiting for API")})
        t = run(b, "ALFA", "S29", people=GONE, notes=notes)
        self.assertEqual(t["cycle_window"]["p85"], 3)
        self.assertEqual(item_flags(t), [
            {"level": "red", "text": "#1 has been carried over for 2 sprints and is 26 days "
                                     "old, older than the squad's P85 of 3 days.",
             "ask": "Ask what is blocking it."},
            {"level": "amber", "text": "#2 is 12 days old, older than the squad's P85 of 3 days.",
             "ask": "Ask what is blocking it."},
            {"level": "amber", "text": "#2 was carried over with no note in notes.txt.",
             "ask": "Confirm with the PO and record it in notes.txt."}])


class RestOfSquadTests(unittest.TestCase):
    def test_rest_of_squad(self):
        b = board()
        closed(b, 1)
        closed(b, 2, who=JANE, start="2026-10-28 09:00", end="2026-10-29 09:00")
        b.item(3).at("2026-10-28 09:00", iteration=S29, assigned=JANE, state="Active") \
            .at("2026-10-29 09:00", state="Ready for QA").at("2026-11-01 09:00", state="Closed")
        closed(b, 4, who=JANE, area="Proj\\Billing")
        closed(b, 5, who=JANE, start="2026-10-13 09:00", end="2026-10-20 09:00")
        t = run(b, "ALFA", "S29")
        self.assertEqual(t["delivered"], [1])
        self.assertEqual(t["rest"], {"assignees": 1, "delivered": 2, "median": 2, "p85": 5,
                                     "waiting_share": 0.6})
        self.assertIsNone(run(b, "BETA", "S29")["rest"])

    def test_rest_counts_engineers_on_children_not_the_story_owner(self):
        b = board()
        closed(b, 1)
        closed(b, 2, who=PO)
        b.item(3, "Task", parent=2).at("2026-10-27 09:00", iteration=S29, assigned=JANE,
                                       state="Active").at("2026-11-02 09:00", state="Closed")
        closed(b, 4, who=JANE)
        rest = run(b, "ALFA", "S29")["rest"]
        self.assertEqual((rest["delivered"], rest["assignees"]), (2, 1))


class StatusTests(unittest.TestCase):
    def _board(self, delivered, open_, agreed=None, wait_from=None):
        b = board()
        for i, day in ((91, "16"), (92, "20"), (93, "21")):  # S28: P85 = 3 days
            b.item(i).at("2026-10-%s 09:00" % day, iteration=S28, assigned=IVAN, state="Active") \
                .at("2026-10-%d 09:00" % (int(day) + 2), state="Closed")
        for i in range(1, delivered + 1):
            closed(b, i, end="2026-10-28 09:00")
        for i in range(delivered + 1, delivered + open_ + 1):
            b.item(i).at("2026-10-27 09:00", iteration=S29, assigned=MAYA)
        old = b.item(50).at("2026-10-13 09:00", iteration=S28, assigned=MAYA, state="Active")
        if wait_from and wait_from < "2026-10-26":
            old.at(wait_from, state="Ready for QA")
        old.at("2026-10-26 09:00", iteration=S29)
        if wait_from and wait_from > "2026-10-26 09:00":
            old.at(wait_from, state="Ready for QA")
        b.item(60).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-29 09:00", iteration="Proj")
        notes = Notes(items={50: (agreed, "API late")} if agreed else {})
        return b, notes

    def status(self, *args, **kwargs):
        b, notes = self._board(*args, **kwargs)
        return run(b, "ALFA", "S29", people=GONE, notes=notes)["status"]

    def test_status_rules(self):
        self.assertEqual(self.status(4, 0, agreed="agreed"), {
            "colour": "GREEN", "manual": False,
            "reason": "80% of planned work items delivered, not counting 1 removed item; the "
                      "one old carry-over (#50) was agreed with the PO."})
        self.assertEqual(self.status(3, 1, agreed="agreed"), {
            "colour": "AMBER", "manual": False,
            "reason": "60% of planned work items delivered, not counting 1 removed item; the "
                      "one old carry-over (#50) was agreed with the PO."})
        self.assertEqual(self.status(4, 0)["colour"], "AMBER")
        self.assertEqual(self.status(3, 1), {
            "colour": "RED", "manual": False,
            "reason": "60% of planned work items delivered, not counting 1 removed item; the "
                      "one old carry-over (#50) is not recorded as agreed with the PO."})
        self.assertEqual(self.status(4, 0, agreed="agreed", wait_from="2026-10-22 09:00"), {
            "colour": "RED", "manual": False,
            "reason": "80% of planned work items delivered, not counting 1 removed item; the "
                      "one old carry-over (#50) was agreed with the PO; #50 has waited on the "
                      "customer for 11 working days."})
        self.assertEqual(self.status(4, 0, agreed="agreed",
                                     wait_from="2026-10-26 10:00")["colour"], "GREEN")
        b, notes = self._board(3, 1)
        notes.status["alfa"] = ("AMBER", "PO moved the release; agreed scope cut.")
        self.assertEqual(run(b, "ALFA", "S29", people=GONE, notes=notes)["status"], {
            "colour": "AMBER", "reason": "PO moved the release; agreed scope cut.", "manual": True})

    def test_status_share_is_floored(self):
        f = {"planned": list(range(49)), "delivered_planned": list(range(39)), "removed": []}
        self.assertEqual(_status(f, [], None, {}, None), {
            "colour": "AMBER", "manual": False,
            "reason": "79% of planned work items delivered; no carry-over is older than the "
                      "squad's P85."})

    def test_status_when_every_planned_item_was_removed(self):
        f = {"planned": [1, 2], "delivered_planned": [], "removed": [1, 2]}
        self.assertEqual(_status(f, [], None, {}, None)["reason"],
                         "All planned work items were removed from the sprint; no carry-over is "
                         "older than the squad's P85.")

    def test_status_without_planned_or_old_carry(self):
        b = board()
        b.item(1).at("2026-10-29 09:00", iteration=S29, assigned=MAYA)
        self.assertEqual(run(b, "ALFA", "S29")["status"], {
            "colour": "GREEN", "manual": False,
            "reason": "There were no planned work items; no carry-over is older than the "
                      "squad's P85."})


if __name__ == "__main__":
    unittest.main()
