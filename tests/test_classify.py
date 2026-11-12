import unittest

from ledger.classify import (OUTCOMES, CarryAge, carry_age,
                             held_by_vendor_at_end, involvement, is_member, is_planned, left_sprint, outcome,
                             workers)
from ledger.phases import PhaseMapper
from ledger.testing.builder import PortfolioBuilder
from ledger.timeline import build_timeline

SPRINTS = [
    ("S33", "2026-08-31", "2026-09-11"),
    ("S34", "2026-09-14", "2026-09-25"),
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
    ("S38", "2026-11-09", "2026-11-20"),
]
CUTOFF = "2026-11-07 08:00"


def make(anna_end=None, **kw):
    b = PortfolioBuilder(tz="Europe/Zurich", **kw)
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
    anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05",
                    end=anna_end)
    return b, anna


def timeline(b, ticket_id=1):
    cfg = b.config()
    mapper = PhaseMapper(cfg.team("Claims"), cfg)
    snap = b.snapshot(cutoff=CUTOFF)
    t = {x.id: x for x in snap.tickets}[ticket_id]
    return build_timeline(t, mapper, b.parse_time(CUTOFF), set())


class Ctx:
    def __init__(self, b):
        self.b = b
        self.cfg = b.config()
        self.roster = b.roster()
        self.sp = {s.name: s for s in b.sprints("Claims")}
        self.all = b.sprints("Claims")
        from ledger.workcal import WorkCalendar
        self.cal = WorkCalendar("Europe/Zurich", self.cfg.holidays)
        self.tz = self.cal.tz

    def before(self, name):
        return [s for s in self.all if s.start < self.sp[name].start]

    def outcome(self, tl, name):
        return outcome(tl, self.sp[name], self.before(name))

    def involvement(self, tl, name):
        return involvement(tl, self.sp[name], "Claims", self.roster, self.cal)

    def carry(self, tl, names):
        return carry_age(tl, [self.sp[n] for n in names], "Claims",
                         self.roster, self.cal)

    def workers(self, tl, upto="2026-11-07 00:00"):
        return workers(tl, self.b.parse_time(upto), "Claims", self.roster,
                       self.cal)


class OutcomeTests(unittest.TestCase):
    def test_outcomes_constant(self):
        self.assertEqual(OUTCOMES, (
            "delivered", "closed_not_delivered", "reclosed",
            "awaiting_acceptance", "carried_in_progress",
            "carried_not_started", "active_outside_sprint",
            "assigned_outside_sprint"))

    def test_handoff_then_closed_is_delivered_and_worked(self):
        b, anna = make()
        qa = b.other("qa")
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-29 09:00", state="In QA", assignee=qa).on(
            "2026-11-02 09:00", state="Closed")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.involvement(tl, "S37"), "worked")
        self.assertEqual(c.outcome(tl, "S37"), "delivered")

    def test_closed_on_weekend_between_sprints_counts_in_later_sprint(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-12 09:00", state="Active", assignee=anna,
            iteration="S36").on(
            "2026-10-24 11:00", state="Closed")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.outcome(tl, "S36"), "carried_in_progress")
        self.assertEqual(c.outcome(tl, "S37"), "delivered")

    def test_done_before_period_is_not_in_play(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-09-28 09:00", state="Active", assignee=anna,
            iteration="S35").on(
            "2026-10-01 09:00", state="Closed")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.outcome(tl, "S35"), "delivered")
        self.assertIsNone(c.outcome(tl, "S37"))

    def test_reopened_and_closed_again_is_reclosed(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-12 09:00", state="Active", assignee=anna,
            iteration="S36").on(
            "2026-10-15 09:00", state="Closed").on(
            "2026-10-27 09:00", state="Active", iteration="S37").on(
            "2026-10-29 09:00", state="Closed")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.outcome(tl, "S36"), "delivered")
        self.assertEqual(c.outcome(tl, "S37"), "reclosed")

    def test_not_delivered_closure_then_real_closure_is_delivered(self):
        b, anna = make(not_delivered_reasons=["Duplicate"])
        b.ticket(1, team="Claims").on(
            "2026-10-12 09:00", state="Active", assignee=anna,
            iteration="S36").on(
            "2026-10-15 09:00", state="Closed", reason="Duplicate").on(
            "2026-10-27 09:00", state="Active", reason="Reopened",
            iteration="S37").on(
            "2026-10-29 09:00", state="Closed", reason="Done")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.outcome(tl, "S36"), "closed_not_delivered")
        self.assertEqual(c.outcome(tl, "S37"), "delivered")

    def test_duplicate_closure_not_delivered(self):
        b, anna = make(not_delivered_reasons=["Duplicate"])
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-28 09:00", state="Closed", reason="Duplicate")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.outcome(tl, "S37"), "closed_not_delivered")

    def test_awaiting_acceptance_is_not_carryover(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-30 09:00", state="In QA", assignee=b.other("qa"))
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.outcome(tl, "S37"), "awaiting_acceptance")
        age = c.carry(tl, ["S36", "S37"])
        self.assertEqual(age.sprints_carried, 0)
        self.assertEqual(age.sprints_waiting, 1)

    def test_moved_to_next_sprint_early_still_member(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-11-04 09:00", iteration="S38")
        c, tl = Ctx(b), timeline(b)
        self.assertTrue(is_member(tl, c.sp["S37"]))
        self.assertEqual(c.outcome(tl, "S37"), "carried_in_progress")
        self.assertFalse(left_sprint(tl, c.sp["S37"], c.all))

    def test_never_in_any_sprint(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration=None)
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.outcome(tl, "S37"), "active_outside_sprint")

    def test_assigned_outside_sprint(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna, iteration=None)
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.outcome(tl, "S37"), "assigned_outside_sprint")

    def test_iteration_path_not_in_calendar_treated_as_no_sprint(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="Old Bucket")
        c, tl = Ctx(b), timeline(b)
        for s in c.all:
            self.assertFalse(is_member(tl, s))
        self.assertEqual(c.outcome(tl, "S37"), "active_outside_sprint")

    def test_assigned_never_started(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna, iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.involvement(tl, "S37"), "assigned")
        self.assertEqual(c.outcome(tl, "S37"), "carried_not_started")

    def test_triage_by_vendor_then_built_by_other_is_not_worked(self):
        b, anna = make()
        qa = b.other("qa")
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna,
            iteration="S37").on(
            "2026-10-27 09:00", state="Active", assignee=qa).on(
            "2026-10-30 09:00", state="Closed")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.involvement(tl, "S37"), "assigned")
        self.assertEqual(c.workers(tl), {})

    def test_removed_is_none(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-28 09:00", state="Removed")
        c, tl = Ctx(b), timeline(b)
        self.assertIsNone(c.outcome(tl, "S37"))

    def test_ticket_created_after_sprint_is_none(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-27 09:00", state="Active", assignee=anna,
            iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertIsNone(c.outcome(tl, "S36"))
        self.assertIsNone(c.involvement(tl, "S36"))


class InvolvementTests(unittest.TestCase):
    def test_holder_outside_assignment_dates_not_vendor(self):
        b, anna = make(anna_end="2026-10-09")
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertIsNone(c.involvement(tl, "S37"))
        self.assertEqual(c.workers(tl), {})

    def test_assignment_ending_mid_interval(self):
        b, anna = make(anna_end="2026-10-27")
        b.ticket(1, team="Claims").on(
            "2026-10-26 00:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-29 00:00", state="Closed")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.involvement(tl, "S37"), "worked")
        self.assertEqual(c.workers(tl), {"anna": 2.0})

    def test_assignment_starting_mid_interval_is_worked(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-11-02")
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.involvement(tl, "S37"), "worked")
        self.assertIsNone(c.involvement(tl, "S36"))


    def test_vendor_hold_only_on_holiday_is_not_involvement(self):
        b, anna = make(holidays=["2026-10-26"])
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-26 18:00", assignee=b.other("dev"))
        c, tl = Ctx(b), timeline(b)
        self.assertIsNone(c.involvement(tl, "S37"))

    def test_held_by_vendor_at_end(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-12 09:00", state="Active", assignee=anna,
            iteration="S36").on(
            "2026-10-26 09:00", assignee=b.other("dev"), iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertTrue(held_by_vendor_at_end(tl, c.sp["S36"], "Claims",
                                              c.roster, c.cal))
        self.assertFalse(held_by_vendor_at_end(tl, c.sp["S37"], "Claims",
                                               c.roster, c.cal))
        self.assertFalse(held_by_vendor_at_end(tl, c.sp["S35"], "Claims",
                                               c.roster, c.cal))


class MembershipTests(unittest.TestCase):
    def test_added_mid_sprint_not_planned(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna).on(
            "2026-10-29 10:00", iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertTrue(is_member(tl, c.sp["S37"]))
        self.assertFalse(is_planned(tl, c.sp["S37"], c.cal, 1))

    def test_planned_within_grace(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna).on(
            "2026-10-27 16:00", iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertFalse(is_planned(tl, c.sp["S37"], c.cal, 1))
        self.assertTrue(is_planned(tl, c.sp["S37"], c.cal, 2))

    def test_left_sprint_to_backlog(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-30 09:00", iteration=None)
        c, tl = Ctx(b), timeline(b)
        self.assertTrue(left_sprint(tl, c.sp["S37"], c.all))
        self.assertEqual(c.outcome(tl, "S37"), "carried_in_progress")

    def test_closed_after_leaving_is_not_left(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-30 09:00", iteration=None, state="Closed")
        c, tl = Ctx(b), timeline(b)
        self.assertFalse(left_sprint(tl, c.sp["S37"], c.all))


class CarryAgeTests(unittest.TestCase):
    def test_carried_four_sprints(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-09-14 09:00", state="Active", assignee=anna,
            iteration="S34").on(
            "2026-09-28 09:00", iteration="S35").on(
            "2026-10-12 09:00", iteration="S36").on(
            "2026-10-26 09:00", iteration="S37")
        c, tl = Ctx(b), timeline(b)
        age = c.carry(tl, ["S33", "S34", "S35", "S36", "S37"])
        self.assertEqual(age, CarryAge(sprints_carried=4,
                                       carried_since="S34",
                                       trail=["S34", "S35", "S36", "S37"],
                                       sprints_waiting=0))

    def test_carry_age_starts_at_first_vendor_hold(self):
        b, anna = make()
        other = b.other("dev")
        b.ticket(1, team="Claims").on(
            "2026-08-31 09:00", state="Active", assignee=other,
            iteration="S33").on(
            "2026-09-14 09:00", iteration="S34").on(
            "2026-09-28 09:00", iteration="S35").on(
            "2026-10-12 09:00", iteration="S36", assignee=anna).on(
            "2026-10-26 09:00", iteration="S37")
        c, tl = Ctx(b), timeline(b)
        age = c.carry(tl, ["S33", "S34", "S35", "S36", "S37"])
        self.assertEqual(age.sprints_carried, 2)
        self.assertEqual(age.carried_since, "S36")
        self.assertEqual(age.trail, ["S36", "S37"])

    def test_handed_to_other_party_stops_carry_count(self):
        b, anna = make()
        other = b.other("dev")
        b.ticket(1, team="Claims").on(
            "2026-09-28 09:00", state="Active", assignee=anna,
            iteration="S35").on(
            "2026-10-12 09:00", iteration="S36", assignee=other).on(
            "2026-10-26 09:00", iteration="S37")
        c, tl = Ctx(b), timeline(b)
        age = c.carry(tl, ["S35", "S36", "S37"])
        self.assertEqual(age.sprints_carried, 1)
        self.assertEqual(age.carried_since, "S35")
        self.assertEqual(age.trail, ["S35", "S36", "S37"])

    def test_vendor_returns_after_other_party(self):
        b, anna = make()
        other = b.other("dev")
        b.ticket(1, team="Claims").on(
            "2026-09-28 09:00", state="Active", assignee=anna,
            iteration="S35").on(
            "2026-10-12 09:00", iteration="S36", assignee=other).on(
            "2026-10-26 09:00", iteration="S37", assignee=anna)
        c, tl = Ctx(b), timeline(b)
        age = c.carry(tl, ["S35", "S36", "S37"])
        self.assertEqual(age.sprints_carried, 2)
        self.assertEqual(age.carried_since, "S35")

    def test_never_held_by_vendor(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=b.other("dev"),
            iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.carry(tl, ["S36", "S37"]),
                         CarryAge(0, None, [], 0))


class WorkersTests(unittest.TestCase):
    def test_workers_two_people(self):
        b, anna = make()
        bence = b.person("bence", "Bence T.", team="Claims",
                         start="2026-01-05")
        b.ticket(1, team="Claims").on(
            "2026-10-26 00:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-29 00:00", assignee=bence).on(
            "2026-10-31 00:00", state="Closed")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.workers(tl), {"anna": 3.0, "bence": 2.0})

    def test_workers_stop_at_upto(self):
        b, anna = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 00:00", state="Active", assignee=anna,
            iteration="S37")
        c, tl = Ctx(b), timeline(b)
        self.assertEqual(c.workers(tl, upto="2026-10-28 00:00"),
                         {"anna": 2.0})


if __name__ == "__main__":
    unittest.main()
