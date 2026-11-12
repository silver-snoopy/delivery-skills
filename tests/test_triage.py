"""Small fixes from the final review's triage table."""
import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from ledger import analyze, annotations, history
from ledger.buckets import first_vendor_hold
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.errors import LedgerError
from ledger.evidence import build_person_brief
from ledger.home import Home
from ledger.metrics import analyze_team_sprint
from ledger.model import Snapshot
from ledger.phases import PhaseMapper
from ledger.render.evidence_cards import evidence_cards
from ledger.testing.builder import DEFAULT_PHASES, PortfolioBuilder
from ledger.timeline import build_timeline
from ledger.workcal import WorkCalendar

try:  # run by discovery (tests/ on the path) or as tests.<module>
    import test_history
    import test_issue
    import test_render
except ImportError:
    from tests import test_history, test_issue, test_render

SPRINTS = [
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
]
CUTOFF = "2026-11-07 08:00"


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def base(**kw):
    b = PortfolioBuilder(tz="Europe/Zurich", **kw)
    b.team("A", area="Demo\\A", sprints=SPRINTS)
    eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
    return b, eva, b.other("qa")


def written(b, team="A"):
    home = Home(Path(tempfile.mkdtemp()) / "home")
    home.ensure()
    b.write(home)
    last = b.sprints(team)[-1]
    b.snapshot(cutoff=CUTOFF, team=team).save(
        home.team_sprint_dir(team, last.id) / "snapshot")
    return home


class ExplainLabelTests(unittest.TestCase):
    def test_explain_prints_the_outcome_label(self):
        b, eva, qa = base()
        b.ticket(1, team="A", title="One").on(
            "2026-10-26 09:00", state="Active", assignee=eva,
            iteration="S37").on("2026-10-28 09:00", state="In QA",
                                assignee=qa)
        home = written(b)
        root = str(home.root)
        self.assertEqual(run_main(["--home", root, "analyze", "--team",
                                   "A"])[0], 0)
        code, out, _ = run_main([
            "--home", root, "explain", "--team", "A", "--sprint",
            "demo-a-s37", "figures.awaiting_acceptance.count"])
        self.assertEqual(code, 0)
        self.assertIn("Finished, awaiting acceptance", out)
        self.assertNotIn("awaiting_acceptance\n", out + "\n")


class ContextTests(unittest.TestCase):
    def apply(self, **kw):
        m = test_history.metrics(6, people={"anna": test_history.person(
            **kw)})
        history.apply(m, test_history.ledger_for(anna={}), [],
                      test_history.cfg())
        return m["people"]["anna"]["context"]

    def test_no_allocation_context_for_a_mid_sprint_joiner(self):
        # full-time from the sprint's sixth working day: 5 of 10 nominal
        ctx = self.apply(available_days=5.0, nominal_days=5.0,
                         is_joiner=True, allocation=1.0)
        self.assertEqual(ctx, ["Joined in S37"])

    def test_allocation_context_for_a_part_time_person(self):
        self.assertEqual(self.apply(available_days=5.0, nominal_days=5.0,
                                    allocation=0.5), ["Allocation 0.5"])

    def test_metrics_record_the_allocation(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        b.person("eva", "Eva M.", team="A", start="2026-11-02")
        b.person("ian", "Ian P.", team="A", start="2026-01-05",
                 allocation=0.5)
        b.ticket(1, team="A").on("2026-11-02 09:00", state="Active",
                                 assignee="p:eva", iteration="S37")
        snap = b.snapshot(cutoff=CUTOFF)
        cfg = b.config()
        m, led = analyze_team_sprint(snap, cfg, b.roster(), "A",
                                     snap.sprints["A"][-1])
        history.apply(m, led, [], cfg)
        self.assertEqual(m["people"]["eva"]["allocation"], 1.0)
        self.assertEqual(m["people"]["eva"]["nominal_days"], 5.0)
        self.assertNotIn("Allocation 0.5", m["people"]["eva"]["context"])
        self.assertEqual(m["people"]["ian"]["allocation"], 0.5)
        self.assertIn("Allocation 0.5", m["people"]["ian"]["context"])


class EvidenceTimelineTests(unittest.TestCase):
    def test_a_waiting_segment_names_no_holder(self):
        b, eva, qa = base()
        t = b.ticket(1, team="A", title="One").on(
            "2026-10-26 09:00", state="Active", assignee=eva,
            iteration="S37")
        # the engineer stays assigned while the ticket waits in QA
        for day in ("27", "29"):
            t.on("2026-10-%s 09:00" % day, state="In QA").on(
                "2026-10-%d 09:00" % (int(day) + 1), state="Active")
        home = written(b)
        cfg, roster = load_config(home), load_roster(home)
        analyze.run_analyze(home, cfg, roster, "A")
        code, _, err = run_main(["--home", str(home.root), "report",
                                 "--as-of", "2026-11-07"])
        self.assertEqual(code, 0, err)
        dm = (home.path("reports", "2026-11-07", "draft")
              / "delivery-manager.html").read_text(encoding="utf-8")
        card = dm.split('id="evidence-1"')[1].split("</article>")[0]
        self.assertIn("Waiting: QA", card)
        self.assertNotIn("Eva M., Waiting: QA", card)
        self.assertIn("Eva M., in progress", card)


class PendingTests(unittest.TestCase):
    def test_pending_skips_carried_tickets_another_party_holds(self):
        b, eva, qa = base()
        b.ticket(1, team="A").on(
            "2026-10-26 09:00", state="Active", assignee=eva,
            iteration="S37")
        b.ticket(2, team="A").on(
            "2026-10-26 09:00", state="Active", assignee=eva,
            iteration="S37").on("2026-11-02 09:00",
                                assignee=b.other("dev"))
        snap = b.snapshot(cutoff=CUTOFF)
        m, _ = analyze_team_sprint(snap, b.config(), b.roster(), "A",
                                   snap.sprints["A"][-1])
        self.assertEqual([i["ticket"] for i in annotations.pending(m, [])],
                         [1])


class ThresholdTypeTests(unittest.TestCase):
    def test_thresholds_must_be_numbers(self):
        for bad in ("3", None, [3], True):
            b = PortfolioBuilder(thresholds={"quiet_days": bad})
            b.team("A", area="Demo\\A")
            with self.assertRaises(LedgerError) as cm:
                b.config()
            self.assertIn("quiet_days", cm.exception.message)
            self.assertIn("number", cm.exception.message)


class ErrorLineTests(unittest.TestCase):
    def test_no_bare_next_line(self):
        def boom(args, home):
            raise LedgerError("it broke")
        with mock.patch("ledger.status._status_cmd", boom):
            code, _, err = run_main(["--home", tempfile.mkdtemp(), "status"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "error: it broke\n")

    def test_next_line_when_there_is_a_hint(self):
        def boom(args, home):
            raise LedgerError("it broke", "do this")
        with mock.patch("ledger.status._status_cmd", boom):
            _, _, err = run_main(["--home", tempfile.mkdtemp(), "status"])
        self.assertEqual(err, "error: it broke\nnext: do this\n")


class DirectoryModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)

    def modes(self, top):
        return {str(p.relative_to(self.tmp)): stat.S_IMODE(p.stat().st_mode)
                for p in [top] + list(top.rglob("*")) if p.is_dir()}

    def test_home_ensure_creates_parents_private(self):
        Home(self.tmp / "a" / "b" / "home").ensure()
        self.assertEqual(set(self.modes(self.tmp / "a").values()), {0o700})

    def test_snapshot_save_creates_parents_private(self):
        b, eva, qa = base()
        b.ticket(1, team="A").on("2026-10-26 09:00")
        b.snapshot().save(self.tmp / "teams" / "a" / "sprints" / "s"
                          / "snapshot")
        modes = self.modes(self.tmp / "teams")
        self.assertEqual(len(modes), 5)
        self.assertEqual(set(modes.values()), {0o700})


class FirstVendorHoldTests(unittest.TestCase):
    def test_a_hold_while_done_or_removed_is_not_a_receipt(self):
        b, eva, qa = base()
        dev = b.other("dev")
        b.ticket(1, team="A").on(
            "2026-10-12 09:00", state="Active", assignee=dev,
            iteration="S36").on(
            "2026-10-14 09:00", state="Closed").on(
            "2026-10-15 09:00", assignee=eva).on(           # while done
            "2026-10-27 09:00", state="Active", iteration="S37")
        b.ticket(2, team="A").on(
            "2026-10-12 09:00", state="Removed", assignee=eva).on(
            "2026-10-28 09:00", state="New")
        snap = b.snapshot(cutoff=CUTOFF)
        cfg, roster = b.config(), b.roster()
        cal = WorkCalendar(cfg.timezone, cfg.holidays)
        cut = b.parse_time(CUTOFF)
        got = {}
        for t in snap.tickets:
            tl = build_timeline(t, PhaseMapper(cfg.team("A"), cfg), cut,
                                set())
            got[t.id] = first_vendor_hold(tl, "A", roster, cal.tz)
        self.assertEqual(got[1], b.parse_time("2026-10-27 09:00"))
        self.assertEqual(got[2], b.parse_time("2026-10-28 09:00"))


class MarkerRollbackTests(test_issue.IssueTests):
    """Runs on the issue tests' fixture; only the test below is new."""

    def test_failed_second_edition_keeps_the_first_marker(self):
        SID, DSID, AS_OF = test_issue.SID, test_issue.DSID, test_issue.AS_OF
        self.report()
        self.assertEqual(self.ok("issue", "--as-of", AS_OF)["edition"], 1)
        self.report()
        real = Home.write_json
        calls = []

        def flaky(home, obj, *parts):
            if parts[-1] == "issued.json":
                calls.append(parts)
                if len(calls) == 2:
                    raise OSError("marker failed")
            return real(home, obj, *parts)

        with mock.patch.object(Home, "write_json", flaky):
            self.fails("issue", "--as-of", AS_OF, "--note", "second")
        self.assertTrue((self.edition(2) / "manifest.json").is_file())
        # every sprint is still protected by its earlier marker
        for team, sid in (("Claims", SID), ("Data", DSID)):
            marker = self.home.read_json(
                *self.home.team_sprint_dir(team, sid).relative_to(
                    self.home.root).parts, "issued.json")
            self.assertEqual(marker["edition"], 1, team)
        # the re-run finishes edition 2 instead of making a third
        r = self.ok("issue", "--as-of", AS_OF, "--note", "second")
        self.assertEqual(r["edition"], 2)
        self.assertFalse(self.edition(3).exists())
        for team, sid in (("Claims", SID), ("Data", DSID)):
            self.assertEqual(self.home.read_json(
                *self.home.team_sprint_dir(team, sid).relative_to(
                    self.home.root).parts, "issued.json")["edition"], 2)


def _only_new(cls, keep):
    for name in dir(cls):
        if name.startswith("test_") and name not in keep:
            setattr(cls, name, None)


_only_new(MarkerRollbackTests,
          {"test_failed_second_edition_keeps_the_first_marker"})


class SprintPeriodsTests(unittest.TestCase):
    def test_sprints_means_distinct_sprint_periods(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        b.team("B", area="Demo\\B", sprints=[
            (n.replace("S", "B"), s, f) for n, s, f in SPRINTS])
        b.person("eva", "Eva M.", team="A", start="2026-01-05",
                 allocation=0.5)
        b.assign("eva", "B", start="2026-01-05", allocation=0.5)
        for team in ("A", "B"):
            for k, (_, start, _f) in enumerate(SPRINTS):
                b.ticket(100 * (1 + (team == "B")) + k, team=team).on(
                    start + " 09:00", state="Active", assignee="p:eva",
                    iteration=b.sprints(team)[k].name)
        home = Home(Path(tempfile.mkdtemp()) / "home")
        home.ensure()
        b.write(home)
        cfg, roster = load_config(home), load_roster(home)
        for team in ("A", "B"):
            b.snapshot(cutoff=CUTOFF, team=team).save(
                home.team_sprint_dir(team, b.sprints(team)[-1].id)
                / "snapshot")
            analyze.run_analyze(home, cfg, roster, team)
        brief = build_person_brief(home, cfg, roster, "eva", sprints=2,
                                   as_of=date(2026, 11, 7))
        self.assertEqual([(r["team"], r["sprint"]) for r in brief["rows"]],
                         [("A", "S36"), ("B", "B36"), ("A", "S37"),
                          ("B", "B37")])


class TraceMapperTests(unittest.TestCase):
    def test_trace_reads_each_interval_with_its_areas_team_rules(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A", sprints=SPRINTS)
        # team B reads "In Review" as work in progress
        b.team("B", area="Demo\\B", sprints=SPRINTS, phases=[
            dict(p, phase="doing") if p["state"] == "In Review" else p
            for p in DEFAULT_PHASES])
        for p in b._teams["B"]["phases"]:
            if p["state"] == "In Review":
                p.pop("wait_label", None)
        eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
        b.ticket(1, team="A", title="Moved").on(
            "2026-10-26 09:00", state="In Review", assignee=eva,
            iteration="S37").on(
            "2026-10-29 09:00", area="Demo\\B").on(
            "2026-11-03 09:00", state="Closed")
        home = written(b)
        cfg, roster = load_config(home), load_roster(home)
        d = analyze.trace_data(home, cfg, roster, 1, "A")
        self.assertEqual([(i["phase"], i["days"]) for i in d["intervals"]
                          if i["state"] == "In Review"],
                         [("handoff", 3.0), ("doing", 3.0)])


if __name__ == "__main__":
    unittest.main()
