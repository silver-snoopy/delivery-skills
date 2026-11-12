"""Evidence briefs: the person brief and the team brief, built from the
analysed files of the demo portfolio and of a small two-team fixture."""
import contextlib
import io
import json
import os
import re
import stat
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from ledger import analyze
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.errors import LedgerError
from ledger.evidence import (DISCLAIMER, build_person_brief,
                             build_team_brief, render_person_brief,
                             render_team_brief)
from ledger.home import Home
from ledger.testing.builder import PortfolioBuilder
from ledger.testing.portfolio import AS_OF, PRICING, run_demo

FORBIDDEN = ("hands-on", "Delivering", "safe to send", "starvation",
             "sole engineer")
PSEUDONYM = re.compile(r"(?<![\w-])[ops]:[0-9a-f]{6,}")
EXTERNAL = ("http://", "<link", "src=", "@import", "url(")
AS_OF_DATE = date.fromisoformat(AS_OF)


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class DemoBriefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "demo"
        run_demo(Home(cls.root))
        cls.home = Home(cls.root)
        cls.cfg, cls.roster = load_config(cls.home), load_roster(cls.home)
        cls.person = build_person_brief(cls.home, cls.cfg, cls.roster,
                                        "david", as_of=AS_OF_DATE)
        cls.person_html = render_person_brief(cls.person)
        cls.team = build_team_brief(cls.home, cls.cfg, cls.roster, PRICING,
                                    as_of=AS_OF_DATE)
        cls.team_html = render_team_brief(cls.team)
        cls.exec = build_team_brief(cls.home, cls.cfg, cls.roster, PRICING,
                                    executive=True, as_of=AS_OF_DATE)
        cls.exec_html = render_team_brief(cls.exec)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_person_brief_lists_ticket_links_and_timeline(self):
        h = self.person_html
        self.assertIn("_workitems/edit/40655", h)
        self.assertIn('id="evidence-40655"', h)
        # holder timeline: Dávid in progress, then waiting on QA
        self.assertIn("Dávid R., in progress", h)
        self.assertIn("Waiting: QA", h)
        # dated changes from the ticket history
        self.assertIn("8 Oct 2026", h)
        self.assertIn("In QA → Active", h)
        # the DM's carry answer is shown with the card
        self.assertIn("Acceptance criteria rewritten after each review", h)
        # flags by sprint
        self.assertIn("Flags by sprint", h)
        self.assertIn("Carried over more than 2 sprints", h)
        self.assertIn(self.person["rows"][0]["sprint"], h)
        self.assertEqual(
            ["PM-18", "PM-19", "PM-20", "PM-21", "PM-22", "PM-23"],
            [r["sprint"] for r in self.person["rows"]])
        self.assertIn(40655, [t["id"] for t in self.person["in_progress_now"]])

    def test_person_brief_marked_restricted(self):
        h = self.person_html
        self.assertIn("Restricted: delivery manager", h)
        self.assertIn(DISCLAIMER, h)
        self.assertEqual(
            "This brief lists what the board recorded. It does not measure "
            "effort and is not an assessment of the person.", DISCLAIMER)
        self.assertIn("Evidence brief: Dávid R.", h)
        self.assertIn("Restricted: delivery manager", self.team_html)

    def test_team_brief_executive_has_no_names(self):
        h = self.exec_html
        for p in self.roster.people:
            self.assertNotIn(p.display_name, h)
            self.assertIsNone(re.search(r"\b%s\b" % re.escape(p.id), h), p.id)
        self.assertNotIn("Restricted", h)
        self.assertNotIn("<script", h)
        self.assertEqual([], self.exec["cards"])
        self.assertIn("Evidence brief: Pricing Models", h)
        # the non-executive brief carries the flagged-ticket evidence
        self.assertIn('id="evidence-40655"', self.team_html)

    def test_team_brief_rows_and_risks(self):
        rows = self.team["rows"]
        self.assertEqual(6, len(rows))
        self.assertEqual("PM-23", rows[-1]["sprint"])
        self.assertEqual(2, rows[-1]["delivered"])
        self.assertEqual(7, rows[-1]["squad_delivered"])
        self.assertEqual([5, 5, 5, 4, 3, 2], self.team["trend_delivered"])
        self.assertIn("Fewer delivered than usual", self.team_html)

    def test_no_pseudonyms_or_forbidden_words(self):
        for h in (self.person_html, self.team_html, self.exec_html):
            self.assertIsNone(PSEUDONYM.search(h))
            for w in FORBIDDEN:
                self.assertNotIn(w, h)

    def test_unknown_person_or_team(self):
        with self.assertRaises(LedgerError) as cm:
            build_person_brief(self.home, self.cfg, self.roster, "zoltan")
        self.assertIn("david", cm.exception.hint)
        with self.assertRaises(LedgerError) as cm:
            build_team_brief(self.home, self.cfg, self.roster, "Nope")
        self.assertIn(PRICING, cm.exception.hint)

    def test_executive_with_person_refused(self):
        rc, _, err = run("--home", str(self.root), "evidence", "--person",
                         "david", "--executive", "--as-of", AS_OF)
        self.assertEqual(2, rc)
        self.assertIn("--executive", err)

    def test_files_written_private_and_self_contained(self):
        for argv, suffix in (
                (("--person", "david"), "%s-david.html" % AS_OF),
                (("--team", PRICING, "--executive"),
                 "%s-pricing-models-executive.html" % AS_OF)):
            rc, out, err = run("--home", str(self.root), "--json",
                               "evidence", *argv, "--as-of", AS_OF)
            self.assertEqual(0, rc, err)
            res = json.loads(out)
            path = Path(res["file"])
            self.assertEqual(self.root / "evidence" / suffix, path)
            self.assertEqual(0o600, stat.S_IMODE(os.stat(str(path)).st_mode))
            text = path.read_text(encoding="utf-8")
            for ref in EXTERNAL:
                self.assertNotIn(ref, text)
            self.assertEqual(6, res["sprints"])


class ExecutiveRefusalTests(unittest.TestCase):
    def test_executive_refused_when_title_names_engineer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            run_demo(Home(root))
            home = Home(root)
            d = home.team_sprint_dir(PRICING, "demo-pricing-models-pm-23")
            parts = d.relative_to(root).parts
            m = home.read_json(*parts, "metrics.json")
            m["tickets"]["40655"]["title"] = "Pair with Emese on rate tables"
            home.write_json(m, *parts, "metrics.json")
            rc, _, err = run("--home", str(root), "evidence", "--team",
                             PRICING, "--executive", "--as-of", AS_OF)
            self.assertEqual(2, rc)
            self.assertIn("Emese", err)
            self.assertFalse((root / "evidence").exists()
                             and any((root / "evidence").iterdir()))
            # the non-executive brief is still written
            rc, _, err = run("--home", str(root), "evidence", "--team",
                             PRICING, "--as-of", AS_OF)
            self.assertEqual(0, rc, err)


def moved_fixture(root: Path) -> Home:
    """Two teams; Mira works on Alpha in August, then moves to Beta."""
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Alpha", area="Demo\\Alpha", sprints=[
        ("A-1", "2026-08-03", "2026-08-14"),
        ("A-2", "2026-08-17", "2026-08-28")])
    b.team("Beta", area="Demo\\Beta", sprints=[
        ("B-1", "2026-08-31", "2026-09-11"),
        ("B-2", "2026-09-14", "2026-09-25")])
    mira = b.person("mira", "Mira T.", "Alpha", "2026-01-05",
                    end="2026-08-28")
    b.assign("mira", "Beta", "2026-08-31")
    po = PortfolioBuilder.other("product owner")
    tid = 70001
    for team, sprint, start in (("Alpha", "A-1", "2026-08-03"),
                                ("Alpha", "A-2", "2026-08-17"),
                                ("Beta", "B-1", "2026-08-31"),
                                ("Beta", "B-2", "2026-09-14")):
        d = date.fromisoformat(start)
        def day(n, d=d):
            return (d + timedelta(days=n)).isoformat()
        t = b.ticket(tid, team=team, title="Item %d" % tid,
                     description="Scope agreed in refinement for this item.",
                     acceptance="Given the data, then it works as described.")
        t.on("%s 08:00" % day(0), state="New", iteration=sprint,
             assignee=mira, by=po)
        t.on("%s 09:00" % day(1), state="Active")
        t.on("%s 09:00" % day(3), state="In Review")
        t.on("%s 15:00" % day(4), state="Closed", by=po)
        tid += 1
    home = Home(root)
    home.ensure()
    b.write(home)
    cfg, roster = load_config(home), load_roster(home)
    for team in ("Alpha", "Beta"):
        last = b.sprints(team)[-1]
        b.snapshot(cutoff="2026-09-28 08:00", team=team).save(
            home.team_sprint_dir(team, last.id) / "snapshot")
        analyze.run_analyze(home, cfg, roster, team)
    return home


class MovedPersonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.home = moved_fixture(Path(cls._tmp.name) / "moved")
        cls.cfg = load_config(cls.home)
        cls.roster = load_roster(cls.home)
        cls.brief = build_person_brief(cls.home, cls.cfg, cls.roster, "mira",
                                       as_of=date(2026, 9, 30))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_person_brief_follows_person_across_teams(self):
        rows = self.brief["rows"]
        self.assertEqual([("Alpha", "A-1"), ("Alpha", "A-2"),
                          ("Beta", "B-1"), ("Beta", "B-2")],
                         [(r["team"], r["sprint"]) for r in rows])
        self.assertEqual(
            [("Alpha", "2026-01-05", "2026-08-28", 1.0),
             ("Beta", "2026-08-31", None, 1.0)],
            [(a["team"], a["from"], a["to"], a["allocation"])
             for a in self.brief["person"]["assignments"]])
        html = render_person_brief(self.brief)
        self.assertIn("Alpha", html)
        self.assertIn("Beta", html)
        self.assertIn("3 Aug 2026 – 25 Sep 2026", html)
        self.assertIn("_workitems/edit/70004", html)

    def test_totals_are_sums(self):
        t = self.brief["totals"]
        self.assertEqual(4, t["delivered_worked_on"])
        self.assertEqual(4, t["handed_off"])
        self.assertEqual(40.0, t["available_days"])
        self.assertEqual(40.0, t["nominal_days"])
        self.assertEqual([70001, 70002, 70003, 70004],
                         [x["id"] for x in self.brief["delivered"]])

    def test_sprints_limit_keeps_latest(self):
        brief = build_person_brief(self.home, self.cfg, self.roster, "mira",
                                   sprints=2, as_of=date(2026, 9, 30))
        self.assertEqual(["B-1", "B-2"], [r["sprint"] for r in brief["rows"]])
        early = build_person_brief(self.home, self.cfg, self.roster, "mira",
                                   as_of=date(2026, 8, 20))
        self.assertEqual(["A-1"], [r["sprint"] for r in early["rows"]])

    def test_no_analysed_sprint(self):
        with self.assertRaises(LedgerError) as cm:
            build_person_brief(self.home, self.cfg, self.roster, "mira",
                               as_of=date(2026, 7, 1))
        self.assertIn("analyze", cm.exception.hint)


if __name__ == "__main__":
    unittest.main()
