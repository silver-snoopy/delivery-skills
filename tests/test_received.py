import unittest

from ledger.phases import PhaseMapper
from ledger.received import received_state
from ledger.testing.builder import PortfolioBuilder
from ledger.timeline import build_timeline
from ledger.workcal import WorkCalendar

SPRINTS = [
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
]
TEAM = "Pricing"
CUTOFF = "2026-11-07 08:00"
CAL = WorkCalendar("Europe/Zurich", [])
CRITERIA = "Given a quote, when the rate changes, then the total updates."


def make():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team(TEAM, area="Demo\\Pricing", sprints=SPRINTS)
    anna = b.person("anna", "Anna K.", team=TEAM, start="2026-01-05")
    return b, anna


def state(b, sprint="S37"):
    cfg = b.config()
    t = {x.id: x for x in b.snapshot(cutoff=CUTOFF).tickets}[1]
    tl = build_timeline(t, PhaseMapper(cfg.team(TEAM), cfg),
                        b.parse_time(CUTOFF), set())
    sp = {s.name: s for s in b.sprints(TEAM)}[sprint]
    return received_state(t, tl, sp, CAL, TEAM, b.roster(), cfg)


class ReceivedStateTest(unittest.TestCase):
    def test_no_criteria_measured_at_vendor_receipt(self):
        b, anna = make()
        po = b.other("po")
        (b.ticket(1, TEAM)
         .on("2026-10-26 09:00", state="Active", assignee=b.other("dev-x"),
             iteration="S37")
         .edit("2026-10-27 09:00", acceptance=CRITERIA, by=po)
         .on("2026-10-28 09:00", assignee=anna))
        r = state(b)
        self.assertEqual(r["received_at"],
                         b.parse_time("2026-10-28 09:00").isoformat())
        self.assertFalse(r["no_criteria"])
        self.assertEqual(r["text_changes_after"], 0)

    def test_no_criteria_true_and_text_changed_after(self):
        b, anna = make()
        po = b.other("po")
        (b.ticket(1, TEAM, description="Fix rates")
         .on("2026-10-26 09:00", state="Active", assignee=anna,
             iteration="S37")
         .edit("2026-10-27 09:00", acceptance=CRITERIA, by=po)
         .on("2026-10-27 10:00", state="In QA", by=po)
         .edit("2026-10-28 09:00", description="Fix rates in quotes", by=po)
         .edit("2026-10-29 09:00", description="Fix all rates",
               by=b.service("sync")))
        r = state(b)
        self.assertTrue(r["no_criteria"])
        self.assertEqual(r["text_changes_after"], 2)

    def test_long_description_is_criteria(self):
        b, anna = make()
        b.ticket(1, TEAM, description="x" * 40).on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37")
        self.assertFalse(state(b)["no_criteria"])

    def test_assigned_late(self):
        for day, late in (("2026-11-05", True), ("2026-11-02", False)):
            b, anna = make()
            (b.ticket(1, TEAM)
             .on("2026-10-26 09:00", state="New", iteration="S37",
                 assignee=None)
             .on(day + " 09:00", assignee=anna))
            r = state(b)
            self.assertEqual(r["assigned_late"], late, day)
            self.assertFalse(r["added_mid_sprint"], day)

    def test_added_mid_sprint(self):
        b, anna = make()
        (b.ticket(1, TEAM)
         .on("2026-10-26 09:00", state="New", assignee=anna)
         .on("2026-10-29 09:00", iteration="S37"))
        self.assertTrue(state(b)["added_mid_sprint"])

    def test_received_before_sprint_is_not_assigned_late(self):
        b, anna = make()
        b.ticket(1, TEAM).on("2026-10-14 09:00", state="Active",
                             assignee=anna, iteration="S36")
        self.assertFalse(state(b, "S36")["assigned_late"])
        self.assertFalse(state(b, "S37")["assigned_late"])

    def test_never_received(self):
        b, _ = make()
        (b.ticket(1, TEAM)
         .on("2026-11-05 09:00", state="Active", assignee=b.other("dev-x"),
             iteration="S37")
         .edit("2026-11-05 10:00", description="changed", by=b.other("po")))
        self.assertEqual(state(b), {
            "received_at": None, "no_criteria": False,
            "text_changes_after": 0, "added_mid_sprint": False,
            "assigned_late": False})


if __name__ == "__main__":
    unittest.main()
