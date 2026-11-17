import json
import os
import tempfile
import unittest

from ledger.config import load_config
from ledger.replay import History
from tests.test_metrics import board, run, config, MAYA, S28, S29


class FirstPassTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.cfg.test_states = ["Ready for QA", "In Review"]

    def test_return_from_test_state_counts(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Ready for QA") \
            .at("2026-10-29 09:00", state="Active").at("2026-11-04 09:00", state="Closed")
        t = run(b, "ALFA", "S29", cfg=self.cfg)
        self.assertEqual(t["first_pass"], {"scored": 1, "returned": [1], "share": 0.0,
                                          "tracked": True})
        self.assertTrue(all("first_pass" not in p for p in t["people"]))

    def test_forward_and_between_test_states_do_not_count(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Ready for QA") \
            .at("2026-10-29 09:00", state="In Review").at("2026-11-04 09:00", state="Closed")
        fp = run(b, "ALFA", "S29", cfg=self.cfg)["first_pass"]
        self.assertEqual((fp["returned"], fp["share"]), ([], 1.0))

    def test_return_to_new_counts(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Ready for QA").at("2026-10-29 09:00", state="New") \
            .at("2026-10-30 09:00", state="Active").at("2026-11-04 09:00", state="Closed")
        self.assertEqual(run(b, "ALFA", "S29", cfg=self.cfg)["first_pass"]["returned"], [1])

    def test_test_state_to_removed_is_not_a_return(self):
        b = board()
        item = b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Ready for QA").at("2026-10-29 09:00", state="Removed")
        self.assertFalse(History(item.rows, self.cfg.not_delivered_reasons).returned(
            self.cfg.test_states))

    def test_change_inside_test_state_is_not_a_return(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Ready for QA") \
            .at("2026-10-29 09:00", assigned="orin@vendor.example") \
            .at("2026-11-04 09:00", state="Closed")
        self.assertEqual(run(b, "ALFA", "S29", cfg=self.cfg)["first_pass"]["returned"], [])

    def test_blocked_tag_never_counts(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", tags=["Blocked"]).at("2026-10-29 09:00", tags=[]) \
            .at("2026-11-04 09:00", state="Closed")
        self.assertEqual(run(b, "ALFA", "S29", cfg=self.cfg)["first_pass"]["returned"], [])

    def test_reopen_after_sprint_counts_against_credit_sprint(self):
        b = board().sprint("S30", "2026-11-10", "2026-11-21")
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-04 09:00", state="Closed") \
            .at("2026-11-12 09:00", state="Active", iteration="Proj\\S30") \
            .at("2026-11-14 09:00", state="Closed")
        t = run(b, "ALFA", "S29", cfg=self.cfg)
        self.assertEqual((t["delivered"], t["first_pass"]["returned"]), ([1], [1]))

    def test_item_counted_once(self):
        b = board()
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Ready for QA") \
            .at("2026-10-29 09:00", state="Active").at("2026-10-30 09:00", state="In Review") \
            .at("2026-11-02 09:00", state="Active").at("2026-11-04 09:00", state="Closed")
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-04 09:00", state="Closed")
        fp = run(b, "ALFA", "S29", cfg=self.cfg)["first_pass"]
        self.assertEqual((fp["returned"], fp["share"]), ([2], 0.5))

    def test_not_tracked_without_returns_in_window(self):
        b = board()
        b.item(1).at("2026-10-13 09:00", iteration=S28, assigned=MAYA, state="Active") \
            .at("2026-10-21 09:00", state="Closed")
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-04 09:00", state="Closed")
        fp = run(b, "ALFA", "S29", cfg=self.cfg)["first_pass"]
        self.assertIs(fp["tracked"], False)
        self.assertEqual(fp["share"], 1.0)

    def test_tracked_by_return_in_earlier_window_sprint(self):
        b = board()
        b.item(1).at("2026-10-13 09:00", iteration=S28, assigned=MAYA, state="Active") \
            .at("2026-10-14 09:00", state="Ready for QA") \
            .at("2026-10-15 09:00", state="Active").at("2026-10-21 09:00", state="Closed")
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-04 09:00", state="Closed")
        t = run(b, "ALFA", "S29", cfg=self.cfg)
        self.assertIs(t["first_pass"]["tracked"], True)
        self.assertEqual(t["first_pass"]["share"], 1.0)
        self.assertEqual([e["first_pass"] for e in t["trend"]], [0.0, 1.0])

    def test_nothing_delivered_share_none(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active")
        t = run(b, "ALFA", "S29", cfg=self.cfg)
        self.assertEqual(t["first_pass"]["scored"], 0)
        self.assertIsNone(t["first_pass"]["share"])
        self.assertIsNone(t["trend"][-1]["first_pass"])

    def test_empty_test_states_counts_reopens_only(self):
        self.cfg.test_states = []
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Ready for QA") \
            .at("2026-10-29 09:00", state="Active").at("2026-11-04 09:00", state="Closed")
        b.item(2).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Closed").at("2026-10-29 09:00", state="Active") \
            .at("2026-11-04 09:00", state="Closed")
        self.assertEqual(run(b, "ALFA", "S29", cfg=self.cfg)["first_pass"]["returned"], [2])

    def test_cut_item_not_scored(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-11-04 09:00", state="Closed", reason="Cut")
        self.assertEqual(run(b, "ALFA", "S29", cfg=self.cfg)["first_pass"]["scored"], 0)

    def test_reactivation_after_not_delivered_close_is_not_returned(self):
        b = board()
        b.item(1).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active") \
            .at("2026-10-28 09:00", state="Closed", reason="Cut") \
            .at("2026-10-29 09:00", state="Active") \
            .at("2026-11-04 09:00", state="Closed", reason="Fixed")
        t = run(b, "ALFA", "S29", cfg=self.cfg)
        self.assertEqual(t["delivered"], [1])
        self.assertEqual(t["first_pass"]["returned"], [])
        self.assertFalse(t["first_pass"]["tracked"])

    def test_config_without_test_states_loads(self):
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "config.json"), "w", encoding="utf-8") as f:
                json.dump({"timezone": "Europe/Zurich"}, f)
            self.assertEqual(load_config(directory).test_states, [])

    def test_reopen_categories(self):
        for state, category, expected in (("New", "Proposed", True),
                                          ("Active", "InProgress", True),
                                          ("Resolved", "Resolved", True),
                                          ("Removed", "Removed", False),
                                          ("Done", "Completed", False)):
            with self.subTest(category=category):
                item = board().item(1).at("2026-10-27 09:00", iteration=S29,
                                          assigned=MAYA, state="Active") \
                    .at("2026-10-28 09:00", state="Closed") \
                    .at("2026-10-29 09:00", state=state, category=category)
                self.assertIs(History(item.rows, []).returned([]), expected)
