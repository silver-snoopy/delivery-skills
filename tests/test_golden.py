"""Golden portfolio: the invented demo data run end to end through analyze,
annotations and report, with the story lines checked against the real rules.

Every expected number below is derived from the histories written in
ledger/testing/portfolio_*.py (working days are whole local days Monday to
Friday; a 09:00 start on a day counts 0.625 of that day, a 09:00 end 0.375).
"""
import contextlib
import io
import re
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from ledger import annotations
from ledger.analyze import trace_data
from ledger.classify import OUTCOMES
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.errors import LedgerError
from ledger.home import DEFAULT_DIRNAME, Home
from ledger.issue import issue
from ledger.names_check import _terms, draft_texts, names_check
from ledger.testing.portfolio import (AS_OF, CLAIMS, PLATFORM, PRICING,
                                      build_demo, run_demo)

# person_observations integers that are maxima, not counts of ledger items
MAXIMA = {"longest_sprints", "todo_assigned_max", "concurrency_max"}


def walk(tree, prefix):
    for k, v in tree.items():
        key = prefix + "." + k
        if isinstance(v, dict):
            yield from walk(v, key)
        else:
            yield key, k, v


class GoldenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "demo"
        cls.result = run_demo(Home(cls.root))
        cls.home = Home(cls.root)
        cls.cfg, cls.roster = load_config(cls.home), load_roster(cls.home)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def sprints(self, team):
        """(sprint, metrics, ledger) for each analysed sprint, oldest first."""
        out = []
        root = self.home.path("teams")
        for d in sorted((root / _slug(team) / "sprints").iterdir()):
            m = self.home.read_json(*d.relative_to(self.root).parts,
                                    "metrics.json")
            if m is not None:
                l = self.home.read_json(*d.relative_to(self.root).parts,
                                        "ledger.json")
                out.append((m["sprint"]["name"], m, l))
        return sorted(out, key=lambda x: x[1]["sprint"]["start"])

    def last(self, team):
        return self.sprints(team)[-1][1]

    # ---- end to end ---------------------------------------------------------

    def test_demo_end_to_end(self):
        files = self.result["files"]
        self.assertEqual(set(files), {"executive_html", "executive_md",
                                      "delivery_manager_html"})
        for p in files.values():
            self.assertTrue(Path(p).is_file(), p)
        self.assertIn("invented", self.result["summary"])
        for p in files.values():
            self.assertIn(p, self.result["summary"])
        texts = draft_texts(self.home, date.fromisoformat(AS_OF))
        self.assertEqual(names_check(texts, self.roster, []), [])
        for text in texts.values():
            for p in self.roster.people:
                self.assertNotIn(p.display_name, text)
        dm = Path(files["delivery_manager_html"]).read_text(encoding="utf-8")
        self.assertIn("Dávid R.", dm)
        self.assertIn("Not yet answered", dm)
        # the HTML-looking, piped title of #40197 is escaped everywhere
        for key in ("executive_html", "delivery_manager_html"):
            html = Path(files[key]).read_text(encoding="utf-8")
            self.assertIn("&lt;AddressService&gt;", html, key)
            self.assertNotIn("<AddressService>", html, key)
        md = Path(files["executive_md"]).read_text(encoding="utf-8")
        self.assertIn("AddressService", md)
        self.assertNotIn("<AddressService> |", md)
        # no edition is issued by the demo
        self.assertFalse(self.home.path("reports", AS_OF, "editions").exists())

    def test_cli_demo_refuses_non_empty_home(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "something").write_text("x")
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                code = main(["demo", "--home", d])
            self.assertEqual(code, 2)
            self.assertIn("not empty", err.getvalue())

    def test_cli_demo_writes_into_empty_home(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(["demo", "--home", d])
            self.assertEqual(code, 0)
            self.assertIn("executive.html", out.getvalue())
            self.assertTrue((Path(d) / "reports" / AS_OF / "draft" /
                             "delivery-manager.html").is_file())

    def test_no_engineer_name_in_any_ticket_text_or_note(self):
        b = build_demo()
        terms = _terms(self.roster)
        texts = []
        for t in b._tickets:
            texts += [t.title, t.description, t.acceptance, t.parent_title]
            texts += [x for triple in t.texts for x in triple]
        for d in self.home.path("teams").rglob(annotations.FILE):
            texts += [e["note"] for e in self.home.read_json(
                *d.relative_to(self.root).parts)]
        for term in terms:
            rx = re.compile(r"(?<!\w)%s(?!\w)" % re.escape(term), re.I)
            for text in texts:
                self.assertIsNone(rx.search(text), (term, text))

    def test_issue_succeeds_on_a_copy(self):
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d) / "copy"
            shutil.copytree(str(self.root), str(copy))
            h = Home(copy)
            n = issue(h, load_config(h), load_roster(h),
                      date.fromisoformat(AS_OF))
            self.assertEqual(n, 1)

    def test_two_date_groups(self):
        from ledger.report import build_report
        r = build_report(self.home, self.cfg, self.roster,
                         date.fromisoformat(AS_OF))
        groups = [(g["start"], g["finish"], sorted(t["name"]
                                                   for t in g["teams"]))
                  for g in r["groups"]]
        self.assertEqual(groups, [
            ("2026-10-26", "2026-11-06", sorted([CLAIMS, PRICING])),
            ("2026-10-19", "2026-10-30", [PLATFORM])])
        self.assertEqual(r["missing_teams"], [])

    # ---- the rate table ticket ---------------------------------------------

    def test_rate_table_ticket(self):
        t = self.last(PRICING)["tickets"]["40655"]
        self.assertEqual(t["outcome"], "carried_in_progress")
        self.assertTrue(t["held_by_vendor_at_end"])
        self.assertEqual(t["holder_at_end"], "david")
        # PM-20 ends in QA (awaiting, not carried); PM-21, PM-22, PM-23 end
        # in progress with Dávid -> 3 carried sprints, 1 waiting sprint.
        self.assertEqual(t["sprints_carried"], 3)
        self.assertEqual(t["carried_since"], "PM-21")
        self.assertEqual(t["sprints_waiting"], 1)
        self.assertEqual(t["trail"], ["PM-20", "PM-21", "PM-22", "PM-23"])
        # returns on 8 Oct (QA), 21 Oct (QA), 5 Nov (review)
        self.assertEqual(len(t["returns"]), 3)
        self.assertEqual(t["backward_moves"], 3)
        # Active 22-25 Sep 3 + 8-13 Oct 3 + 21-27 Oct 4 + 5 Nov 09:00 to
        # sprint end 7 Nov 00:00 1.625 = 11.625 -> displayed 11.5
        self.assertEqual(t["workers"], {"david": 11.5})
        flags = self.last(PRICING)["people"]["david"]["flags"]
        self.assertIn("Carried over more than 2 sprints", flags)
        self.assertIn("Ticket returned repeatedly", flags)
        # three backward moves; Dávid's delivered counts 3,3,3,2,1,1 (each of
        # the last three below the mean of the three before)
        self.assertEqual(flags, ["Carried over more than 2 sprints",
                                 "Ticket returned repeatedly",
                                 "Ticket moving backward",
                                 "Fewer delivered than usual"])
        self.assertEqual(self.last(PRICING)["people"]["david"]
                         ["trend_delivered"], [3, 3, 3, 2, 1, 1])

    def test_rate_table_whole_life_time(self):
        d = trace_data(self.home, self.cfg, self.roster, 40655, PRICING)
        # with Dávid: 3 + 3 + 4 + (5 Nov 09:00 -> cutoff 7 Nov 08:00:
        # 0.625 + 1) = 11.625 -> 11.5; QA waits 9 (25 Sep-8 Oct) and 6
        # (13-21 Oct); review wait 7 (27 Oct-5 Nov)
        self.assertEqual(d["time_split"], {"vendor": 11.5, "waiting:QA": 15.0,
                                           "waiting:review": 7.0})
        waiting = sum(v for k, v in d["time_split"].items()
                      if k.startswith("waiting:"))
        self.assertGreater(waiting, d["time_split"]["vendor"] * 1.5)
        # acceptance criteria edited by a non-vendor person after each return
        ret_days = [e["at"][:10] for e in d["events"]
                    if "returned" in e["flags"]]
        self.assertEqual(ret_days, ["2026-10-08", "2026-10-21",
                                    "2026-11-05"])
        ac = [c for c in d["changes"]
              if any(x["field"] == "acceptance" for x in c["changes"])]
        self.assertEqual([c["date"] for c in ac], ret_days)
        for c in ac:
            self.assertFalse((c["by"] or "").startswith("p:"))

    def test_pricing_status_at_risk(self):
        m = self.last(PRICING)
        self.assertEqual(m["status"]["label"], "At risk")
        self.assertIn("Chronic carryover", m["status"]["rules"])
        self.assertIn("Returns", m["status"]["rules"])
        # deliberate trend: 5,5,5,4,3,2 -> Fewer delivered than usual
        self.assertEqual(m["figures"]["trend_delivered"], [5, 5, 5, 4, 3, 2])
        self.assertEqual(m["status"]["rules"], [
            "Chronic carryover", "Returns", "Fewer delivered than usual"])
        # 40655 (5 Nov) and 40662 (29 Oct) returned inside PM-23
        self.assertEqual(m["figures"]["returns"]["tickets"], 2)

    def test_other_team_statuses(self):
        self.assertEqual(self.last(CLAIMS)["status"]["label"], "On track")
        dp = self.last(PLATFORM)
        self.assertEqual(dp["status"]["label"], "Needs attention")
        self.assertEqual(dp["status"]["rules"], ["Returns"])

    # ---- person context -----------------------------------------------------

    def test_csilla_context_leave(self):
        p = self.last(CLAIMS)["people"]["csilla"]
        # leave Mon 2 - Thu 5 Nov
        self.assertEqual((p["nominal_days"], p["available_days"]), (10.0, 6.0))
        self.assertIn("4 days leave", p["context"])
        self.assertEqual(p["flags"], [])

    def test_ferenc_context_joiner(self):
        m = self.last(PRICING)
        p = m["people"]["ferenc"]
        self.assertTrue(p["is_joiner"])
        self.assertIn("Joined in PM-22", p["context"])
        self.assertEqual(p["trend_delivered"], [None, None, None, None, 1, 0])
        # nothing delivered in PM-23, but the ticket finished 28 Oct counts
        # as handed off, so no "Nothing delivered or handed off"
        self.assertEqual(p["handed_off"], 1)
        self.assertEqual(p["flags"], [])

    def test_emese_context_nothing_in_progress(self):
        p = self.last(PRICING)["people"]["emese"]
        # nothing in progress on 3, 4 and 5 Nov
        self.assertEqual(p["days_nothing_in_progress"], 3.0)
        self.assertIn("3 days with nothing in progress", p["context"])
        self.assertEqual(p["flags"], [])

    # ---- other story lines --------------------------------------------------

    def test_finished_28_oct_awaiting_review(self):
        m = self.last(PRICING)
        t = m["tickets"]["40671"]
        self.assertEqual(t["outcome"], "awaiting_acceptance")
        self.assertEqual(t["wait_label"], "review")
        # 28 Oct 09:00 -> sprint end 7 Nov 00:00: 0.625 + 7 = 7.625 -> 7.5
        self.assertEqual(t["days_waiting"], 7.5)
        self.assertEqual(m["figures"]["awaiting_acceptance"]["count"], 1)

    def test_claims_story_outcomes(self):
        m = self.last(CLAIMS)
        tk = m["tickets"]
        self.assertEqual(tk["40191"]["outcome"], "closed_not_delivered")
        self.assertTrue(tk["40192"]["received"]["added_mid_sprint"])
        self.assertEqual(tk["40192"]["outcome"], "delivered")
        self.assertEqual(tk["40193"]["outcome"], "carried_in_progress")
        self.assertFalse(tk["40193"]["held_by_vendor_at_end"])
        self.assertEqual(tk["40194"]["outcome"], "active_outside_sprint")
        self.assertEqual(tk["40195"]["outcome"], "assigned_outside_sprint")
        self.assertEqual(tk["40196"]["outcome"], "delivered")
        self.assertIn("<AddressService> |", tk["40197"]["title"])
        self.assertEqual(tk["40197"]["outcome"], "carried_in_progress")
        self.assertTrue(tk["40197"]["held_by_vendor_at_end"])
        f = m["figures"]
        self.assertEqual(f["carried"], {"in_progress": 1, "not_started": 0,
                                        "with_other_party": 1})
        self.assertEqual(f["active_outside_sprint"], 1)
        self.assertEqual(f["assigned_outside_sprint"], 1)
        self.assertEqual(f["closed_not_delivered"], 1)
        # Anna 2 + Bence 2 + mid-sprint 1 + Csilla 2
        self.assertEqual(f["trend_delivered"], [6, 7, 6, 6, 7, 7])
        notes = annotations.current(annotations.load(
            self.home, CLAIMS, m["sprint"]["id"]))
        note = notes["carry:40197:%s" % m["sprint"]["id"]]["note"]
        self.assertIn("address lookup service", note)

    def test_one_meaning_per_carried_label(self):
        # Claims Intake: 40197 is carried by the vendor, 40193 is in
        # progress with another party. The planned line, the "Carried
        # over" column and the lists agree.
        f = self.last(CLAIMS)["figures"]
        self.assertEqual(f["planned"], {
            "total": 9, "delivered": 6, "awaiting": 0, "carried": 1,
            "with_other_party": 1, "left": 0, "not_delivered": 1})
        files = self.result["files"]
        md = Path(files["executive_md"]).read_text(encoding="utf-8")
        row = [ln for ln in md.splitlines()
               if ln.startswith("| Claims Intake |")][0]
        self.assertIn("| 9 planned: 6 delivered, 0 awaiting, 1 carried, "
                      "1 with another party, 0 left, 1 not delivered "
                      "| 1 | 1 |", row)
        line = [ln for ln in md.splitlines() if "edit/40193)" in ln
                and ln.startswith("  - ")][0]
        self.assertTrue(line.endswith("In progress with another party"))
        section = md.split("### Claims Intake")[1].split("\n### ")[0]
        self.assertIn("edit/40193", section.split(
            "**In progress with another party**")[1])
        self.assertNotIn("edit/40193", section.split(
            "**Carried over**")[1].split("**")[0])
        self.assertIn("**Active outside a sprint**", section)
        self.assertIn("**Where ticket time went**", section)
        for key in ("executive_html", "delivery_manager_html"):
            html = Path(files[key]).read_text(encoding="utf-8")
            self.assertIn("9 planned: 6 delivered, 0 awaiting, 1 carried, "
                          "1 with another party, 0 left, 1 not delivered",
                          html)
        self.assertIn("Returns in the sprint by recorded reason: 0 defect, "
                      "1 requirement changed, 0 other, 1 not recorded.", md)

    def test_demo_notes_present_and_name_nobody(self):
        # (team, ticket, kind) answered with a note by the demo
        expected = {(CLAIMS, 40197, "carry"), (PRICING, 40655, "carry"),
                    (PLATFORM, 40951, "return")}
        rxs = [re.compile(r"(?<!\w)%s(?!\w)" % re.escape(t), re.I)
               for t in _terms(self.roster)]
        found = set()
        for team in (CLAIMS, PRICING, PLATFORM):
            m = self.last(team)
            for e in annotations.load(self.home, team, m["sprint"]["id"]):
                if e["note"]:
                    found.add((team, e["ticket"], e["kind"]))
                for p in self.roster.people:
                    self.assertNotIn(p.display_name.lower(),
                                     e["note"].lower())
                for rx in rxs:
                    self.assertIsNone(rx.search(e["note"]), e["note"])
        self.assertEqual(found, expected)

    def test_demo_refuses_empty_home_argument(self):
        for arg in ("", "   "):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                code = main(["demo", "--home", arg])
            self.assertEqual(code, 2)
            self.assertIn("/tmp/ledger-demo", err.getvalue())

    def test_demo_refuses_default_data_home(self):
        with tempfile.TemporaryDirectory() as d:
            fake_home = Path(d)
            default = fake_home / DEFAULT_DIRNAME
            with mock.patch.object(Path, "home", return_value=fake_home):
                for target in (default, ):
                    with self.assertRaises(LedgerError) as cm:
                        run_demo(Home(target))
                    self.assertIn("default data home", cm.exception.message)
                    self.assertFalse(default.exists())
                    default.mkdir()  # empty and existing: still refused
                    with self.assertRaises(LedgerError):
                        run_demo(Home(target))
                    self.assertEqual(list(default.iterdir()), [])

    def test_platform_single_contributor_features(self):
        sprints = self.sprints(PLATFORM)
        parents = {}
        for _, m, _ in sprints:
            for t in m["tickets"].values():
                parents.setdefault(t["parent_title"], set()).update(
                    t["workers"])
        solo = sorted(k for k, v in parents.items() if v == {"gabor"})
        self.assertEqual(len(solo), 3, parents)

    def test_squad_well_above_vendor(self):
        for team in (CLAIMS, PRICING, PLATFORM):
            for _, m, _ in self.sprints(team):
                f = m["figures"]
                self.assertGreaterEqual(f["squad_delivered"],
                                        f["delivered"] + 4, team)

    def test_some_but_not_all_answered(self):
        pending = answered = 0
        for team in (CLAIMS, PRICING, PLATFORM):
            m = self.last(team)
            entries = annotations.load(self.home, team, m["sprint"]["id"])
            answered += len(entries)
            pending += len(annotations.pending(m, entries))
        self.assertGreater(answered, 0)
        self.assertGreater(pending, 0)

    # ---- invariants ---------------------------------------------------------

    def test_six_sprints_per_team(self):
        for team in (CLAIMS, PRICING, PLATFORM):
            self.assertEqual(len(self.sprints(team)), 6, team)

    def test_invariants_across_portfolio(self):
        checked = 0
        for team in (CLAIMS, PRICING, PLATFORM):
            for name, m, l in self.sprints(team):
                where = "%s %s" % (team, name)
                self.assertEqual(m["warnings"], [], where)
                self.assertLessEqual(set(l["figures.delivered"]),
                                     set(l["figures.squad_delivered"]), where)
                self.assertTrue(m["tickets"], where)
                for t in m["tickets"].values():
                    self.assertIn(t["outcome"], OUTCOMES, where)
                outcome_keys = ("figures.delivered",
                                "figures.closed_not_delivered",
                                "figures.reclosed",
                                "figures.awaiting_acceptance.count",
                                "figures.carried.in_progress",
                                "figures.carried.not_started",
                                "figures.carried.with_other_party",
                                "figures.active_outside_sprint")
                ids = [i for k in outcome_keys for i in l[k]]
                self.assertEqual(len(ids), len(set(ids)), where)
                for tree in ("figures", "people"):
                    for key, k, v in walk(m[tree], tree):
                        if isinstance(v, (str, bool, list)) or v is None:
                            continue
                        self.assertIn(key, l, where)
                        if isinstance(v, int) and k not in MAXIMA:
                            self.assertEqual(len(l[key]), v,
                                             "%s %s" % (where, key))
                            checked += 1
                for k, v in m["figures"]["time_split"].items():
                    self.assertGreaterEqual(v, 0, where)
        self.assertGreater(checked, 500)


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


if __name__ == "__main__":
    unittest.main()
