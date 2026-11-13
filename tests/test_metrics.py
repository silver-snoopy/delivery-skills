import unittest
from datetime import date

from ledger.config import Config, Notes, Person
from ledger.replay import parse_ts
from ledger.metrics import (histories, is_onboarding, level_of, percentile, team_sprint,
                            team_sprints)
from tests.fixture import Board

MAYA, IVAN, LENA, PO = ("maya@vendor.example", "ivan@vendor.example", "lena@vendor.example",
                        "po@customer.example")
S28, S29 = "Proj\\S28", "Proj\\S29"
PEOPLE = [Person("Maya Brook", [MAYA], "ALFA", None, None),
          Person("Ivan Holt", [IVAN], "ALFA", None, None),
          Person("Lena Ford", [LENA], "BETA", None, None)]


def board():
    return Board().sprint("S28", "2026-10-13", "2026-10-24").sprint("S29", "2026-10-27", "2026-11-07")


def config():
    return Config(timezone="Europe/Zurich", waiting_states=["Ready for QA"],
                  teams={"ALFA": {"count": "stories"}, "BETA": {"count": "tasks"}})


def run(b, team, name, people=PEOPLE, notes=None, days_off=None):
    data = b.cache()
    sprint = next(s for s in data["iterations"] if s["name"] == name)
    return team_sprint(data, config(), people, team, sprint, notes or Notes(), days_off or {})


class SprintFigureTests(unittest.TestCase):
    def test_planned_delivered_added_removed(self):
        b = board()
        for i in (101, 102, 103):
            b.item(i).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
                .at("2026-11-0%d 10:00" % (i - 98), state="Closed")
        b.item(104).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-03 10:00", iteration="Proj")
        b.item(105).at("2026-10-27 09:00", iteration=S29, assigned=IVAN, state="Active")
        b.item(106).at("2026-10-29 09:00", iteration=S29, assigned=IVAN, state="Active")
        t = run(b, "ALFA", "S29")
        self.assertEqual((len(t["planned"]), len(t["delivered_planned"]), len(t["added"]),
                          len(t["removed"])), (5, 3, 1, 1))
        self.assertEqual(t["planned"], [101, 102, 103, 104, 105])
        self.assertEqual((t["added"], t["removed"], t["delivered"]), ([106], [104], [101, 102, 103]))
        self.assertEqual([c["id"] for c in t["carry"]], [105])
        self.assertEqual((t["team"], t["count"], t["sprint"]["name"]), ("ALFA", "stories", "S29"))

    def test_child_task_assignee_makes_story_ours(self):
        b = board()
        b.item(201).at("2026-10-27 09:00", iteration=S29, assigned=PO) \
            .at("2026-10-28 09:00", state="Active").at("2026-11-05 09:00", state="Closed")
        b.item(202, "Task", parent=201).at("2026-10-27 09:00", iteration=S29, assigned=MAYA) \
            .at("2026-10-28 09:00", state="Active").at("2026-11-04 09:00", state="Closed")
        b.item(203).at("2026-10-27 09:00", iteration=S29, assigned=PO) \
            .at("2026-10-28 09:00", state="Active").at("2026-11-05 09:00", state="Closed")
        t = run(b, "alfa", "S29")
        self.assertEqual((t["planned"], t["delivered"]), ([201], [201]))

    def test_tasks_team_counts_tasks_only(self):
        b = board()
        b.item(301, title="Claims upload").at("2026-10-27 09:00", iteration=S29, assigned=LENA,
                                               state="Active")
        for i in (302, 303, 304):
            b.item(i, "Task", parent=301).at("2026-10-27 09:00", iteration=S29, assigned=LENA,
                                             state="Active").at("2026-11-04 09:00", state="Closed")
        t = run(b, "BETA", "S29")
        self.assertEqual((t["planned"], t["delivered"]), ([302, 303, 304], [302, 303, 304]))
        self.assertEqual(t["worked_on"], [["Claims upload", 3]])

    def test_credit_by_completion_date_and_late(self):
        b = board()
        b.item(401).at("2026-10-13 09:00", iteration=S28, assigned=MAYA) \
            .at("2026-10-15 09:00", state="Active").at("2026-10-28 09:00", state="Closed")
        s28, s29 = run(b, "ALFA", "S28"), run(b, "ALFA", "S29")
        self.assertEqual((s28["planned"], s28["delivered"], s28["late"]), ([401], [], [401]))
        self.assertEqual((s29["planned"], s29["delivered"], s29["late"]), ([], [401], []))
        self.assertEqual(s29["cycle"], {"n": 1, "median": 14, "p85": 14})

    def test_cycle_time_nearest_rank_and_closed_without_active(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.85), 9)
        self.assertEqual(percentile([4, 1, 3], 0.5), 3)
        self.assertEqual(percentile([7] * 20, 0.85), 7)
        self.assertIsNone(percentile([], 0.85))
        b = board()
        b.item(501).at("2026-10-27 09:00", iteration=S29, assigned=MAYA) \
            .at("2026-11-02 09:00", state="Closed")
        b.item(502).at("2026-10-27 09:00", iteration=S29, assigned=MAYA) \
            .at("2026-10-28 09:00", state="Active").at("2026-10-29 09:00", state="Ready for QA") \
            .at("2026-10-30 09:00", state="Closed")
        t = run(b, "ALFA", "S29")
        self.assertEqual(t["delivered"], [501, 502])
        self.assertEqual(t["cycle"], {"n": 1, "median": 3, "p85": 3})
        self.assertAlmostEqual(t["waiting_share"], 0.5)

    def test_zero_planned_sprint(self):
        b = board()
        b.item(1, "Task").at("2026-10-27 09:00", iteration=S29, assigned=LENA, state="Active")
        t = run(b, "ALFA", "S29")
        self.assertEqual((t["planned"], t["delivered"], t["carry"], t["worked_on"]), ([], [], [], []))
        self.assertEqual(t["cycle"], {"n": 0, "median": None, "p85": None})
        self.assertIsNone(t["waiting_share"])
        self.assertEqual(t["notes"], {"summary": "", "note": "", "exec_note": ""})

    def test_carry_over_age_and_sprints(self):
        b = board()
        b.item(601, parent=900).at("2026-10-13 09:00", iteration=S28, assigned=MAYA) \
            .at("2026-10-15 09:00", state="Active").at("2026-10-26 09:00", iteration=S29) \
            .at("2026-11-04 09:00", state="Ready for QA")
        b.item(602).at("2026-10-27 09:00", iteration=S29, assigned=IVAN)
        notes = Notes(items={601: ("agreed", "QA environment down")},
                      summary={"Alfa": "Upload work"}, exec_note={"ALFA": "On track"})
        t = run(b, "ALFA", "S29", notes=notes)
        self.assertEqual(t["carry"], [
            {"id": 601, "title": "Item 601", "age": 24, "sprints": 2, "state": "Ready for QA",
             "waiting": True, "agreed": "agreed", "reason": "QA environment down"},
            {"id": 602, "title": "Item 602", "age": None, "sprints": 1, "state": "New",
             "waiting": False, "agreed": "unknown", "reason": ""}])
        self.assertEqual(run(b, "ALFA", "S28")["carry"][0]["age"], 10)
        self.assertEqual(t["notes"], {"summary": "Upload work", "note": "", "exec_note": "On track"})
        self.assertEqual(t["worked_on"], [["Other", 1], ["Parent 900", 1]])
        self.assertEqual([(r["sprint"], r["planned"]) for r in t["trend"]], [("S28", 1), ("S29", 2)])

    def test_capacity_join_date_leave_allocation(self):
        b = board()
        b.item(1).at("2026-10-14 09:00", iteration=S28, assigned=MAYA, state="Active")
        b.item(2).at("2026-10-28 09:00", iteration=S29, assigned=MAYA, state="Active")
        people = [Person("Maya Brook", [MAYA], "ALFA", date(2026, 1, 5), None),
                  Person("Nadia Kern", ["nadia@vendor.example"], "ALFA", date(2026, 10, 29), None, 0.5),
                  Person("Old Timer", ["old@vendor.example"], "ALFA", None, date(2026, 10, 20)),
                  Person("Lena Ford", [LENA], "BETA", None, None)]
        off = {"Nadia Kern": {date(2026, 11, 3): 1.0, date(2026, 11, 7): 1.0},
               "Maya Brook": {date(2026, 10, 28): 0.5}}
        t = run(b, "ALFA", "S29", people=people, days_off=off)
        # S29 has 9 working days (Tue 27 Oct .. Fri 6 Nov); Nadia works 7 of them at 0.5
        self.assertEqual(t["capacity"], {"engineers": 2, "onboarding": 1,
                                         "nominal": 12.5, "available": 11.5})

    def test_is_onboarding(self):
        sprints = [{"path": "P\\%d" % i, "name": str(i), "start": s, "end": e} for i, (s, e) in
                   enumerate([("2026-10-13", "2026-10-24"), ("2026-10-27", "2026-11-07"),
                              ("2026-11-10", "2026-11-21")])]
        p = Person("Nadia Kern", [], "ALFA", date(2026, 10, 20), None)
        self.assertEqual([is_onboarding(p, sprints, s, 2) for s in sprints], [True, True, False])
        p.joined = date(2026, 10, 25)  # between sprints: the next sprint is the first
        self.assertEqual([is_onboarding(p, sprints, s, 2) for s in sprints], [False, True, True])
        p.joined = date(2026, 1, 5)  # long before the first known sprint
        self.assertFalse(is_onboarding(p, sprints, sprints[0], 2))
        p.joined = None
        self.assertFalse(is_onboarding(p, sprints, sprints[0], 2))

    def test_reopened_listed_not_credited_twice(self):
        b = board()
        b.item(701).at("2026-10-13 09:00", iteration=S28, assigned=MAYA, state="Active") \
            .at("2026-10-20 09:00", state="Closed").at("2026-10-26 09:00", iteration=S29) \
            .at("2026-10-28 09:00", state="Active", reason="Reopened") \
            .at("2026-11-02 09:00", state="Closed", reason="Done")
        s28, s29 = run(b, "ALFA", "S28"), run(b, "ALFA", "S29")
        self.assertEqual((s28["delivered"], s28["reopened"]), ([701], []))
        self.assertEqual((s29["delivered"], s29["planned"], s29["added"], s29["reopened"]),
                         ([], [], [], [701]))
        self.assertEqual(s28["cycle"]["median"], 8)

    def test_bug_level_rule(self):
        b = board()
        b.item(800).at("2026-10-27 09:00", iteration=S29, assigned=PO)
        b.item(801, "Bug", parent=800).at("2026-10-27 09:00", iteration=S29, assigned=LENA,
                                          state="Active").at("2026-11-02 09:00", state="Closed")
        b.item(802, "Bug").at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-02 09:00", state="Closed")
        data = b.cache()
        hist = histories(data, [])
        self.assertEqual(level_of(hist[801], data["levels"], hist), "tasks")
        self.assertEqual(level_of(hist[802], data["levels"], hist), "stories")
        self.assertEqual(level_of(hist[800], data["levels"], hist), "stories")
        self.assertEqual(run(b, "BETA", "S29")["delivered"], [801])
        self.assertEqual(run(b, "ALFA", "S29")["delivered"], [802])

    def test_cut_during_sprint_is_removed(self):
        b = board()
        b.item(901).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-03 10:00", state="Closed", reason="Cut")
        t = run(b, "ALFA", "S29")
        self.assertEqual((t["planned"], t["removed"], t["delivered_planned"], t["carry"]),
                         ([901], [901], [], []))

    def test_moved_to_next_sprint_before_the_end_is_a_carry_over(self):
        def sprint(done):
            b = board().sprint("S30", "2026-11-10", "2026-11-21")
            for i in range(1, 11):
                it = b.item(i).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active")
                if i <= done:
                    it.at("2026-11-04 09:00", state="Closed")
                else:  # moved to the next sprint the day before the sprint ends
                    it.at("2026-11-06 15:00", iteration="Proj\\S30")
            return run(b, "ALFA", "S29"), run(b, "ALFA", "S30")
        t, nxt = sprint(8)
        self.assertEqual((len(t["planned"]), len(t["delivered_planned"]), t["removed"]),
                         (10, 8, []))
        self.assertEqual([c["id"] for c in t["carry"]], [9, 10])
        self.assertTrue(t["status"]["reason"].startswith("80% of planned work items delivered;"))
        self.assertEqual(t["status"]["colour"], "AMBER")  # the old rule gave 8 of 8, GREEN
        self.assertEqual([c["sprints"] for c in nxt["carry"]], [2, 2])
        t, _ = sprint(7)
        self.assertEqual(t["status"]["colour"], "RED")
        self.assertTrue(t["status"]["reason"].startswith("70% of planned work items delivered;"))

    def test_moved_to_backlog_or_cut_is_removed(self):
        b = board().sprint("PI 4", "2026-10-12", "2026-12-18", path="Proj\\PI 4")
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-03 09:00", iteration="Proj\\PI 4")
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-03 09:00", state="Closed", reason="won't fix")
        b.item(3).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-03 09:00", state="Closed", reason="Obsolete")
        b.item(4).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-03 09:00", state="Removed")
        t = run(b, "ALFA", "S29")
        self.assertEqual((t["planned"], t["removed"], t["delivered"], t["carry"]),
                         ([1, 2, 3, 4], [1, 2, 3, 4], [], []))

    def test_long_iterations_are_not_sprints(self):
        b = board().sprint("PI 4", "2026-10-12", "2026-12-18", path="Proj\\PI 4") \
            .sprint("Six weeks", "2026-09-01", "2026-10-12", path="Proj\\Six weeks")
        b.item(1).at("2026-10-13 09:00", iteration="Proj\\PI 4", assigned=MAYA) \
            .at("2026-10-27 09:00", iteration=S29)
        b.item(2).at("2026-09-02 09:00", iteration="Proj\\Six weeks", assigned=MAYA)
        names = [s["name"] for s in team_sprints(b.cache(), config(), PEOPLE, "ALFA")]
        self.assertEqual(names, ["Six weeks", "S29"])

    def test_team_sprints(self):
        b = board().sprint("S30", "2026-11-10", "2026-11-21")
        b.item(1).at("2026-10-13 09:00", iteration=S28, assigned=PO)
        b.item(2, "Task", parent=1).at("2026-10-13 09:00", iteration=S28, assigned=LENA)
        b.item(3).at("2026-10-27 09:00", iteration=S29, assigned=MAYA)
        b.item(4).at("2026-10-13 09:00", iteration=S28, assigned=PO) \
            .at("2026-11-12 09:00", assigned=MAYA)
        data = b.cache()
        names = lambda team, until=None: [s["name"] for s in
                                          team_sprints(data, config(), PEOPLE, team, until)]
        self.assertEqual(names("beta"), ["S28"])
        self.assertEqual(names("ALFA"), ["S28", "S29"])
        self.assertEqual(names("ALFA", parse_ts("2026-11-08T00:00:00Z")), ["S29"])

    def test_rerun_is_identical(self):
        def build(later):
            b = board().sprint("S27", "2026-09-28", "2026-10-09")
            one = b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
                .at("2026-11-03 09:00", state="Closed")
            two = b.item(2, area="Proj\\Pay").at("2026-10-13 09:00", iteration=S28,
                                                  assigned=IVAN, state="Active") \
                .at("2026-10-26 09:00", iteration=S29)
            b.item(10).at("2026-10-27 09:00", iteration=S29, assigned=PO) \
                .at("2026-10-28 09:00", state="Active").at("2026-11-05 09:00", state="Closed")
            child = b.item(11, "Task", parent=10).at("2026-10-27 09:00", iteration=S29,
                                                     assigned=MAYA, state="Active") \
                .at("2026-11-04 09:00", state="Closed")
            old = b.item(20).at("2026-09-28 09:00", iteration="Proj\\S27", assigned=PO,
                                state="Active").at("2026-10-08 09:00", state="Closed")
            b.item(21, area="Proj\\Pay").at("2026-10-28 09:00", iteration=S29, assigned=PO,
                                              state="Active") \
                .at("2026-10-30 09:00", state="Closed")
            if later:  # all after S29 ended
                one.type = "Task"
                one.at("2026-11-10 09:00")
                child.parent = 12
                child.at("2026-11-10 09:00")
                old.at("2026-11-10 09:00", assigned=MAYA)
                two.at("2026-11-12 09:00", state="Closed")
                one.at("2026-11-11 09:00", assigned="tom.h2@vendor.example")
                for n in range(3):  # would move Ivan's main area and his assignment
                    b.item(30 + n, area="Proj\\Other").at("2026-11-10 09:00", iteration=S29,
                                                         assigned=IVAN, state="Active")
                b.item(40).at("2026-11-09 09:00", iteration=S29, assigned=PO, state="Active") \
                    .at("2026-11-10 09:00", state="Closed")
            return b
        first = run(build(False), "ALFA", "S29")
        self.assertEqual(first, run(build(False), "ALFA", "S29"))
        self.assertEqual((first["delivered"], [r["sprint"] for r in first["trend"]]),
                         ([1, 10], ["S28", "S29"]))
        self.assertEqual(first["rest"]["delivered"], 1)
        self.assertEqual([p["delivered"] for p in first["people"]], [[], [1, 10]])
        self.assertEqual([a["level"] for a in first["attention"]], ["red"] + ["amber"] * 3)
        self.assertEqual(first["status"]["colour"], "RED")
        again = run(build(True), "ALFA", "S29")
        self.assertEqual((first["late"], again["late"]), ([], [2]))
        again["late"] = first["late"]
        self.assertEqual(first, again)


if __name__ == "__main__":
    unittest.main()
