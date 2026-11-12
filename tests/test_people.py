import unittest

from ledger.people import person_observations
from ledger.phases import PhaseMapper
from ledger.testing.builder import PortfolioBuilder
from ledger.timeline import build_timeline
from ledger.workcal import WorkCalendar

SPRINTS = [
    ("S33", "2026-08-31", "2026-09-11"),
    ("S34", "2026-09-14", "2026-09-25"),
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
    ("S38", "2026-11-09", "2026-11-20"),
]
CUTOFF = "2026-11-07 08:00"


def make():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
    b.team("Pricing", area="Demo\\Pricing", sprints=SPRINTS)
    return b


def observe(b, pid="anna", team="Claims", sprint="S37"):
    cfg = b.config()
    roster = b.roster()
    cal = WorkCalendar(b.tz, [])
    snap = b.snapshot(cutoff=CUTOFF, team=team)
    mapper = PhaseMapper(cfg.team(team), cfg)
    cut = b.parse_time(CUTOFF)
    tls = {t.id: build_timeline(t, mapper, cut, set()) for t in snap.tickets}
    sps = b.sprints(team)
    idx = [s.name for s in sps].index(sprint)
    return person_observations(pid, team, sps[idx], tls, cal, roster, cfg,
                               sps[:idx + 1])


class PeopleTest(unittest.TestCase):
    def test_leave_reduces_available_days(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.absence("anna", "2026-10-26", "2026-10-29")
        o = observe(b)
        self.assertEqual(o["available_days"], 6.0)
        self.assertEqual(o["nominal_days"], 10.0)

    def test_part_time_allocation(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05",
                 allocation=0.5)
        o = observe(b)
        self.assertEqual(o["available_days"], 5.0)
        self.assertEqual(o["days_nothing_in_progress"], 5.0)

    def test_moved_teams_mid_sprint(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05",
                 end="2026-10-29")
        b.assign("anna", "Pricing", start="2026-10-30")
        self.assertEqual(observe(b)["available_days"], 4.0)
        self.assertEqual(observe(b, team="Pricing")["available_days"], 6.0)

    def test_days_nothing_in_progress(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.ticket(1, "Claims").on("2026-10-20 09:00", iteration="S37") \
            .on("2026-10-26 09:00", state="Active", assignee=anna) \
            .on("2026-10-28 17:00", state="Closed")
        for tid in (2, 3):
            b.ticket(tid, "Claims").on("2026-10-26 08:00", iteration="S37",
                                       assignee=anna)
        o = observe(b)
        self.assertEqual(o["days_nothing_in_progress"], 7.0)
        self.assertEqual(o["todo_assigned_max"], 2)

    def test_concurrency(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        for tid, start in ((1, "2026-10-26 09:00"), (2, "2026-10-27 09:00"),
                           (3, "2026-10-28 09:00")):
            b.ticket(tid, "Claims").on(start, iteration="S37",
                                       state="Active", assignee=anna) \
                .on("2026-10-29 12:00", state="Closed")
        o = observe(b)
        self.assertEqual(o["concurrency_max"], 3)
        # Mon 1, Tue 2, Wed 3, Thu 3, rest 0 over 10 days
        self.assertAlmostEqual(o["concurrency_avg"], 0.9)

    def test_pickup_median(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.ticket(1, "Claims").on("2026-10-27 09:00", iteration="S37",
                                 assignee=anna) \
            .on("2026-10-28 09:00", state="Active")          # 1.0
        b.ticket(2, "Claims").on("2026-10-27 09:00", iteration="S37",
                                 assignee=anna) \
            .on("2026-10-30 09:00", state="Active")          # 3.0
        b.ticket(3, "Claims").on("2026-10-27 09:00", iteration="S37",
                                 assignee=anna) \
            .on("2026-10-28 09:00", assignee=None) \
            .on("2026-10-29 09:00", state="Active")          # skipped
        self.assertEqual(observe(b)["pickup_median"], 2.0)

    def test_pickup_median_none_without_samples(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        self.assertIsNone(observe(b)["pickup_median"])

    def test_board_change_days_and_last(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.ticket(1, "Claims").on("2026-10-27 09:00", iteration="S37",
                                 by=anna) \
            .on("2026-10-27 15:00", state="Active", by=anna) \
            .on("2026-10-29 09:00", state="In QA", by=anna) \
            .on("2026-10-31 10:00", state="Closed", by=anna) \
            .on("2026-11-02 10:00", by=b.other("sm"))
        o = observe(b)
        self.assertEqual(o["board_change_days"], 3)
        self.assertEqual(o["last_board_change"], "2026-10-31")

    def test_joiner(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-10-26")
        b.ticket(1, "Claims").on("2026-10-26 09:00", iteration="S37",
                                 state="Active", assignee=anna) \
            .on("2026-11-02 00:00", state="Closed")
        o = observe(b)
        self.assertTrue(o["is_joiner"])
        self.assertEqual(o["joiner_days_to_first_delivery"], 5.0)

    def test_joiner_without_delivery_is_none(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-10-26")
        o = observe(b)
        self.assertTrue(o["is_joiner"])
        self.assertIsNone(o["joiner_days_to_first_delivery"])

    def test_joiner_not_applicable_before_window(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.ticket(1, "Claims").on("2026-10-26 09:00", iteration="S37",
                                 state="Active", assignee=anna) \
            .on("2026-11-02 00:00", state="Closed")
        o = observe(b)
        self.assertFalse(o["is_joiner"])
        self.assertIn("joiner_days_to_first_delivery", o)
        self.assertIsNone(o["joiner_days_to_first_delivery"])

    def test_comparison_break(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05",
                 end="2026-10-25")
        b.assign("anna", "Claims", start="2026-10-26", allocation=0.5)
        self.assertTrue(observe(b)["comparison_break"])
        self.assertFalse(observe(b, sprint="S36")["comparison_break"])
        self.assertFalse(observe(b, sprint="S33")["comparison_break"])

    def test_comparison_break_on_joining_team(self):
        b = make()
        b.person("anna", "Anna K.", team="Pricing", start="2026-01-05")
        b.assign("anna", "Claims", start="2026-10-26")
        self.assertTrue(observe(b)["comparison_break"])

    def test_absent_whole_sprint(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.absence("anna", "2026-10-26", "2026-11-06")
        o = observe(b)
        self.assertEqual(o["available_days"], 0.0)
        self.assertEqual(o["days_nothing_in_progress"], 0.0)
        self.assertEqual(o["concurrency_avg"], 0.0)
        self.assertEqual(o["nominal_days"], 10.0)


if __name__ == "__main__":
    unittest.main()
