"""Who decided the sprint's scope, the starting load against the team's
usual delivery, the load risk and the wording that keeps "planned" from
reading as the vendor's own commitment."""
import unittest

from ledger.config import DEFAULT_THRESHOLDS
from ledger.history import sprint_load
from ledger.metrics import analyze_team_sprint
from ledger.render.fmt import load_text, scope_text
from ledger.report import FOOTNOTE, exec_projection, footnote
from ledger.scope import PARTIES, party
from ledger.testing.builder import PortfolioBuilder
from tests.test_risks import TH, fired, metrics

SPRINTS = [
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
    ("S38", "2026-11-09", "2026-11-20"),
]
CUTOFF = "2026-11-07 08:00"


def make():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
    anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
    return b, anna


def run(b, sprint="S37"):
    snap = b.snapshot(cutoff=CUTOFF)
    sp = {s.name: s for s in snap.sprints["Claims"]}[sprint]
    return analyze_team_sprint(snap, b.config(), b.roster(), "Claims", sp)


class PartyTests(unittest.TestCase):
    def test_actor_prefixes(self):
        self.assertEqual(party("p:anna"), "vendor")
        self.assertEqual(party("o:" + "a" * 32), "other_party")
        self.assertEqual(party("s:" + "b" * 32), "automation")
        self.assertEqual(party(None), "not_recorded")
        self.assertEqual(party(""), "not_recorded")


class ScopeFigureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        b, anna = make()
        po, bot = b.other("po"), b.service("rollover")
        # 1: placed by the PO before the start
        b.ticket(1, team="Claims").on(
            "2026-10-22 10:00", iteration="S37", by=po).on(
            "2026-10-27 09:00", state="Active", assignee=anna).on(
            "2026-10-30 09:00", state="Closed")
        # 2: placed by the vendor engineer themselves
        b.ticket(2, team="Claims").on(
            "2026-10-22 10:00", iteration="S37", assignee=anna,
            by=anna).on(
            "2026-10-27 09:00", state="Active")
        # 3: carried in by an automation at rollover
        b.ticket(3, team="Claims").on(
            "2026-10-12 09:00", iteration="S36", assignee=anna,
            state="Active", by=po).on(
            "2026-10-26 00:30", iteration="S37", by=bot)
        # 4: added by the PO mid-sprint
        b.ticket(4, team="Claims").on(
            "2026-10-29 10:00", iteration="S37", by=po).on(
            "2026-10-29 11:00", state="Active", assignee=anna).on(
            "2026-11-03 09:00", state="Closed")
        # 5: in the sprint at the start, taken out by the PO mid-sprint
        b.ticket(5, team="Claims").on(
            "2026-10-22 10:00", iteration="S37", by=po).on(
            "2026-10-27 09:00", state="Active", assignee=anna).on(
            "2026-10-29 09:00", iteration=None, state="New", by=po)
        # 6: in S37 once, moved out before the start, put back by the PO
        b.ticket(6, team="Claims").on(
            "2026-10-15 10:00", iteration="S37", assignee=anna,
            by=anna).on(
            "2026-10-16 10:00", iteration=None, by=anna).on(
            "2026-10-23 10:00", iteration="S37", by=po).on(
            "2026-10-27 09:00", state="Active")
        cls.m, cls.ledger = run(b)

    def test_ticket_records(self):
        t = self.m["tickets"]
        self.assertEqual(t["1"]["placed_by"], "other_party")
        self.assertEqual(t["2"]["placed_by"], "vendor")
        self.assertEqual(t["3"]["placed_by"], "automation")
        self.assertEqual(t["4"]["placed_by"], "other_party")
        self.assertFalse(t["4"]["in_sprint_at_start"])
        self.assertEqual(t["5"]["removed_by"], "other_party")
        self.assertTrue(t["5"]["in_sprint_at_start"])
        self.assertEqual(t["6"]["placed_by"], "other_party",
                         "the run in force during the sprint counts")
        self.assertIsNone(t["1"]["removed_by"])

    def test_figures_and_ledger(self):
        s = self.m["figures"]["scope"]
        self.assertEqual(s["at_start"]["total"], 5)
        self.assertEqual(s["at_start"]["parties"],
                         {"other_party": 3, "vendor": 1, "automation": 1,
                          "not_recorded": 0})
        self.assertEqual(s["added"]["total"], 1)
        self.assertEqual(s["added"]["parties"]["other_party"], 1)
        self.assertEqual(s["removed"]["total"], 1)
        self.assertEqual(self.ledger["figures.scope.at_start.total"],
                         [1, 2, 3, 5, 6])
        self.assertEqual(
            self.ledger["figures.scope.at_start.parties.automation"], [3])
        self.assertEqual(self.ledger["figures.scope.added.total"], [4])
        self.assertEqual(
            self.ledger["figures.scope.removed.parties.other_party"], [5])
        self.assertEqual(set(s["at_start"]["parties"]), set(PARTIES))

    def test_exec_projection_keeps_the_party_split(self):
        report = {"groups": [{"teams": [{"metrics": dict(self.m)}]}]}
        out = exec_projection(report)
        scope = out["groups"][0]["teams"][0]["metrics"]["figures"]["scope"]
        self.assertEqual(scope["at_start"]["parties"]["other_party"], 3)


def _hist(*delivered):
    return [{"sprint": {"name": "S%d" % i}, "figures": {"delivered": d}}
            for i, d in enumerate(delivered)]


class LoadTests(unittest.TestCase):
    def figs(self, n):
        return {"scope": {"at_start": {"total": n}}}

    def test_needs_enough_history(self):
        out = sprint_load(self.figs(9), _hist(5, 6), TH)
        self.assertIsNone(out["median_delivered"])
        self.assertFalse(out["above_usual"])
        self.assertEqual(out["sprints"], 2)

    def test_median_over_the_window(self):
        th = dict(TH, window_sprints=3)
        out = sprint_load(self.figs(9), _hist(100, 4, 6, 5), th)
        self.assertEqual(out["median_delivered"], 5.0)
        self.assertEqual(out["sprints"], 3)
        self.assertTrue(out["above_usual"])

    def test_above_usual_needs_ratio_and_excess(self):
        self.assertFalse(sprint_load(self.figs(7), _hist(5, 5, 5),
                                     TH)["above_usual"], "ratio not met")
        self.assertFalse(sprint_load(self.figs(3), _hist(2, 2, 2),
                                     TH)["above_usual"], "excess under 2")
        self.assertTrue(sprint_load(self.figs(8), _hist(5, 5, 5),
                                    TH)["above_usual"])

    def test_silent_without_scope(self):
        self.assertIsNone(sprint_load({}, _hist(5, 5, 5), TH))

    def test_thresholds_have_defaults(self):
        for k in ("load_history_min", "load_ratio", "load_min_excess"):
            self.assertIn(k, DEFAULT_THRESHOLDS)


class LoadRiskTests(unittest.TestCase):
    def m(self, above):
        m = metrics(tickets={"1": {"in_sprint_at_start": True},
                             "2": {"in_sprint_at_start": False}})
        m["figures"]["load"] = {"at_start": 9, "median_delivered": 6.0,
                                "sprints": 5, "above_usual": above}
        return m

    def test_fires_only_above_usual(self):
        hit = fired(self.m(True), "load_above_usual")
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["tickets"], ["1"])
        self.assertIn("9 tickets were with vendor engineers", hit[0]["text"])
        self.assertIn("median of 6", hit[0]["text"])
        self.assertIn("product owner", hit[0]["ask"])
        self.assertEqual(fired(self.m(False), "load_above_usual"), [])

    def test_asks_do_not_call_scope_a_vendor_commitment(self):
        low = fired(metrics(capacity={"available": 1.0, "nominal": 10.0}),
                    "low_capacity")
        fewer = fired(metrics(rules=["Fewer delivered than usual"]),
                      "fewer_delivered")
        for c in low + fewer:
            self.assertNotIn("commitment", c["ask"])
            self.assertIn("product owner", c["ask"])


class WordingTests(unittest.TestCase):
    def test_scope_text(self):
        f = {"scope": {
            "at_start": {"total": 14, "parties": {
                "other_party": 12, "vendor": 2, "automation": 0,
                "not_recorded": 0}},
            "added": {"total": 3, "parties": {
                "other_party": 3, "vendor": 0, "automation": 0,
                "not_recorded": 0}},
            "removed": {"total": 0, "parties": {}}}}
        self.assertEqual(
            scope_text(f),
            "Sprint scope: 14 with vendor engineers at the start (placed in "
            "the sprint: 12 by another party, 2 by vendor engineers); 3 "
            "added after the start (3 by another party)")
        self.assertEqual(scope_text({}), "")

    def test_load_text(self):
        self.assertEqual(
            load_text({"load": {"at_start": 9, "median_delivered": 6.0,
                                "sprints": 5, "above_usual": True}}),
            "Load at the start: 9 tickets, against a median of 6 delivered "
            "in the team's previous 5 sprints; above what the team usually "
            "delivers")
        self.assertIn("too few earlier sprints", load_text(
            {"load": {"at_start": 4, "median_delivered": None,
                      "sprints": 1, "above_usual": False}}))
        self.assertEqual(load_text({"load": None}), "")

    def test_footnote_says_who_sets_scope(self):
        b, _ = make()
        cfg = b.config()
        self.assertIn("Sprint scope is set by the customer", footnote(cfg))
        self.assertIn("whoever placed it there", footnote(cfg))
        self.assertTrue(footnote(cfg).startswith(FOOTNOTE))


if __name__ == "__main__":
    unittest.main()
