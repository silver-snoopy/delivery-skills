import contextlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ledger import annotations, narrative
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.errors import LedgerError
from ledger.home import Home
from ledger.report import (FOOTNOTE, build_report, clear_override,
                           exec_projection, set_override)
from ledger.testing.builder import PortfolioBuilder

AS_OF = date(2026, 11, 7)


def dates(prefix, shift_weeks=0):
    base = [("35", "2026-09-28", "2026-10-09"), ("36", "2026-10-12", "2026-10-23"),
            ("37", "2026-10-26", "2026-11-06"), ("38", "2026-11-09", "2026-11-20")]
    out = []
    for n, s, f in base:
        s2 = date.fromordinal(date.fromisoformat(s).toordinal() - 7 * shift_weeks)
        f2 = date.fromordinal(date.fromisoformat(f).toordinal() - 7 * shift_weeks)
        out.append((prefix + n, s2.isoformat(), f2.isoformat()))
    return out


def delivered(b, tid, team, it, who, start, parent=""):
    b.ticket(tid, team=team, title="Story %d" % tid, parent_title=parent).on(
        start + " 08:00", iteration=it).on(
        start + " 09:00", state="Active", assignee=who).on(
        start + " 15:00", state="Closed")


def portfolio():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=dates("S"))
    b.team("Data", area="Demo\\Data", sprints=dates("D-"))
    b.team("Mobile", area="Demo\\Mobile", sprints=dates("M"))
    b.team("Payments", area="Demo\\Payments", sprints=dates("P", 1))
    b.team("Ledger", area="Demo\\Ledger",
           sprints=[("L1", "2026-11-09", "2026-11-20")])
    anna = b.person("eng_anna", "Anna K.", team="Claims", start="2026-01-05")
    bence = b.person("eng_bence", "Bence T.", team="Claims", start="2026-01-05")
    csilla = b.person("eng_csilla", "Csilla V.", team="Data", start="2026-01-05")
    dora = b.person("eng_dora", "Dora M.", team="Payments", start="2026-01-05")
    emil = b.person("eng_emil", "Emil R.", team="Mobile", start="2026-01-05")
    # Claims S37: two delivered under one parent, one under another,
    # one with no parent, one awaiting acceptance, four carried
    delivered(b, 1, "Claims", "S37", anna, "2026-10-27", "Billing export")
    delivered(b, 2, "Claims", "S37", bence, "2026-10-28", "Billing export")
    delivered(b, 3, "Claims", "S37", anna, "2026-10-29", "Address lookup")
    delivered(b, 4, "Claims", "S37", bence, "2026-10-30")
    b.ticket(5, team="Claims", title="Story 5").on(
        "2026-10-26 08:00", iteration="S37").on(
        "2026-10-27 09:00", state="Active", assignee=anna).on(
        "2026-11-04 09:00", state="In Review", assignee=b.other("reviewer"))
    for tid in (21, 22, 23, 24):
        b.ticket(tid, team="Claims", title="Story %d" % tid,
                 parent_title="Search rework").on(
            "2026-10-26 08:00", iteration="S37").on(
            "2026-10-27 09:00", state="Active",
            assignee=anna if tid % 2 else bence)
    for i in (101, 102):
        b.squad_ticket(i, team="Claims").on(
            "2026-10-26 08:00", iteration="S37").on(
            "2026-10-27 09:00", state="Active").on(
            "2026-10-30 09:00", state="Closed")
    delivered(b, 31, "Data", "D-37", csilla, "2026-10-27")
    delivered(b, 41, "Mobile", "M37", emil, "2026-10-28")
    delivered(b, 51, "Payments", "P37", dora, "2026-10-21")
    b.ticket(52, team="Payments", title="Story 52").on(
        "2026-10-19 08:00", iteration="P37").on(
        "2026-10-20 09:00", state="Active", assignee=dora)
    # late revisions so every team's cutoff falls after its sprint
    for team, tid in (("Claims", 90), ("Data", 91), ("Mobile", 92),
                      ("Payments", 93), ("Ledger", 94)):
        b.ticket(tid, team=team).on("2026-11-23 09:00")
    return b


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        home = Home(Path(cls.tmp))
        portfolio().write(home)
        code, _, err = run_main(["--home", cls.tmp, "analyze", "--all"])
        assert code == 0, err

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # each test gets its own copy of the analysed home
        self.dir = tempfile.mkdtemp()
        shutil.copytree(self.tmp, self.dir, dirs_exist_ok=True)
        self.home = Home(Path(self.dir))
        self.cfg = load_config(self.home)
        self.roster = load_roster(self.home)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def report(self):
        return build_report(self.home, self.cfg, self.roster, AS_OF)

    def team(self, report, name):
        return next(t for g in report["groups"] for t in g["teams"]
                    if t["name"] == name)

    def annotate_claims(self):
        sid = "demo-claims-s37"
        rec = annotations.record
        rec(self.home, "Claims", sid, "carry:21:" + sid, agreed="yes",
            reason="dependency", note="Waiting on the export service")
        rec(self.home, "Claims", sid, "carry:22:" + sid, agreed="yes")
        rec(self.home, "Claims", sid, "carry:23:" + sid, agreed="no",
            reason="larger than expected")
        # 24 has no answer

    def test_groups_by_identical_dates(self):
        r = self.report()
        self.assertTrue(r["footnote"].startswith(FOOTNOTE))
        self.assertIn("Sprint scope is set by the customer", r["footnote"])
        self.assertEqual(r["variant"], "dm")
        self.assertEqual(r["as_of"], "2026-11-07")
        self.assertEqual([(g["start"], g["finish"]) for g in r["groups"]],
                         [("2026-10-26", "2026-11-06"),
                          ("2026-10-19", "2026-10-30")])
        g1, g2 = r["groups"]
        self.assertEqual([t["name"] for t in g1["teams"]],
                         ["Claims", "Data", "Mobile"])
        self.assertEqual({t["sprint"]["name"] for t in g1["teams"]},
                         {"S37", "D-37", "M37"})
        self.assertEqual([t["name"] for t in g2["teams"]], ["Payments"])
        self.assertEqual(g1["totals"]["teams"], 3)
        self.assertEqual(g1["totals"]["delivered"], 4 + 1 + 1)
        self.assertEqual(g1["totals"]["delivered"], sum(
            t["metrics"]["figures"]["delivered"] for t in g1["teams"]))
        self.assertEqual(g2["totals"]["delivered"], 1)
        self.assertEqual(g2["totals"]["carried"], 1)
        self.assertEqual(g1["totals"]["engineers"], 4)
        self.assertEqual(g2["totals"]["engineers"], 1)
        self.assertEqual(g1["totals"]["squad_delivered"], sum(
            t["metrics"]["figures"]["squad_delivered"] for t in g1["teams"]))
        self.assertEqual(g1["totals"]["awaiting"], 1)
        self.assertEqual(g1["totals"]["available"], sum(
            t["metrics"]["figures"]["capacity"]["available"]
            for t in g1["teams"]))
        self.assertEqual(g1["totals"]["nominal"], sum(
            t["metrics"]["figures"]["capacity"]["nominal"]
            for t in g1["teams"]))
        split = {}
        for t in g1["teams"]:
            for k, v in t["metrics"]["figures"]["time_split"].items():
                split[k] = split.get(k, 0) + v
        self.assertEqual(g1["totals"]["time_split"], split)
        self.assertFalse(self.team(r, "Claims")["backfilled"] is None)

    def test_unchanged_marker(self):
        self.assertFalse(self.team(self.report(), "Claims")["unchanged"])
        self.home.write_json(
            {"edition": 1, "issued_at": "2026-11-01T09:00:00+00:00",
             "team_sprints": [{"team": "Data", "sprint_id": "demo-data-d-37"}]},
            "reports", "2026-10-31", "editions", "1", "manifest.json")
        self.home.write_json(
            {"edition": 2, "issued_at": "2026-11-07T09:00:00+00:00",
             "team_sprints": [{"team": "Claims", "sprint_id": "demo-claims-s37"},
                              {"team": "Mobile", "sprint_id": "demo-mobile-m36"}]},
            "reports", "2026-11-07", "editions", "1", "manifest.json")
        r = self.report()
        self.assertTrue(self.team(r, "Claims")["unchanged"])
        self.assertFalse(self.team(r, "Data")["unchanged"])  # older edition
        self.assertFalse(self.team(r, "Mobile")["unchanged"])  # other sprint
        self.assertFalse(self.team(r, "Payments")["unchanged"])

    def test_carried_split_by_annotation(self):
        self.annotate_claims()
        r = self.report()
        claims = self.team(r, "Claims")
        self.assertEqual(claims["metrics"]["figures"]["carried"]["in_progress"], 4)
        self.assertEqual(claims["annotations"]["carry:21:demo-claims-s37"]["agreed"],
                         "yes")
        t = r["groups"][0]["totals"]
        self.assertEqual((t["carried"], t["carried_agreed"],
                          t["carried_not_agreed"], t["carried_unanswered"]),
                         (4, 2, 1, 1))
        t2 = r["groups"][1]["totals"]
        self.assertEqual((t2["carried"], t2["carried_agreed"],
                          t2["carried_not_agreed"], t2["carried_unanswered"]),
                         (1, 0, 0, 1))

    def test_exec_projection_has_no_person_fields(self):
        self.annotate_claims()
        report = self.report()
        full = json.dumps(report)
        self.assertIn('"eng_anna"', full)  # the DM report does carry them
        proj = exec_projection(report)
        self.assertEqual(proj["variant"], "exec")
        self.assertEqual(report["variant"], "dm")
        s = json.dumps(proj)
        for p in self.roster.people:
            self.assertNotIn(p.display_name, s)
            self.assertNotIn('"' + p.id + '"', s)
            self.assertNotIn(p.id, s)
        self.assertNotIn('"people"', s)
        self.assertNotIn('"workers"', s)
        self.assertNotIn('"holder_at_end"', s)
        self.assertNotIn('"coverage"', s)
        self.assertNotIn('"owner"', s)
        self.assertIn('"held_by_vendor_at_end"', s)
        self.assertIn("Waiting on the export service", s)  # DM note kept
        # the full report is untouched
        self.assertIn("people", report["groups"][0]["teams"][0]["metrics"])

    def test_people_names_in_full_report_only(self):
        report = self.report()
        self.assertEqual(report["people_names"]["eng_anna"], "Anna K.")
        self.assertEqual(set(report["people_names"]),
                         {p.id for p in self.roster.people})
        report["evidence"] = {"Claims": [{"ticket": 21, "title": "x"}]}
        proj = exec_projection(report)
        self.assertNotIn("people_names", proj)
        self.assertNotIn("evidence", proj)
        self.assertIn("people_names", report)
        self.assertIn("evidence", report)

    def test_fallback_groups_by_parent(self):
        m = self.team(self.report(), "Claims")["metrics"]
        n = narrative.fallback(m)
        self.assertEqual(n["delivered"], [
            {"heading": "Billing export", "text": "", "tickets": [1, 2]},
            {"heading": "Address lookup", "text": "", "tickets": [3]},
            {"heading": "Other", "text": "", "tickets": [4]}])
        self.assertEqual(n["awaiting"],
                         [{"heading": "Other", "text": "", "tickets": [5]}])
        self.assertEqual(n["not_delivered"], [
            {"heading": "Search rework", "text": "",
             "tickets": [21, 22, 23, 24]}])
        self.assertEqual(self.team(self.report(), "Claims")["narrative"], n)

    def test_fallback_summary_uses_counts_not_percent(self):
        self.annotate_claims()
        r = self.report()
        g = r["groups"][0]["totals"]
        first = ("Sprint ending 6 Nov: %d of %d squad items delivered by the "
                 "Vendor team across 3 teams; 1 finished and awaiting "
                 "acceptance; 4 carried over (2 agreed in advance, 1 not, "
                 "1 not yet answered)." % (g["delivered"], g["squad_delivered"]))
        self.assertTrue(r["summary"].startswith(first), r["summary"])
        self.assertIn("Sprint ending 30 Oct: 1 of ", r["summary"])
        self.assertIn("across 1 team;", r["summary"])
        self.assertNotIn("%", r["summary"])
        self.assertEqual(r["summary"], narrative.fallback_summary(r))
        # squad_delivered 0 omits "of N squad items"
        fake = {"vendor_label": "Vendor team", "groups": [{
            "start": "2026-10-26", "finish": "2026-11-06",
            "teams": [{"name": "X", "status": {"label": "At risk",
                                               "rules": ["a", "b"],
                                               "override": None}}],
            "totals": {"teams": 1, "delivered": 0, "squad_delivered": 0,
                       "awaiting": 0, "carried": 0, "carried_agreed": 0,
                       "carried_not_agreed": 0, "carried_unanswered": 0}}]}
        self.assertEqual(
            narrative.fallback_summary(fake),
            "Sprint ending 6 Nov: 0 delivered by the Vendor team across 1 "
            "team; 0 finished and awaiting acceptance; 0 carried over (0 "
            "agreed in advance, 0 not, 0 not yet answered). At risk: X (a, b).")

    def test_missing_team_listed(self):
        r = self.report()
        self.assertEqual(r["missing_teams"], ["Ledger"])
        names = [t["name"] for g in r["groups"] for t in g["teams"]]
        self.assertNotIn("Ledger", names)
        later = build_report(self.home, self.cfg, self.roster, date(2026, 11, 20))
        self.assertEqual(later["missing_teams"], [])
        self.assertEqual(self.team(later, "Claims")["sprint"]["name"], "S38")

    def test_override_recorded_with_reason(self):
        with self.assertRaises(LedgerError):
            set_override(self.home, self.cfg, AS_OF, "Claims", "At risk", "  ")
        with self.assertRaises(LedgerError):
            set_override(self.home, self.cfg, AS_OF, "Claims", "Bad", "why")
        with self.assertRaises(LedgerError):
            set_override(self.home, self.cfg, AS_OF, "Nope", "At risk", "why")
        set_override(self.home, self.cfg, AS_OF, "Claims", "At risk",
                     "customer escalation")
        set_override(self.home, self.cfg, AS_OF, "Data", "On track", "fine")
        self.assertEqual(
            self.home.read_json("reports", "2026-11-07", "overrides.json"),
            {"Claims": {"label": "At risk", "reason": "customer escalation"},
             "Data": {"label": "On track", "reason": "fine"}})
        r = self.report()
        st = self.team(r, "Claims")["status"]
        self.assertEqual(st["override"], {"label": "At risk",
                                          "reason": "customer escalation"})
        self.assertIn("label", st)
        self.assertIn("rules", st)
        self.assertIn(" At risk: Claims (set by the delivery manager: "
                      "customer escalation).", r["summary"])
        self.assertNotIn(": Data (", r["summary"])
        clear_override(self.home, AS_OF, "Claims")
        r = self.report()
        self.assertIsNone(self.team(r, "Claims")["status"]["override"])


if __name__ == "__main__":
    unittest.main()
