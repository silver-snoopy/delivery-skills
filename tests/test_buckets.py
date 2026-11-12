import unittest
from datetime import timedelta

from ledger.buckets import classified_days, first_vendor_hold, time_split
from ledger.phases import PhaseMapper
from ledger.testing.builder import PortfolioBuilder
from ledger.timeline import build_timeline
from ledger.workcal import WorkCalendar, display_days

SPRINTS = [
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
    ("S38", "2026-11-09", "2026-11-20"),
]
TEAM = "Pricing"
CAL = WorkCalendar("Europe/Zurich", [])


def make(anna_end=None):
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team(TEAM, area="Demo\\Pricing", sprints=SPRINTS)
    anna = b.person("anna", "Anna K.", team=TEAM, start="2026-01-05",
                    end=anna_end)
    return b, anna


def timeline(b, cutoff, ticket_id=1):
    cfg = b.config()
    mapper = PhaseMapper(cfg.team(TEAM), cfg)
    t = {x.id: x for x in b.snapshot(cutoff=cutoff).tickets}[ticket_id]
    return build_timeline(t, mapper, b.parse_time(cutoff), set())


def split_and_total(b, tl, start, end):
    roster = b.roster()
    s, e = b.parse_time(start), b.parse_time(end)
    return (time_split(tl, s, e, CAL, TEAM, roster),
            classified_days(tl, s, e, CAL, TEAM, roster))


class TimeSplitTest(unittest.TestCase):
    def test_split_matches_mockup_evidence_card(self):
        b, anna = make()
        d = CAL.local_date(b.parse_time("2026-06-01 00:00"))
        steps = [("Active", 4), ("In QA", 9), ("Active", 2), ("In QA", 12),
                 ("Active", 3), ("In Review", 13)]
        t = b.ticket(1, TEAM)
        for state, days in steps:
            t.on(d.isoformat() + " 00:00", state=state, assignee=anna)
            d = CAL.add_working_days(d, days)
        cutoff = d.isoformat() + " 00:00"
        tl = timeline(b, cutoff)
        roster = b.roster()
        first = first_vendor_hold(tl, TEAM, roster, CAL.tz)
        self.assertEqual(first, b.parse_time("2026-06-01 00:00"))
        s = time_split(tl, first, b.parse_time(cutoff), CAL, TEAM, roster)
        self.assertEqual({k: display_days(v) for k, v in s.items() if v},
                         {"vendor": 9.0, "waiting:QA": 21.0,
                          "waiting:review": 13.0})
        self.assertAlmostEqual(s["vendor"], 9.0, places=9)

    def test_buckets_are_exhaustive_and_exclusive(self):
        b, anna = make()
        other = b.other("dev-x")
        (b.ticket(1, TEAM)
         .on("2026-10-26 00:00", state="Active", assignee=anna)     # vendor 1
         .on("2026-10-27 00:00", state="Active", assignee=other)    # other 1
         .on("2026-10-28 00:00", state="Active", assignee=None)     # unass 1
         .on("2026-10-29 00:00", state="In QA", assignee=None)      # wait 1
         .on("2026-10-30 00:00", state="New", assignee=anna)        # parked 1
         .on("2026-11-02 00:00", state="In Review", assignee=other))  # 2
        tl = timeline(b, "2026-11-04 00:00")
        s, total = split_and_total(b, tl, "2026-10-01", "2026-11-04")
        for k, v in {"vendor": 1.0, "other_party": 1.0, "unassigned": 1.0,
                     "waiting:QA": 1.0, "parked": 1.0,
                     "waiting:review": 2.0}.items():
            self.assertAlmostEqual(s[k], v, places=9, msg=k)
        self.assertEqual(len(s), 6)
        self.assertLess(abs(sum(s.values()) - total), 1e-9)
        self.assertAlmostEqual(total, 7.0, places=9)

    def test_handoff_with_unknown_wait_label_is_other(self):
        phases = [{"state": "New", "phase": "todo"},
                  {"state": "Active", "phase": "doing"},
                  {"state": "Blocked", "phase": "handoff"},
                  {"state": "Closed", "phase": "done"},
                  {"state": "Removed", "phase": "removed"}]
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team(TEAM, area="Demo\\Pricing", sprints=SPRINTS, phases=phases)
        anna = b.person("anna", "Anna K.", team=TEAM, start="2026-01-05")
        (b.ticket(1, TEAM)
         .on("2026-10-26 00:00", state="Active", assignee=anna)
         .on("2026-10-27 00:00", state="Blocked"))
        tl = timeline(b, "2026-10-28 00:00")
        s, _ = split_and_total(b, tl, "2026-10-01", "2026-10-28")
        self.assertAlmostEqual(s["waiting:other"], 1.0, places=9)

    def test_closed_gap_before_reopen_not_counted(self):
        b, anna = make()
        (b.ticket(1, TEAM)
         .on("2026-10-26 00:00", state="Active", assignee=anna)
         .on("2026-10-27 00:00", state="Closed")
         .on("2026-10-30 00:00", state="Active"))
        tl = timeline(b, "2026-11-02 00:00")
        s, total = split_and_total(b, tl, "2026-10-01", "2026-11-02")
        self.assertEqual(set(s), {"vendor"})
        self.assertAlmostEqual(s["vendor"], 2.0, places=9)
        self.assertAlmostEqual(total, 2.0, places=9)

    def test_time_before_first_vendor_hold_not_counted(self):
        b, anna = make()
        other = b.other("dev-x")
        (b.ticket(1, TEAM)
         .on("2026-10-26 00:00", state="Active", assignee=other)
         .on("2026-10-28 00:00", state="In QA", assignee=None)
         .on("2026-10-29 00:00", state="Active", assignee=anna))
        tl = timeline(b, "2026-10-31 00:00")
        roster = b.roster()
        self.assertEqual(first_vendor_hold(tl, TEAM, roster, CAL.tz),
                         b.parse_time("2026-10-29 00:00"))
        s, total = split_and_total(b, tl, "2026-10-01", "2026-10-31")
        self.assertEqual(s, {"vendor": 2.0})
        self.assertAlmostEqual(total, 2.0, places=9)

    def test_todo_before_first_doing_not_counted(self):
        b, anna = make()
        (b.ticket(1, TEAM)
         .on("2026-10-26 00:00", state="New", assignee=anna)
         .on("2026-10-28 00:00", state="Active"))
        tl = timeline(b, "2026-10-29 00:00")
        s, total = split_and_total(b, tl, "2026-10-01", "2026-10-29")
        self.assertEqual(first_vendor_hold(tl, TEAM, b.roster(), CAL.tz),
                         b.parse_time("2026-10-26 00:00"))
        self.assertEqual(s, {"vendor": 1.0})
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_clipped_to_sprint(self):
        b, anna = make()
        (b.ticket(1, TEAM)
         .on("2026-09-28 00:00", state="Active", assignee=anna,
             iteration="S35")
         .on("2026-10-12 00:00", iteration="S36")
         .on("2026-10-26 00:00", iteration="S37")
         .on("2026-11-09 00:00", iteration="S38"))
        tl = timeline(b, "2026-11-20 00:00")
        roster = b.roster()
        sp = {s.name: s for s in b.sprints(TEAM)}["S37"]
        s = time_split(tl, sp.start, sp.end, CAL, TEAM, roster)
        self.assertLessEqual(sum(s.values()), 10.0 + 1e-9)
        self.assertAlmostEqual(s["vendor"], 10.0, places=9)
        self.assertLess(abs(sum(s.values()) - classified_days(
            tl, sp.start, sp.end, CAL, TEAM, roster)), 1e-9)

    def test_assignment_ending_mid_interval_moves_time_to_other_party(self):
        b, anna = make(anna_end="2026-10-27")
        (b.ticket(1, TEAM)
         .on("2026-10-26 00:00", state="Active", assignee=anna))
        tl = timeline(b, "2026-10-31 00:00")
        s, total = split_and_total(b, tl, "2026-10-01", "2026-10-31")
        self.assertAlmostEqual(s["vendor"], 2.0, places=9)
        self.assertAlmostEqual(s["other_party"], 3.0, places=9)
        self.assertLess(abs(sum(s.values()) - total), 1e-9)

    def test_first_vendor_hold_when_assignment_starts_mid_interval(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team(TEAM, area="Demo\\Pricing", sprints=SPRINTS)
        anna = b.person("anna", "Anna K.", team=TEAM, start="2026-10-28")
        (b.ticket(1, TEAM)
         .on("2026-10-26 09:00", state="Active", assignee=anna))
        tl = timeline(b, "2026-10-31 00:00")
        roster = b.roster()
        self.assertEqual(first_vendor_hold(tl, TEAM, roster, CAL.tz),
                         b.parse_time("2026-10-28 00:00"))
        s, _ = split_and_total(b, tl, "2026-10-01", "2026-10-31")
        self.assertEqual(s, {"vendor": 3.0})

    def test_never_held_by_vendor(self):
        b, _ = make()
        b.ticket(1, TEAM).on("2026-10-26 00:00", state="Active",
                             assignee=b.other("dev-x"))
        tl = timeline(b, "2026-10-31 00:00")
        self.assertIsNone(first_vendor_hold(tl, TEAM, b.roster(), CAL.tz))
        s, total = split_and_total(b, tl, "2026-10-01", "2026-10-31")
        self.assertEqual(s, {})
        self.assertEqual(total, 0.0)


if __name__ == "__main__":
    unittest.main()
