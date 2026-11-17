import unittest
from datetime import date, timedelta

from ledger.config import Notes, Person
from ledger.metrics import _Team, team_sprint
from tests.fixture import Board
from tests.test_metrics import config

ADA = "ada@vendor.example"
PO = "po@customer.example"


class BuildTimeTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.cfg.test_states = ["Ready for QA"]
        self.cfg.waiting_states = ["Ready for QA", "PO Review"]
        self.cfg.hold_states = ["On Hold"]
        self.board = Board()

    def team(self):
        return _Team(self.board.cache(), self.cfg,
                     [Person("Ada Vale", [ADA], "ALFA", None, None)], "ALFA")

    def test_assignment_while_proposed_does_not_start_the_clock(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA) \
            .at("2026-11-04 09:00", state="Active") \
            .at("2026-11-06 09:00", state="Ready for QA") \
            .at("2026-11-09 09:00", state="Closed")
        team = self.team()
        h = team.hist[1]
        self.assertEqual(team.build_span(h), (h.times[1], h.times[2]))
        self.assertEqual(team.build_seconds(h), 2 * 86400)

    def test_active_before_ours_is_not_build_time(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=PO, state="Active") \
            .at("2026-11-03 09:00", assigned=ADA) \
            .at("2026-11-05 09:00", state="Ready for QA") \
            .at("2026-11-09 09:00", state="Closed")
        team = self.team()
        self.assertEqual(team.build_seconds(team.hist[1]), 2 * 86400)

    def test_story_starts_when_child_task_is_ours(self):
        self.board.item(1).at("2026-11-02 09:00", state="Active") \
            .at("2026-11-04 09:00", state="Ready for QA") \
            .at("2026-11-09 09:00", state="Closed")
        self.board.item(2, "Task", parent=1).at("2026-11-03 09:00", assigned=ADA)
        team = self.team()
        self.assertEqual(team.build_seconds(team.hist[1]), 1 * 86400)

    def test_handoff_falls_back_to_done_without_test_states(self):
        self.cfg.test_states = []
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Active") \
            .at("2026-11-05 09:00", state="Closed")
        team = self.team()
        self.assertEqual(team.build_seconds(team.hist[1]), 3 * 86400)

    def test_hold_and_blocked_inside_span_are_excluded(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Active") \
            .at("2026-11-03 09:00", state="On Hold") \
            .at("2026-11-04 09:00", state="Active") \
            .at("2026-11-05 09:00", tags=["Blocked"]) \
            .at("2026-11-06 09:00", tags=[]) \
            .at("2026-11-07 09:00", state="Ready for QA") \
            .at("2026-11-09 09:00", state="Closed")
        team = self.team()
        self.assertEqual(team.build_seconds(team.hist[1]), 3 * 86400)

    def test_rework_after_return_is_not_build_time(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Active") \
            .at("2026-11-03 09:00", state="Ready for QA") \
            .at("2026-11-04 09:00", state="Active") \
            .at("2026-11-06 09:00", state="Ready for QA") \
            .at("2026-11-07 09:00", state="Closed")
        team = self.team()
        h = team.hist[1]
        self.assertEqual(team.build_seconds(h), 86400)
        self.assertEqual(team.time_split(h), (86400, 2 * 86400, 2 * 86400))
        self.assertEqual(sum(team.time_split(h)), (h.done_at - h.first_active).total_seconds())

    def test_not_measured_without_build_start(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Ready for QA") \
            .at("2026-11-05 09:00", state="Closed")
        self.board.item(2).at("2026-11-02 09:00", assigned=ADA) \
            .at("2026-11-05 09:00", state="Closed")
        team = self.team()
        for i in (1, 2):
            with self.subTest(item=i):
                self.assertIsNone(team.build_span(team.hist[i]))
                self.assertIsNone(team.build_seconds(team.hist[i]))
        self.assertEqual(team.time_split(team.hist[1]), (0, 3 * 86400, 0))
        self.assertIsNone(team.time_split(team.hist[2]))

    def test_waiting_states_seen_in_config_order(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Active") \
            .at("2026-11-03 09:00", state="PO Review") \
            .at("2026-11-04 09:00", state="Ready for QA") \
            .at("2026-11-05 09:00", state="Closed")
        team = self.team()
        self.assertEqual(team.waiting_states_seen(team.hist[1]), ["Ready for QA", "PO Review"])

    def test_open_item_has_no_timing(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Active")
        team = self.team()
        h = team.hist[1]
        self.assertIsNone(team.build_span(h))
        self.assertIsNone(team.build_seconds(h))
        self.assertIsNone(team.time_split(h))
        self.assertEqual(team.waiting_states_seen(h), [])

    def test_delivered_close_caps_handoff_before_later_test_entry(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Resolved") \
            .at("2026-11-03 09:00", state="Closed") \
            .at("2026-11-04 09:00", state="Active") \
            .at("2026-11-05 09:00", state="Ready for QA")
        team = self.team()
        self.assertEqual(team.build_seconds(team.hist[1]), 86400)
        self.assertEqual(team.waiting_states_seen(team.hist[1]), [])

    def test_build_start_after_delivered_close_is_not_measured(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA) \
            .at("2026-11-03 09:00", state="Closed") \
            .at("2026-11-04 09:00", state="Active") \
            .at("2026-11-05 09:00", state="Ready for QA")
        team = self.team()
        self.assertIsNone(team.build_span(team.hist[1]))
        self.assertIsNone(team.time_split(team.hist[1]))

    def test_reassignment_away_does_not_stop_build_time(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Active") \
            .at("2026-11-03 09:00", assigned=PO) \
            .at("2026-11-05 09:00", state="Ready for QA") \
            .at("2026-11-06 09:00", state="Closed")
        team = self.team()
        self.assertEqual(team.build_seconds(team.hist[1]), 3 * 86400)

    def test_child_ownership_uses_parent_at_revision_time(self):
        self.board.item(1).at("2026-11-02 09:00", state="Active") \
            .at("2026-11-05 09:00", state="Ready for QA") \
            .at("2026-11-06 09:00", state="Closed")
        child = self.board.item(2, "Task").at("2026-11-02 09:00", assigned=ADA)
        child.parent = 1
        child.at("2026-11-03 09:00")
        child.parent = None
        child.at("2026-11-04 09:00")
        team = self.team()
        self.assertEqual(team.build_seconds(team.hist[1]), 2 * 86400)

    def test_counted_children_do_not_start_parent_build_time(self):
        self.board.item(1).at("2026-11-02 09:00", state="Active") \
            .at("2026-11-05 09:00", state="Closed")
        self.board.item(2, "Task", parent=1).at("2026-11-03 09:00", assigned=ADA)
        for count in ("tasks", ["User Story", "Task"]):
            with self.subTest(count=count):
                self.cfg.teams["ALFA"]["count"] = count
                team = self.team()
                self.assertIsNone(team.build_span(team.hist[1]))

    def test_waiting_hold_and_blocked_overlap_is_subtracted_once(self):
        self.board.item(1).at("2026-11-02 09:00", assigned=ADA, state="Active") \
            .at("2026-11-03 09:00", state="PO Review", tags=["Blocked"]) \
            .at("2026-11-04 09:00", state="On Hold") \
            .at("2026-11-05 09:00", state="Active", tags=[]) \
            .at("2026-11-06 09:00", state="Ready for QA") \
            .at("2026-11-07 09:00", state="Closed")
        team = self.team()
        h = team.hist[1]
        self.assertEqual(team.build_seconds(h), 2 * 86400)
        self.assertEqual(team.time_split(h), (2 * 86400, 3 * 86400, 0))


class BuildWindowTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.cfg.timezone = "UTC"
        self.cfg.test_states = ["Ready for QA"]
        self.cfg.waiting_states = ["Ready for QA", "PO Review", "Unused"]
        self.people = [Person("Ada Vale", [ADA], "ALFA", None, None),
                       Person("Eli Moss", ["eli@vendor.example"], "ALFA", None, None)]
        self.board = Board("UTC")
        for k in range(7):
            start = date(2026, 1, 12) + timedelta(days=14 * k)
            self.board.sprint("S%d" % k, str(start), str(start + timedelta(days=11)))
            # An open item establishes team history without adding any delivered work.
            self.board.item(100 + k).at(str(start) + " 09:00", assigned=ADA,
                                       iteration=self.board.sprints[-1]["path"])

    def deliver(self, i, sprint, days=1):
        s = self.board.sprints[sprint]
        start = date.fromisoformat(s["start"])
        return self.board.item(i).at(str(start) + " 09:00", assigned=ADA,
                                     iteration=s["path"], state="Active") \
            .at(str(start + timedelta(days=days)) + " 09:00", state="Ready for QA") \
            .at(str(start + timedelta(days=days + 1)) + " 09:00", state="Closed")

    def result(self, sprint=6, days_off=None):
        return team_sprint(self.board.cache(), self.cfg, self.people, "ALFA",
                           self.board.sprints[sprint], Notes(), days_off or {})

    def test_windows_split_last_six_sprints(self):
        self.assertEqual(self.result()["windows"],
                         {"now": ["S4", "S5", "S6"], "before": ["S1", "S2", "S3"]})

    def test_build_median_pooled_over_now_window(self):
        for i, s, days in ((1, 0, 10), (2, 1, 5), (3, 3, 7),
                           (4, 4, 1), (5, 5, 2), (6, 6, 4)):
            self.deliver(i, s, days)
        self.assertEqual(self.result()["build"], {"median": 2.0, "n": 3, "before": 6.0})

    def test_build_uses_even_median_without_rounding_and_skips_unmeasured(self):
        self.deliver(1, 5, 1)
        s = self.board.sprints[6]
        self.board.item(2).at(s["start"] + " 09:00", iteration=s["path"], assigned=ADA,
                              state="Active").at(s["start"] + " 15:00", state="Closed")
        self.board.item(3).at(s["start"] + " 09:00", iteration=s["path"], assigned=ADA) \
            .at(s["end"] + " 09:00", state="Closed")
        self.assertEqual(self.result()["build"], {"median": 0.625, "n": 2, "before": None})

    def test_throughput_per_ten_person_days(self):
        for i, s in enumerate((4, 4, 5, 5, 6, 6), 1):
            self.deliver(i, s)
        self.deliver(7, 1)
        self.assertEqual(self.result()["throughput"], {"per10": 1.0, "before": 1 / 6})

    def test_throughput_none_with_zero_capacity(self):
        self.deliver(1, 6)
        off = {}
        for p in self.people:
            off[p.name] = {}
            for s in self.board.sprints[4:]:
                start = date.fromisoformat(s["start"])
                for d in range(12):
                    off[p.name][start + timedelta(days=d)] = 1.0
        self.assertIsNone(self.result(days_off=off)["throughput"]["per10"])

    def test_throughput_uses_allocation_join_leave_and_partial_leave(self):
        self.deliver(1, 6)
        self.people[0].allocation = 0.5
        self.people[0].joined = date(2026, 4, 6)
        self.people[1].left = date(2026, 4, 6)
        # Earlier two sprints: 20 PD. Last sprint: Ada's 5 PD minus half a day at 50%.
        result = self.result(days_off={"Ada Vale": {date(2026, 4, 6): 0.5}})
        self.assertAlmostEqual(result["throughput"]["per10"], 10 / 24.75)

    def test_no_before_window_with_three_sprints(self):
        self.board.sprints = self.board.sprints[:3]
        self.board.items = self.board.items[:3]
        result = self.result(2)
        self.assertEqual(result["windows"], {"now": ["S0", "S1", "S2"], "before": []})
        for key in ("build", "throughput", "first_pass"):
            self.assertIsNone(result[key]["before"])

    def test_flow_parts_add_up(self):
        self.deliver(1, 0, 10)  # Outside the six-sprint window.
        s = self.board.sprints[1]
        start = date.fromisoformat(s["start"])
        item = self.board.item(2).at(str(start) + " 09:00", assigned=ADA,
                                     iteration=s["path"], state="Active")
        for d, state in ((1, "PO Review"), (3, "Ready for QA"), (5, "Active"), (6, "Closed")):
            item.at(str(start + timedelta(days=d)) + " 09:00", state=state)
        self.deliver(3, 6, 1)
        flow = self.result()["flow"]
        self.assertEqual(flow, {"total": 3, "building": 25, "waiting": 62, "other": 13,
                                "states": ["Ready for QA", "PO Review"]})
        self.assertEqual(flow["building"] + flow["waiting"] + flow["other"], 100)
        self.assertTrue(all(flow[k] >= 0 for k in ("building", "waiting", "other")))

    def test_flow_even_split_in_percent(self):
        self.deliver(1, 6, 1)
        self.assertEqual(self.result()["flow"],
                         {"total": 3, "building": 50, "waiting": 50, "other": 0,
                          "states": ["Ready for QA"]})

    def test_no_measured_items_and_no_cycle(self):
        result = self.result()
        self.assertEqual(result["build"], {"median": None, "n": 0, "before": None})
        self.assertIsNone(result["flow"])
        self.assertEqual(result["throughput"], {"per10": 0.0, "before": 0.0})

    def test_flow_zero_elapsed_cycle_assigns_total_to_other(self):
        s = self.board.sprints[6]
        self.board.item(1).at(s["start"] + " 09:00", iteration=s["path"], assigned=ADA,
                              state="Active").at(s["start"] + " 09:00", state="Closed")
        self.assertEqual(self.result()["flow"],
                         {"total": 1, "building": 0, "waiting": 0, "other": 100, "states": []})

    def test_trend_keeps_per_sprint_build_and_throughput(self):
        self.deliver(1, 4, 1)
        self.deliver(2, 4, 4)
        self.deliver(3, 6, 2)
        trend = self.result()["trend"]
        self.assertEqual([s["build"] for s in trend], [None, None, None, 2.5, None, 2.0])
        self.assertEqual([s["per10"] for s in trend], [0.0, 0.0, 0.0, 1.0, 0.0, 0.5])

    def test_delivered_items_mark_late(self):
        self.deliver(1, 6, 1)
        self.deliver(2, 6, 12)  # Planned, but closes after the sprint ended.
        result = self.result()
        self.assertEqual([(i["id"], i["late"]) for i in result["delivered_items"]],
                         [(1, False), (2, True)])

    def test_first_pass_before_window_and_tracked_cover_all_six_sprints(self):
        item = self.deliver(1, 1, 1)
        start = date.fromisoformat(self.board.sprints[1]["start"])
        item.at(str(start + timedelta(days=3)) + " 09:00", state="Active")
        self.deliver(2, 6, 1)
        self.assertEqual(self.result()["first_pass"],
                         {"scored": 1, "returned": [], "share": 1.0,
                          "before": 0.0, "tracked": True})
