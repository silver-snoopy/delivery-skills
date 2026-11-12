import unittest

from ledger.classify import OUTCOMES
from ledger.metrics import (RULES_VERSION, SCHEMA, analyze_team_sprint,
                            squad_delivered)
from ledger.testing.builder import PortfolioBuilder

SPRINTS = [
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
    ("S38", "2026-11-09", "2026-11-20"),
]
CUTOFF = "2026-11-07 08:00"

# person_observations integers that are maxima, not counts of ledger items
MAXIMA = {"longest_sprints", "todo_assigned_max", "concurrency_max"}


def make(**kw):
    kw.setdefault("not_delivered_reasons", ["Duplicate"])
    b = PortfolioBuilder(tz="Europe/Zurich", **kw)
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
    b.team("Data", area="Demo\\Data", sprints=SPRINTS)
    anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
    bence = b.person("bence", "Bence T.", team="Claims", start="2026-01-05")
    return b, anna, bence


def run(b, team="Claims", sprint="S37"):
    snap = b.snapshot(cutoff=CUTOFF)
    sp = {s.name: s for s in snap.sprints[team]}[sprint]
    cfg, roster = b.config(), b.roster()
    m, l = analyze_team_sprint(snap, cfg, roster, team, sp)
    return m, l, (snap, cfg, sp)


def small_sprint(b, anna, bence):
    qa, rev = b.other("qa"), b.other("reviewer")
    b.ticket(1, team="Claims", title="One").on(
        "2026-10-20 09:00", iteration="S37").on(
        "2026-10-26 09:00", state="Active", assignee=anna).on(
        "2026-10-28 09:00", state="In QA", assignee=qa).on(
        "2026-10-30 09:00", state="Closed")
    b.ticket(2, team="Claims").on(
        "2026-10-20 09:00", iteration="S37").on(
        "2026-10-27 09:00", state="Active", assignee=bence).on(
        "2026-11-02 09:00", state="Closed")
    b.ticket(3, team="Claims").on(
        "2026-10-20 09:00", iteration="S37").on(
        "2026-10-27 09:00", state="Active", assignee=anna).on(
        "2026-11-04 09:00", state="In Review", assignee=rev)
    b.ticket(4, team="Claims").on(
        "2026-10-20 09:00", iteration="S37").on(
        "2026-10-28 09:00", state="Active", assignee=anna)
    b.ticket(5, team="Claims").on(
        "2026-10-20 09:00", iteration="S37").on(
        "2026-10-28 09:00", state="Active", assignee=bence).on(
        "2026-10-29 09:00", state="Closed", reason="Duplicate")
    for i in (101, 102, 103):
        b.squad_ticket(i, team="Claims").on(
            "2026-10-20 09:00", iteration="S37").on(
            "2026-10-26 09:00", state="Active").on(
            "2026-10-30 09:00", state="Closed")


def rich(b, anna, bence):
    """Small sprint plus returns, outside-team, other-team and other-party
    carry cases."""
    small_sprint(b, anna, bence)
    qa, other = b.other("qa"), b.other("po")
    t = b.ticket(6, team="Claims").on(
        "2026-10-26 09:00", state="Active", assignee=anna, iteration="S37")
    for day in ("27", "28", "29"):
        t.on("2026-10-%s 09:00" % day, state="In QA", assignee=qa).on(
            "2026-10-%s 15:00" % day, state="Active", assignee=anna)
    b.ticket(7, team="Claims").on(
        "2026-10-26 09:00", state="Active", assignee=anna,
        iteration="S37").on(
        "2026-11-03 09:00", assignee=other)
    b.ticket(8, team="Claims").on(
        "2026-10-26 09:00", state="Active", assignee=bence,
        iteration="S37").on(
        "2026-11-03 09:00", state="New", assignee=None)
    b.ticket(9, team="Claims").on(
        "2026-10-26 09:00", state="Active", assignee=anna,
        area="Demo\\Elsewhere").on(
        "2026-10-29 09:00", state="Closed")
    b.ticket(10, team="Claims").on(
        "2026-10-26 09:00", state="Active", assignee=anna,
        area="Demo\\Data").on(
        "2026-10-29 09:00", state="Closed")
    b.ticket(11, team="Claims").on(
        "2026-10-26 09:00", state="New", assignee=bence, iteration="S37")


def walk(tree, prefix):
    for k, v in tree.items():
        key = prefix + "." + k
        if isinstance(v, dict):
            yield from walk(v, key)
        else:
            yield key, k, v


class MetricsTests(unittest.TestCase):
    def test_shape_header(self):
        b, anna, bence = make()
        small_sprint(b, anna, bence)
        m, _, (_, _, sp) = run(b)
        self.assertEqual((m["schema"], m["rules_version"]), (SCHEMA,
                                                             RULES_VERSION))
        self.assertEqual(m["sprint"], {"id": sp.id, "name": "S37",
                                       "start": "2026-10-26",
                                       "finish": "2026-11-06"})
        self.assertFalse(m["backfilled"])
        self.assertEqual(m["coverage"], {"missing": [], "unreadable": [],
                                         "malformed": [], "alarm": False})

    def test_counts_for_small_sprint(self):
        b, anna, bence = make()
        small_sprint(b, anna, bence)
        m, l, (snap, cfg, sp) = run(b)
        f = m["figures"]
        self.assertEqual(f["delivered"], 2)
        self.assertEqual(sorted(l["figures.delivered"]), [1, 2])
        self.assertEqual(f["closed_not_delivered"], 1)
        self.assertEqual(f["awaiting_acceptance"]["count"], 1)
        self.assertEqual(f["awaiting_acceptance"]["longest_wait_days"], 2.5)
        self.assertEqual(f["carried"]["in_progress"], 1)
        self.assertEqual(f["carried"]["with_other_party"], 0)
        self.assertEqual(f["squad_delivered"], 5)
        self.assertEqual(sorted(squad_delivered(snap, cfg, "Claims", sp)),
                         [1, 2, 101, 102, 103])
        self.assertEqual(f["returns"]["handed_off"], 2)
        self.assertEqual(sorted(l["figures.returns.handed_off"]), [1, 3])
        self.assertEqual(m["tickets"]["1"]["title"], "One")
        self.assertEqual(m["tickets"]["3"]["wait_label"], "review")
        self.assertEqual(m["people"]["anna"]["handed_off"], 2)
        self.assertEqual(m["people"]["anna"]["in_progress"]["count"], 1)
        self.assertEqual(m["people"]["anna"]["carried"],
                         {"count": 1, "longest_sprints": 1})
        self.assertIsNotNone(f["median_days_with_vendor"])
        self.assertIsNotNone(f["median_elapsed_days"])
        self.assertIn("vendor", f["time_split"])
        self.assertEqual(f["capacity"], {"available": 20.0, "nominal": 20.0})

    def test_vendor_delivered_subset_of_squad(self):
        b, anna, bence = make()
        small_sprint(b, anna, bence)
        m, ledger, _ = run(b)
        self.assertTrue(set(ledger["figures.delivered"])
                        <= set(ledger["figures.squad_delivered"]))
        self.assertEqual(m["warnings"], [])

    def test_vendor_ticket_missing_from_squad_file_still_in_squad_set(self):
        b, anna, bence = make()
        small_sprint(b, anna, bence)
        snap = b.snapshot(cutoff=CUTOFF)
        snap.squad = [t for t in snap.squad if t.id != 2]
        sp = {s.name: s for s in snap.sprints["Claims"]}["S37"]
        m, led = analyze_team_sprint(snap, b.config(), b.roster(), "Claims",
                                     sp)
        self.assertEqual(m["warnings"], [])
        self.assertEqual(m["figures"]["squad_delivered"], 5)
        self.assertIn(2, led["figures.squad_delivered"])

    def test_planned_line_partitions(self):
        b, anna, bence = make()
        rich(b, anna, bence)
        m, l, _ = run(b)
        p = m["figures"]["planned"]
        self.assertEqual(p["total"], p["delivered"] + p["awaiting"]
                         + p["carried"] + p["with_other_party"] + p["left"]
                         + p["not_delivered"])
        # 4 and 6 are carried by the vendor; 7 and 8 are with another party
        self.assertEqual(p, {"total": 8, "delivered": 2, "awaiting": 1,
                             "carried": 2, "with_other_party": 2, "left": 0,
                             "not_delivered": 1})
        self.assertEqual(l["figures.planned.not_delivered"], [5])

    def test_two_people_both_count_ticket_team_counts_once(self):
        b, anna, bence = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on(
            "2026-10-28 09:00", assignee=bence).on(
            "2026-10-30 09:00", state="Closed")
        m, l, _ = run(b)
        self.assertEqual(m["figures"]["delivered"], 1)
        self.assertEqual(m["people"]["anna"]["delivered_worked_on"], 1)
        self.assertEqual(m["people"]["bence"]["delivered_worked_on"], 1)
        self.assertEqual(set(m["tickets"]["1"]["workers"]),
                         {"anna", "bence"})

    def test_assigned_only_never_delivered(self):
        b, anna, bence = make()
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna,
            iteration="S37").on(
            "2026-10-28 09:00", state="Active", assignee=b.other("x")).on(
            "2026-10-30 09:00", state="Closed")
        b.ticket(2, team="Claims").on(
            "2026-10-26 09:00", state="New", assignee=anna, iteration="S37")
        m, l, _ = run(b)
        self.assertEqual(m["tickets"]["1"]["involvement"], "assigned")
        self.assertEqual(m["tickets"]["1"]["outcome"], "delivered")
        self.assertEqual(m["figures"]["delivered"], 0)
        self.assertEqual(m["figures"]["planned"]["total"], 0)
        self.assertEqual(m["figures"]["carried"]["not_started"], 1)
        self.assertEqual(m["figures"]["received"]["received"], 2)
        self.assertEqual(m["people"]["anna"]["delivered_worked_on"], 0)

    def test_returns_events_vs_tickets(self):
        b, anna, bence = make()
        rich(b, anna, bence)
        m, l, _ = run(b)
        r = m["figures"]["returns"]
        self.assertEqual((r["events"], r["tickets"]), (3, 1))
        self.assertEqual([e["ticket"] for e in l["figures.returns.events"]],
                         [6, 6, 6])
        self.assertEqual(len(m["tickets"]["6"]["returns"]), 3)
        self.assertEqual(m["tickets"]["6"]["backward_moves"], 3)

    def test_person_attribution_of_returns(self):
        b, anna, bence = make()
        rich(b, anna, bence)
        m, l, _ = run(b)
        self.assertEqual(m["people"]["anna"]["returns"], 3)
        self.assertEqual(m["people"]["anna"]["backward_moves"], 3)
        self.assertEqual(m["people"]["bence"]["returns"], 0)
        self.assertEqual({e["ticket"] for e in l["people.anna.returns"]},
                         {6})

    def test_carried_with_other_party(self):
        b, anna, bence = make()
        rich(b, anna, bence)
        m, l, _ = run(b)
        c = m["figures"]["carried"]
        self.assertEqual(c, {"in_progress": 2, "not_started": 1,
                             "with_other_party": 2})
        self.assertEqual(sorted(l["figures.carried.with_other_party"]),
                         [7, 8])
        self.assertFalse(m["tickets"]["7"]["held_by_vendor_at_end"])
        self.assertEqual(m["tickets"]["7"]["holder_at_end"], "other")
        self.assertIsNone(m["tickets"]["8"]["holder_at_end"])
        self.assertEqual(m["tickets"]["4"]["holder_at_end"], "anna")
        self.assertEqual(m["people"]["anna"]["in_progress"]["count"], 2)

    def test_holder_at_end_never_pseudonym(self):
        b, anna, bence = make()
        rich(b, anna, bence)
        m, _, _ = run(b)
        for t in m["tickets"].values():
            self.assertIn(t["holder_at_end"], (None, "other", "anna",
                                               "bence"))

    def test_empty_team_sprint(self):
        b, anna, bence = make()
        small_sprint(b, anna, bence)
        m, l, _ = run(b, team="Data")
        f = m["figures"]
        self.assertEqual(f["delivered"], 0)
        self.assertIsNone(f["median_days_with_vendor"])
        self.assertIsNone(f["median_elapsed_days"])
        self.assertIsNone(f["awaiting_acceptance"]["longest_wait_days"])
        self.assertEqual(f["time_split"], {})
        self.assertEqual(m["people"], {})
        self.assertEqual(m["tickets"], {})

    def test_every_count_has_ledger_entry(self):
        b, anna, bence = make()
        rich(b, anna, bence)
        m, l, _ = run(b)
        seen = 0
        for tree in ("figures", "people"):
            for key, name, v in walk(m[tree], tree):
                if isinstance(v, (str, bool)):
                    continue
                self.assertIn(key, l)
                if isinstance(v, int) and name not in MAXIMA:
                    self.assertEqual(len(l[key]), v, key)
                    seen += 1
        self.assertGreater(seen, 30)
        self.assertIn("figures.time_split", l)

    def test_each_ticket_one_outcome(self):
        b, anna, bence = make()
        rich(b, anna, bence)
        m, _, _ = run(b)
        self.assertTrue(m["tickets"])
        for t in m["tickets"].values():
            self.assertIn(t["outcome"], OUTCOMES)

    def test_ticket_outside_configured_areas(self):
        b, anna, bence = make()
        rich(b, anna, bence)
        m, l, _ = run(b)
        self.assertTrue(m["tickets"]["9"]["outside_teams"])
        self.assertFalse(m["tickets"]["1"]["outside_teams"])
        self.assertNotIn(9, l["figures.delivered"])
        self.assertNotIn(9, l["people.anna.delivered_worked_on"])
        self.assertNotIn("10", m["tickets"])
        self.assertNotIn(10, l["figures.delivered"])

    def test_tasks_counting_level(self):
        b, anna, bence = make(unit_level="tasks", unit_types=["Task"])
        b.ticket(20, team="Claims").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on("2026-10-29 09:00", state="Closed")
        b.ticket(21, team="Claims", type="Task", parent=20,
                 parent_title="Story twenty").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on("2026-10-29 09:00", state="Closed")
        m, l, _ = run(b)
        self.assertEqual(l["figures.delivered"], [21])
        self.assertNotIn("20", m["tickets"])
        self.assertEqual(m["tickets"]["21"]["parent"], 20)
        self.assertEqual(m["tickets"]["21"]["parent_title"], "Story twenty")


if __name__ == "__main__":
    unittest.main()
