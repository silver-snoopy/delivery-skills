"""What decides whether a first real run finishes: odd tickets (I2),
overlapping iterations (I3), unmapped states listed at once (I4), the squad
set always holding the vendor's tickets (I9) and the probe's extra checks
(I5)."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ledger import analyze
from ledger.ado.client import AdoClient, Malformed
from ledger.ado.collect import collect_team, sprint_calendar
from ledger.ado.probe import run_probe
from ledger.ado.probe_export import CHECK_IDS, export
from ledger.ado.transport import Response
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.errors import LedgerError
from ledger.home import Home
from ledger.metrics import analyze_team_sprint
from ledger.model import Snapshot
from ledger.setup_check import setup_check
from ledger.testing import fake_ado
from ledger.testing.builder import DEFAULT_PHASES, PortfolioBuilder
from ledger.testing.fake_ado import FakeAdo
from ledger.workcal import WorkCalendar

SPRINTS = [
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
]
NOW = datetime(2026, 11, 8, 8, 0, tzinfo=timezone.utc)


def portfolio(n=6, sprints=SPRINTS):
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=sprints)
    anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
    for tid in range(1, n + 1):
        b.ticket(tid, team="Claims", title="Story %d" % tid).on(
            "2026-10-26 09:00", iteration="S37", state="Active",
            assignee=anna, column="Doing").on(
            "2026-10-%d 09:00" % (27 + tid % 3), state="Closed",
            column="Done")
    return b


def make_home(b, fake, config=None):
    home = Home(Path(tempfile.mkdtemp()))
    cfg = b._config_json()
    for k, v in (config or {}).items():
        cfg["teams"][0][k] = v
    home.write_json(cfg, "config.json")
    roster = b._roster_json()
    patch = fake.roster_patch()
    for p in roster["people"]:
        p.update(patch[p["id"]])
    home.write_json(roster, "roster.json")
    return home, load_config(home), load_roster(home)


class Tamper:
    """A transport over FakeAdo that damages chosen tickets' histories."""

    def __init__(self, inner, gap=(), unparsable=(), deny=()):
        self.inner = inner
        self.gap, self.unparsable, self.deny = set(gap), set(unparsable), \
            set(deny)

    def request(self, method, url, body=None):
        resp = self.inner.request(method, url, body)
        for tid in self.deny:
            if "/workItems/%d/revisions" % tid in url:
                return Response(403, {}, {"message": "denied"})
        if resp.status != 200 or "/revisions" not in url:
            return resp
        for tid in self.gap:
            if "/workItems/%d/revisions" % tid in url:
                for v in resp.body["value"]:
                    if v["rev"] > 1:
                        v["rev"] += 1
                        v["fields"]["System.Rev"] = v["rev"]
        for tid in self.unparsable:
            if "/workItems/%d/revisions" % tid in url:
                for v in resp.body["value"]:
                    v["fields"]["System.ChangedDate"] = "not a date"
        return resp


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class OddTicketTests(unittest.TestCase):
    def collect(self, b, **tamper):
        fake = FakeAdo(b)
        self.home, cfg, roster = make_home(b, fake)
        client = AdoClient(Tamper(fake, **tamper), fake.org_url,
                           fake.project)
        return collect_team(self.home, cfg, roster, client, "Claims", NOW)

    def snapshot(self):
        return Snapshot.load(self.home.team_sprint_dir(
            "Claims", "demo-claims-s37") / "snapshot")

    def test_client_raises_malformed_for_a_revision_gap(self):
        b = portfolio()
        fake = FakeAdo(b)
        with self.assertRaises(Malformed) as cm:
            AdoClient(Tamper(fake, gap=[2]), fake.org_url,
                      fake.project).revisions(2)
        self.assertEqual(cm.exception.ticket_id, 2)

    def test_malformed_tickets_are_coverage_gaps_and_collection_continues(
            self):
        r = self.collect(portfolio(10), gap=[2], unparsable=[4])
        snap = self.snapshot()
        self.assertEqual(snap.meta["coverage"],
                         {"missing": [], "unreadable": [],
                          "malformed": [2, 4]})
        self.assertEqual(sorted(t.id for t in snap.tickets),
                         [1, 3, 5, 6, 7, 8, 9, 10])
        self.assertEqual(r["malformed"], 2)
        self.assertFalse(r["coverage_alarm"])
        text = "\n".join(r["warnings"])
        self.assertIn("Ticket 2", text)
        self.assertIn("Ticket 4", text)
        self.assertIn("2 malformed", r["summary"])

    def test_resume_does_not_stop_on_the_same_ticket(self):
        self.collect(portfolio(10), gap=[2])
        self.assertFalse(list(self.home.root.rglob("collect-run.json")))

    def test_alarm_when_a_large_share_cannot_be_read(self):
        r = self.collect(portfolio(10), gap=[1], unparsable=[2],
                         deny=[3, 4, 5])
        self.assertTrue(r["coverage_alarm"])
        self.assertTrue(self.snapshot().meta["coverage_alarm"])
        self.assertIn("COVERAGE ALARM", r["summary"])
        self.assertIn("5 of 10", r["summary"])
        # analysis carries it into the metrics, and the report repeats it
        root = str(self.home.root)
        self.assertEqual(run_main(["--home", root, "analyze", "--team",
                                   "Claims"])[0], 0)
        m = self.home.read_json("teams", "claims", "sprints",
                                "demo-claims-s37", "metrics.json")
        self.assertEqual(m["coverage"], {
            "missing": [], "unreadable": [3, 4, 5], "malformed": [1, 2],
            "alarm": True})
        code, out, _ = run_main(["--home", root, "--json", "report",
                                 "--as-of", "2026-11-08"])
        self.assertEqual(code, 0)
        r = json.loads(out)
        self.assertEqual(r["coverage_alarm"], ["Claims"])
        self.assertIn("COVERAGE ALARM", r["summary"])
        self.assertIn("Claims", r["summary"])

    def test_no_alarm_below_five_tickets_or_a_fifth(self):
        self.assertFalse(self.collect(portfolio(4), deny=[1, 2, 3, 4])[
            "coverage_alarm"])
        self.assertFalse(self.collect(portfolio(30), deny=[1, 2, 3, 4, 5, 6])[
            "coverage_alarm"])


class OverlappingIterationTests(unittest.TestCase):
    def calendar(self, sprints, **team_cfg):
        b = portfolio(2, sprints=sprints)
        fake = FakeAdo(b)
        self.home, cfg, _ = make_home(b, fake, team_cfg)
        self.fake, self.b = fake, b
        warnings = []
        cal = WorkCalendar(cfg.timezone, cfg.holidays)
        out = sprint_calendar(AdoClient(fake, fake.org_url, fake.project),
                              cfg.team("Claims"), cal, warnings)
        return out, warnings

    def test_release_level_iteration_is_excluded_with_a_warning(self):
        out, warnings = self.calendar(
            [("PI 5", "2026-09-28", "2026-12-18")] + SPRINTS)
        self.assertEqual([s.name for s in out], ["S35", "S36", "S37"])
        self.assertEqual(out[0].period_start, out[0].start)
        for prev, s in zip(out, out[1:]):
            self.assertEqual(s.period_start, prev.end)
            self.assertLessEqual(s.period_start, s.start)
        self.assertEqual(len(warnings), 1)
        self.assertIn("PI 5", warnings[0])
        self.assertIn("contains 3 other iterations", warnings[0])

    def test_collect_targets_the_sprint_not_the_release(self):
        self.calendar([("PI 5", "2026-09-28", "2026-12-18")] + SPRINTS)
        cfg, roster = load_config(self.home), load_roster(self.home)
        r = collect_team(self.home, cfg, roster, AdoClient(
            self.fake, self.fake.org_url, self.fake.project), "Claims", NOW)
        self.assertEqual(r["sprint"]["name"], "S37")
        self.assertTrue(any("PI 5" in w for w in r["warnings"]))

    def test_pairwise_overlap_is_refused_naming_both(self):
        with self.assertRaises(LedgerError) as cm:
            self.calendar(SPRINTS + [("S36b", "2026-10-19", "2026-10-30")])
        msg = cm.exception.message + " " + cm.exception.hint
        for word in ("S36b", "S36", "S37", "exclude_iterations"):
            self.assertIn(word, msg)

    def test_excluded_iterations_are_ignored_by_name_or_path(self):
        extra = [("S36b", "2026-10-19", "2026-10-30")]
        for value in ("S36b", "Demo\\Claims\\S36b"):
            out, warnings = self.calendar(SPRINTS + extra,
                                          exclude_iterations=[value])
            self.assertEqual([s.name for s in out], ["S35", "S36", "S37"])
            self.assertEqual(warnings, [])

    def test_sprints_sharing_a_boundary_day_are_consecutive(self):
        out, warnings = self.calendar([
            ("S35", "2026-09-28", "2026-10-12"),
            ("S36", "2026-10-12", "2026-10-23"),
            ("S37", "2026-10-26", "2026-11-06")])
        self.assertEqual([s.name for s in out], ["S35", "S36", "S37"])
        self.assertEqual(out[0].end, out[1].start)
        self.assertEqual(out[1].period_start, out[1].start)
        self.assertEqual(len(warnings), 1)
        for word in ("S35", "S36", "2026-10-12"):
            self.assertIn(word, warnings[0])
        cfg, roster = load_config(self.home), load_roster(self.home)
        r = collect_team(self.home, cfg, roster, AdoClient(
            self.fake, self.fake.org_url, self.fake.project), "Claims", NOW)
        self.assertEqual(r["sprint"]["name"], "S37")
        self.assertTrue(any("2026-10-12" in w for w in r["warnings"]))

    def test_one_day_iteration_inside_a_sprint_is_not_a_boundary(self):
        inside = [("L", "2026-10-05", "2026-10-23"),
                  ("D", "2026-10-12", "2026-10-12"),
                  ("S37", "2026-10-26", "2026-11-06")]
        with self.assertRaises(LedgerError) as cm:
            self.calendar(inside)
        self.assertIn("L", cm.exception.message)
        self.assertIn("D", cm.exception.message)
        self.assertIn("exclude_iterations", cm.exception.hint)
        out, warnings = self.calendar(inside, exclude_iterations=["D"])
        self.assertEqual([s.name for s in out], ["L", "S37"])
        cal = WorkCalendar("Europe/Zurich", [])
        from datetime import date
        self.assertEqual((out[0].start, out[0].end), cal.sprint_bounds(
            date(2026, 10, 5), date(2026, 10, 23)))
        self.assertEqual(warnings, [])

    def test_three_sprints_each_sharing_a_boundary_day_chain(self):
        out, warnings = self.calendar([
            ("S35", "2026-09-28", "2026-10-12"),
            ("S36", "2026-10-12", "2026-10-26"),
            ("S37", "2026-10-26", "2026-11-06")])
        self.assertEqual([s.name for s in out], ["S35", "S36", "S37"])
        for prev, s in zip(out, out[1:]):
            self.assertEqual(prev.end, s.start)
            self.assertEqual(s.period_start, s.start)
        self.assertEqual(len(warnings), 2)

    def test_two_day_overlap_still_raises(self):
        with self.assertRaises(LedgerError) as cm:
            self.calendar([("S35", "2026-09-28", "2026-10-13"),
                           ("S36", "2026-10-12", "2026-10-23"),
                           ("S37", "2026-10-26", "2026-11-06")])
        self.assertIn("S35", cm.exception.message)
        self.assertIn("S36", cm.exception.message)

    def test_exclude_entry_matching_no_iteration_warns(self):
        out, warnings = self.calendar(SPRINTS,
                                      exclude_iterations=["PI 9", "s36"])
        self.assertEqual([s.name for s in out], ["S35", "S37"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("PI 9", warnings[0])
        self.assertIn("exclude_iterations", warnings[0])
        self.assertNotIn("s36", warnings[0])

    def test_exclude_iterations_must_be_a_list_of_strings(self):
        b = portfolio(1)
        with self.assertRaises(LedgerError):
            make_home(b, FakeAdo(b), {"exclude_iterations": "S36b"})

    def test_period_start_never_after_the_sprints_own_start(self):
        # two sprints with a gap between them, then back to back
        out, _ = self.calendar([("S35", "2026-09-28", "2026-10-02"),
                                ("S36", "2026-10-12", "2026-10-23"),
                                ("S37", "2026-10-26", "2026-11-06")])
        self.assertEqual(out[1].period_start, out[0].end)
        self.assertLess(out[1].period_start, out[1].start)


class UnmappedStateTests(unittest.TestCase):
    def home_with(self, b):
        home = Home(Path(tempfile.mkdtemp()))
        b.write(home)
        return home

    def build(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.ticket(1, team="Claims").on(
            "2026-09-01 09:00", state="Design").on(
            "2026-10-26 09:00", state="Active", assignee=anna,
            iteration="S37").on("2026-10-28 09:00", state="Closed")
        b.ticket(2, team="Claims").on(
            "2026-09-02 09:00", state="Design", assignee=anna).on(
            "2026-10-27 09:00", state="Blocked").on(
            "2026-10-29 09:00", state="Active", iteration="S37")
        b.squad_ticket(3, team="Claims").on(
            "2025-03-03 09:00", state="Triage").on(
            "2026-10-26 09:00", state="Active", iteration="S37")
        return b

    def test_analyze_lists_every_unmapped_state_at_once(self):
        home = self.home_with(self.build())
        code, _, err = run_main(["--home", str(home.root), "analyze",
                                 "--team", "Claims"])
        self.assertEqual(code, 2)
        self.assertIn("3 board states of team Claims have no phase rule",
                      err)
        self.assertIn('{"state": "Blocked", "phase": "?"}  (1 ticket)', err)
        self.assertIn('{"state": "Design", "phase": "?"}  (2 tickets)', err)
        self.assertIn('{"state": "Triage", "phase": "?"}  (1 ticket)', err)
        self.assertIn("todo, doing, handoff, done or removed", err)
        self.assertFalse(list(home.root.rglob("metrics.json")))

    def test_column_is_named_when_the_state_has_column_rules(self):
        phases = [p for p in DEFAULT_PHASES if p["state"] != "Active"] + [
            {"state": "Active", "column": "Doing", "phase": "doing"}]
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("Claims", area="Demo\\Claims", sprints=SPRINTS,
               phases=phases)
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.ticket(1, team="Claims").on(
            "2026-10-26 09:00", state="Active", column="Doing",
            assignee=anna, iteration="S37").on(
            "2026-10-28 09:00", column="Test").on(
            "2026-10-30 09:00", state="Closed")
        home = self.home_with(b)
        code, _, err = run_main(["--home", str(home.root), "analyze",
                                 "--team", "Claims"])
        self.assertEqual(code, 2)
        self.assertIn('{"state": "Active", "column": "Test", "phase": "?"}'
                      "  (1 ticket)", err)

    def test_mapped_board_analyses(self):
        home = self.home_with(portfolio(3))
        self.assertEqual(run_main(["--home", str(home.root), "analyze",
                                   "--team", "Claims"])[0], 0)

    def test_setup_check_reports_probed_states_without_a_rule(self):
        b = portfolio(1)
        fake = FakeAdo(b)
        home, _, _ = make_home(b, fake)
        home.write_json({"states": {
            "User Story": [{"name": n, "category": "InProgress"} for n in (
                "New", "Active", "In QA", "In Review", "Closed", "Removed",
                "Design", "Blocked")],
            "Epic": [{"name": "Funnel", "category": "Proposed"}]}},
            "probe", "probe-report.json")
        r = setup_check(home)
        self.assertEqual(r["problems"], [
            "team Claims: state 'Blocked' (User Story) was recorded by the "
            "probe and has no phase rule",
            "team Claims: state 'Design' (User Story) was recorded by the "
            "probe and has no phase rule"])


class SquadSubsetTests(unittest.TestCase):
    def test_ticket_moved_to_another_area_after_the_sprint_still_counts(
            self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        for tid in range(1, 8):
            t = b.ticket(tid, team="Claims").on(
                "2026-10-26 09:00", iteration="S37", state="Active",
                assignee=anna).on("2026-10-29 09:00", state="Closed")
            if tid == 7:  # closed in the sprint, moved away before collection
                t.on("2026-11-07 07:00", area="Demo\\Archive")
        snap = b.snapshot(cutoff="2026-11-07 08:00")
        # the squad query (area UNDER the team's path) no longer finds it
        snap.squad = [t for t in snap.squad if t.id != 7]
        sp = snap.sprints["Claims"][-1]
        m, led = analyze_team_sprint(snap, b.config(), b.roster(), "Claims",
                                     sp)
        self.assertEqual(m["figures"]["delivered"], 7)
        self.assertEqual(m["figures"]["squad_delivered"], 7)
        self.assertIn(7, led["figures.squad_delivered"])
        self.assertEqual(m["warnings"], [])


class Scripted:
    def __init__(self, handler):
        self.handler, self.requests = handler, []

    def request(self, method, url, body=None):
        self.requests.append((method, url, body))
        return self.handler(method, url, body)


class TimeoutShapeTests(unittest.TestCase):
    def ids(self, slow):
        import re

        def handler(method, url, body):
            q = body["query"]
            a = int(re.search(r"\[System\.Id\] >= (\d+)", q).group(1))
            m = re.search(r"\[System\.Id\] < (\d+)", q)
            if m is None:
                return Response(200, {}, {"workItems": []})
            b = int(m.group(1))
            if b - a > 500:
                return slow
            return Response(200, {}, {"workItems": [
                {"id": i} for i in (10, 700) if a <= i < b]})
        t = Scripted(handler)
        return AdoClient(t, "https://dev.azure.com/demo", "Demo").wiql_ids(
            "X", id_range=(1, 2001)), t

    def test_timeout_in_a_400_or_500_body_splits_the_query(self):
        for slow in (
                Response(400, {}, {"message": "VS402335: The query has "
                                              "exceeded the time limit."}),
                Response(500, {}, {"message": "Query timeout expired."}),
                Response(500, {}, None, "The operation timed out"),
                Response(503, {}, {"message": "A TIMEOUT occurred"})):
            ids, t = self.ids(slow)
            self.assertEqual(ids, [10, 700])
            self.assertGreater(len(t.requests), 3)

    def test_other_errors_still_stop(self):
        with self.assertRaises(LedgerError):
            self.ids(Response(400, {}, {"message": "TF51005: bad field"}))


class ProbeAdditionTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(Path(tempfile.mkdtemp()))
        self.lines = []

    def probe(self, b, fake=None, transport=None, **kw):
        fake = fake or FakeAdo(b)
        transport = transport or fake
        ticks = iter(range(10000))
        kw.setdefault("alias", fake.alias("p:anna"))
        return run_probe(
            self.home, lambda kind: AdoClient(transport, fake.org_url,
                                              fake.project),
            fake.org_url, fake.project, "Claims", timezone="Europe/Zurich",
            now=lambda: float(next(ticks)), progress=self.lines.append,
            **kw), fake

    def check(self, report, cid):
        return next(c for c in report["checks"] if c["id"] == cid)

    def test_new_checks_are_registered_and_exported(self):
        self.assertEqual(CHECK_IDS[-2:], ("A15", "A16"))
        report, _ = self.probe(portfolio(6), area="Demo\\Claims")
        self.assertEqual([c["id"] for c in report["checks"]],
                         list(CHECK_IDS))
        for cid in ("A4", "A15", "A16"):
            self.assertEqual(self.check(report, cid)["status"], "observed",
                             self.check(report, cid))
        self.assertEqual(report["counts"]["discovery_vendor_unfinished"], 0)
        self.assertEqual(report["counts"]["discovery_vendor_recent"], 6)
        self.assertEqual(report["counts"]["discovery_squad"], 6)
        # the test clock ticks once per reading: one start, one end
        self.assertEqual(report["timing"]["discovery_seconds"], 1.0)
        ex = export(report)
        self.assertEqual(ex["counts"]["discovery_squad"], 6)
        self.assertEqual(ex["timing"]["discovery_seconds"], 1.0)
        self.assertNotIn("Demo", json.dumps(ex))

    def test_iteration_path_format_mismatch_fails_showing_both(self):
        b = portfolio(3)

        class OtherFormat:
            def __init__(self, inner):
                self.inner = inner

            def request(self, method, url, body=None):
                resp = self.inner.request(method, url, body)
                if "teamsettings/iterations" in url and resp.status == 200:
                    for v in resp.body["value"]:
                        v["path"] = "\\Demo\\Iteration\\" + v["name"]
                return resp
        fake = FakeAdo(b)
        report, _ = self.probe(b, fake=fake, transport=OtherFormat(fake))
        a15 = self.check(report, "A15")
        self.assertEqual((a15["status"], a15["code"]), ("failed",
                                                        "mismatch"))
        self.assertIn("\\Demo\\Iteration\\S3", a15["detail"])
        self.assertIn("Demo\\Claims\\S37", a15["detail"])
        self.assertNotIn("Demo", json.dumps(export(report)))

    def test_partition_check_is_capped_and_prints_progress(self):
        b = portfolio(60)
        report, fake = self.probe(b)
        a4 = self.check(report, "A4")
        self.assertEqual((a4["status"], a4["sample"]), ("observed", 25))
        self.assertIn("25", a4["detail"])
        self.assertTrue(any(l.startswith("A4:") for l in self.lines))
        self.assertTrue(any(l.startswith("A16:") for l in self.lines))
        # nowhere near three queries per ticket the alias ever touched
        posts = [r for r in fake.requests if r[0] == "POST"]
        self.assertLess(len(posts), 120)

    def test_discovery_without_an_area_skips_the_squad_query(self):
        report, _ = self.probe(portfolio(3))
        a16 = self.check(report, "A16")
        self.assertEqual(a16["status"], "observed")
        self.assertIn("squad query not run", a16["detail"])
        self.assertNotIn("discovery_squad", report["counts"])

    def test_discovery_fetches_no_histories(self):
        b = portfolio(6)
        fake = FakeAdo(b)
        seen = []

        class Watch:
            def request(self, method, url, body=None):
                seen.append((len(self_lines), url))
                return fake.request(method, url, body)
        self_lines = self.lines
        self.probe(b, fake=fake, transport=Watch(), area="Demo\\Claims")
        start = next(i for i, l in enumerate(self.lines)
                     if l.startswith("A16:"))
        during = [u for n, u in seen if n > start]
        self.assertTrue(during)
        self.assertFalse([u for u in during if "/revisions" in u])


if __name__ == "__main__":
    unittest.main()
