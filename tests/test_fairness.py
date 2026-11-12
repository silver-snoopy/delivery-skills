"""Scenario tests for the final review's fairness findings: work outside the
team's own area (C1), history inherited from other parties (C2) and the
minimum hold for "worked" (R3)."""
import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from ledger import analyze, history
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.home import Home
from ledger.config import DEFAULT_THRESHOLDS
from ledger.metrics import analyze_team_sprint
from ledger.testing.builder import PortfolioBuilder

SPRINTS = [
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
]
CUTOFF = "2026-11-07 08:00"
IDLE = {"Nothing delivered or handed off", "No recorded board changes"}


def chain(b, team):
    """Analyse every sprint of the team in order, applying history."""
    snap = b.snapshot(cutoff=CUTOFF)
    cfg, roster = b.config(), b.roster()
    done = []
    for sp in snap.sprints[team]:
        m, led = analyze_team_sprint(snap, cfg, roster, team, sp)
        history.apply(m, led, list(done), cfg)
        m["_ledger"] = led
        done.append(m)
    return done


class OutsideAreaTests(unittest.TestCase):
    def outside(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
        # one ticket per sprint, each under an area no team is configured for
        for n, (start, close) in enumerate((
                ("2026-09-28", "2026-10-09"), ("2026-10-12", "2026-10-23"),
                ("2026-10-26", "2026-11-06"))):
            b.ticket(300 + n, team="A", title="Shared %d" % n).on(
                start + " 00:00", state="Active", assignee=eva,
                area="Demo\\Shared").on(
                close + " 17:00", state="Closed")
        return b

    def test_engineer_working_outside_configured_areas_is_not_idle(self):
        for m in chain(self.outside(), "A"):
            p = m["people"]["eva"]
            self.assertEqual(p["days_nothing_in_progress"], 0.0)
            self.assertEqual(p["delivered_worked_on"], 0)
            self.assertEqual(p["delivered_elsewhere"], 1)
            self.assertEqual(p["handed_off_elsewhere"], 0)
            self.assertEqual(p["board_change_days"], 2)
            self.assertEqual(IDLE & set(p["flags"]), set())
            self.assertEqual(m["figures"]["delivered"], 0)
        last = m
        self.assertEqual(last["_ledger"]["people.eva.delivered_elsewhere"],
                         [302])
        self.assertTrue(last["tickets"]["302"]["outside_teams"])

    def test_ticket_in_another_teams_area_counts_for_the_person(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        b.team("B", area="Demo\\B", sprints=SPRINTS)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
        qa = b.other("qa")
        b.ticket(310, team="B").on(
            "2026-10-12 00:00", state="Active", assignee=eva).on(
            "2026-10-23 17:00", state="In QA", assignee=qa)
        b.ticket(311, team="B").on(
            "2026-10-26 00:00", state="Active", assignee=eva).on(
            "2026-11-05 12:00", state="In QA", assignee=qa)
        ms = chain(b, "A")
        for m in ms[1:]:
            p = m["people"]["eva"]
            self.assertEqual(p["handed_off"], 0)
            self.assertEqual(p["handed_off_elsewhere"], 1)
            self.assertEqual(IDLE & set(p["flags"]), set())
        # 311 left her hands on 5 Nov; 6 Nov is the one day with nothing
        self.assertEqual(ms[2]["people"]["eva"]["days_nothing_in_progress"],
                         1.0)
        self.assertEqual(ms[2]["_ledger"]["people.eva.handed_off_elsewhere"],
                         [311])
        self.assertNotIn("311", ms[2]["tickets"])

    def test_split_allocation_is_not_idle_in_either_team(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        b.team("B", area="Demo\\B", sprints=SPRINTS)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05",
                       allocation=0.5)
        b.assign("eva", "B", start="2026-01-05", allocation=0.5)
        # always exactly one ticket in progress, alternating between teams
        b.ticket(320, team="A").on(
            "2026-10-26 00:00", state="Active", assignee=eva,
            iteration="S37").on("2026-10-30 00:00", state="Closed")
        b.ticket(321, team="B").on(
            "2026-10-30 00:00", state="Active", assignee=eva,
            iteration="S37").on("2026-11-07 00:00", state="Closed")
        for team in ("A", "B"):
            m = chain(b, team)[-1]
            p = m["people"]["eva"]
            self.assertEqual(p["available_days"], 5.0, team)
            self.assertEqual(p["days_nothing_in_progress"], 0.0, team)
        a = chain(b, "A")[-1]["people"]["eva"]
        self.assertEqual((a["delivered_worked_on"], a["delivered_elsewhere"]),
                         (1, 0))

    def test_outside_ticket_does_not_raise_the_person_carry_flag(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
        b.ticket(330, team="A").on(
            "2026-09-28 09:00", state="Active", assignee=eva,
            iteration="S35", area="Demo\\Shared").on(
            "2026-10-12 09:00", iteration="S36").on(
            "2026-10-26 09:00", iteration="S37")
        m = chain(b, "A")[-1]
        self.assertEqual(m["tickets"]["330"]["sprints_carried"], 3)
        self.assertEqual(m["people"]["eva"]["carried"]["count"], 0)
        self.assertNotIn("Carried over more than 2 sprints",
                         m["people"]["eva"]["flags"])


class OutsideRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.home = Home(self.tmp / "home")
        self.home.ensure()

    def report(self, b, team="A"):
        b.write(self.home)
        last = b.sprints(team)[-1]
        b.snapshot(cutoff=CUTOFF, team=team).save(
            self.home.team_sprint_dir(team, last.id) / "snapshot")
        cfg, roster = load_config(self.home), load_roster(self.home)
        analyze.run_analyze(self.home, cfg, roster, team)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--home", str(self.home.root), "report",
                                   "--as-of", "2026-11-07"]), 0)
        d = self.home.path("reports", "2026-11-07", "draft")
        return {n: (d / n).read_text(encoding="utf-8") for n in (
            "delivery-manager.html", "executive.html", "executive.md")}

    def test_outside_table_in_dm_report_only(self):
        b = OutsideAreaTests().outside()
        # an own-team ticket so the team has something to report
        b.ticket(1, team="A", title="Own work").on(
            "2026-10-26 09:00", state="Active", assignee="p:eva",
            iteration="S37").on("2026-10-28 09:00", state="Closed")
        b.squad_ticket(2, team="A").on(
            "2026-10-26 09:00", state="Active", iteration="S37")
        files = self.report(b)
        dm = files["delivery-manager.html"]
        self.assertIn("<h4>Outside configured teams</h4>", dm)
        table = dm.split("<h4>Outside configured teams</h4>")[1].split(
            "</table>")[0]
        self.assertIn("Shared 2", table)
        self.assertIn("<td>Closed</td><td>Eva M.</td><td>Delivered</td>",
                      table)
        self.assertNotIn("Own work", table)
        self.assertIn("1 (+1 elsewhere)", dm)
        for name in ("executive.html", "executive.md"):
            self.assertNotIn("Outside configured teams", files[name])
            self.assertNotIn("Shared 2", files[name])
            self.assertNotIn("elsewhere", files[name])

    def test_outside_ticket_card_only_while_a_roster_person_holds_it(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
        qa = b.other("qa")
        for tid, last_holder in ((340, eva), (341, b.other("dev"))):
            t = b.ticket(tid, team="A", title="Outside %d" % tid).on(
                "2026-10-26 09:00", state="Active", assignee=eva,
                area="Demo\\Shared")
            for day in ("27", "29"):
                t.on("2026-10-%s 09:00" % day, state="In QA",
                     assignee=qa).on(
                    "2026-10-%d 09:00" % (int(day) + 1), state="Active",
                    assignee=eva)
            t.on("2026-11-04 09:00", assignee=last_holder)
        dm = self.report(b)["delivery-manager.html"]
        self.assertIn('id="evidence-340"', dm)
        self.assertNotIn('id="evidence-341"', dm)
        card = dm.split('id="evidence-340"')[1].split("</article>")[0]
        self.assertIn("This ticket is outside the configured teams.", card)


    def test_card_gives_earlier_returns_as_context(self):
        dm = self.report(InheritedHistoryTests().build(2))[
            "delivery-manager.html"]
        card = dm.split('id="evidence-300"')[1].split("</article>")[0]
        self.assertIn("2 returns before a vendor engineer held it (not "
                      "counted).", card)


class InheritedHistoryTests(unittest.TestCase):
    def build(self, vendor_returns):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
        dev, qa = b.other("dev"), b.other("qa")
        t = b.ticket(300, team="A", title="Inherited").on(
            "2026-10-12 09:00", state="Active", assignee=dev,
            iteration="S36")
        for day in ("13", "15"):  # bounces between customer developer and QA
            t.on("2026-10-%s 09:00" % day, state="In QA", assignee=qa).on(
                "2026-10-%s 09:00" % (int(day) + 1), state="Active",
                assignee=dev)
        t.on("2026-10-26 09:00", assignee=eva, iteration="S37")
        for k in range(vendor_returns):
            t.on("2026-10-%d 09:00" % (27 + 2 * k), state="In QA",
                 assignee=qa).on(
                "2026-10-%d 09:00" % (28 + 2 * k), state="Active",
                assignee=eva)
        return b

    def test_returns_before_vendor_involvement_are_not_charged(self):
        m = chain(self.build(1), "A")[-1]
        t = m["tickets"]["300"]
        self.assertEqual(len(t["returns"]), 1)
        self.assertEqual(t["backward_moves"], 1)
        self.assertEqual(t["returns_before_vendor"], 2)
        self.assertEqual(t["backward_moves_before_vendor"], 2)
        self.assertEqual(m["people"]["eva"]["returns"], 1)
        self.assertEqual(m["people"]["eva"]["flags"], [])
        self.assertEqual(m["figures"]["returns"]["events"], 1)

    def test_no_issue_candidate_for_inherited_returns(self):
        from ledger import risks
        m = chain(self.build(1), "A")[-1]
        m.pop("_ledger")
        cands = risks._team_candidates(
            "A", m, self.build(1).config().thresholds)
        self.assertEqual([c for c in cands if "300" in c["tickets"]], [])
        m2 = chain(self.build(2), "A")[-1]
        cands = risks._team_candidates(
            "A", m2, self.build(2).config().thresholds)
        self.assertEqual([c["detector"] for c in cands
                          if "300" in c["tickets"]], ["repeated_returns"])
        self.assertIn("has come back 2 times", [
            c for c in cands if c["detector"] == "repeated_returns"
        ][0]["text"])

    def test_two_returns_after_vendor_involvement_still_flag(self):
        m = chain(self.build(2), "A")[-1]
        t = m["tickets"]["300"]
        self.assertEqual(len(t["returns"]), 2)
        self.assertEqual(t["returns_before_vendor"], 2)
        self.assertEqual(m["people"]["eva"]["flags"],
                         ["Ticket returned repeatedly",
                          "Ticket moving backward"])


class MinimumHoldTests(unittest.TestCase):
    def test_default_threshold(self):
        # about half an hour: only a momentary hold is excluded
        self.assertEqual(DEFAULT_THRESHOLDS["worked_min_days"], 0.02)

    def test_short_same_day_work_is_worked_and_delivered(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
        tid = 500
        for sprint, day in (("S36", "2026-10-14"), ("S37", "2026-10-28")):
            for start, end in (("10:00", "12:00"), ("09:00", "14:30")):
                tid += 1
                b.ticket(tid, team="A").on(
                    "%s 08:00" % day, iteration=sprint).on(
                    "%s %s" % (day, start), state="Active",
                    assignee=eva).on(
                    "%s %s" % (day, end), state="Closed")
        ms = chain(b, "A")
        for m, ids in ((ms[1], ["501", "502"]), (ms[2], ["503", "504"])):
            self.assertEqual(m["figures"]["delivered"], 2)
            self.assertEqual(m["people"]["eva"]["delivered_worked_on"], 2)
            for i in ids:
                self.assertEqual(m["tickets"][i]["involvement"], "worked")
            self.assertNotIn("Nothing delivered or handed off",
                             m["people"]["eva"]["flags"])

    def build(self, minutes):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
        dev = b.other("dev")
        b.ticket(400, team="A").on(
            "2026-09-29 09:00", state="Active", assignee=eva,
            iteration="S35").on(
            "2026-09-29 %02d:%02d" % (9 + minutes // 60, minutes % 60),
            assignee=dev).on(
            "2026-10-28 09:00", iteration="S37").on(
            "2026-10-29 09:00", state="Closed")
        b.squad_ticket(401, team="A").on(
            "2026-10-26 09:00", state="Active", iteration="S37")
        return b

    def test_a_hold_of_minutes_is_not_worked(self):
        ms = chain(self.build(10), "A")
        self.assertEqual(ms[0]["tickets"]["400"]["involvement"], "assigned")
        self.assertNotIn("400", ms[2]["tickets"])
        self.assertEqual(ms[2]["figures"]["delivered"], 0)
        self.assertEqual(ms[2]["people"]["eva"]["delivered_worked_on"], 0)

    def test_a_hold_of_a_quarter_day_is_worked(self):
        ms = chain(self.build(6 * 60), "A")
        self.assertEqual(ms[0]["tickets"]["400"]["involvement"], "worked")
        self.assertEqual(ms[2]["tickets"]["400"]["involvement"], "worked")
        self.assertEqual(ms[2]["figures"]["delivered"], 1)


if __name__ == "__main__":
    unittest.main()
