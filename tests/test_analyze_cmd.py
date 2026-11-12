import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from ledger.analyze import run_analyze
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.home import Home
from ledger.testing.builder import DEFAULT_PHASES, PortfolioBuilder

SPRINTS = [
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
    ("S38", "2026-11-09", "2026-11-20"),
]


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def portfolio(phases=None):
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS, phases=phases)
    anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
    qa = b.other("qa")
    b.ticket(1, team="Claims", title="Claim intake form").on(
        "2026-10-20 09:00", iteration="S37").on(
        "2026-10-26 09:00", state="Active", assignee=anna).on(
        "2026-10-27 09:00", state="In QA", assignee=qa).on(
        "2026-11-09 09:00", state="Active", assignee=anna,
        iteration="S38").on(
        "2026-11-12 09:00", state="Closed")
    b.ticket(2, team="Claims", title="Policy lookup").on(
        "2026-09-28 09:00", iteration="S35", state="Active",
        assignee=anna).on(
        "2026-10-01 09:00", state="Closed")
    # a late revision so the cutoff falls after S38 ends
    b.ticket(3, team="Claims", title="Later work").on("2026-11-23 09:00")
    return b


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.home = Home(Path(self.dir))

    def write(self, b=None):
        (b or portfolio()).write(self.home)
        return load_config(self.home), load_roster(self.home)

    def test_backfill_orders_and_marks(self):
        cfg, roster = self.write()
        res = run_analyze(self.home, cfg, roster, "Claims", None)
        self.assertEqual([s["name"] for s in res["sprints"]],
                         ["S35", "S36", "S37", "S38"])
        flags = []
        for s in res["sprints"]:
            d = self.home.team_sprint_dir("Claims", s["id"])
            m = json.loads((d / "metrics.json").read_text())
            self.assertTrue((d / "ledger.json").exists())
            flags.append(m["backfilled"])
            self.assertIn("status", m)
        self.assertEqual(flags, [True, True, True, False])
        last = json.loads((self.home.team_sprint_dir(
            "Claims", res["sprints"][-1]["id"]) / "metrics.json").read_text())
        self.assertEqual(last["figures"]["trend_delivered"], [1, 0, 0, 1])
        self.assertEqual(last["figures"]["returns"]["cohort"]["sprint"],
                         "S37")

    def test_issued_sprint_is_not_recomputed(self):
        cfg, roster = self.write()
        first = run_analyze(self.home, cfg, roster, "Claims", None)
        d = self.home.team_sprint_dir("Claims", first["sprints"][0]["id"])
        doc = json.loads((d / "metrics.json").read_text())
        doc["figures"]["delivered"] = 7
        self.home.write_json(doc, *d.relative_to(self.dir).parts,
                             "metrics.json")
        self.home.write_json({}, *d.relative_to(self.dir).parts,
                             "issued.json")
        res = run_analyze(self.home, cfg, roster, "Claims", None)
        self.assertEqual([s["recomputed"] for s in res["sprints"]],
                         [False, True, True, True])
        last = json.loads((self.home.team_sprint_dir(
            "Claims", res["sprints"][-1]["id"]) / "metrics.json").read_text())
        self.assertEqual(last["figures"]["trend_delivered"][0], 7)

    def test_no_snapshot_hints_collect(self):
        self.write()
        code, _, err = run_main(["--home", self.dir, "analyze", "--team",
                                 "Nope"])
        self.assertEqual(code, 2)
        self.assertIn("collect", err)

    def test_analyze_command(self):
        self.write()
        code, out, _ = run_main(["--home", self.dir, "analyze", "--team",
                                 "Claims"])
        self.assertEqual(code, 0, out)
        self.assertIn("S38", out)
        code, out, _ = run_main(["--home", self.dir, "--json", "analyze",
                                 "--all"])
        self.assertEqual(code, 0)
        self.assertIn("summary", json.loads(out))

    def sprint_id(self, name):
        return [s for s in portfolio().sprints("Claims")
                if s.name == name][0].id

    def test_explain_lists_tickets(self):
        self.write()
        run_main(["--home", self.dir, "analyze", "--team", "Claims"])
        base = ["explain", "--team", "Claims", "--sprint",
                self.sprint_id("S38"), "figures.delivered"]
        # the builder's config says agent_text_access "none"
        code, out, _ = run_main(["--home", self.dir] + base)
        self.assertEqual(code, 0)
        self.assertNotIn("Claim intake form", out)
        self.assertIn("[text hidden: agent_text_access is none]", out)
        code, out, _ = run_main(["--home", self.dir, "--show-text"] + base)
        self.assertEqual(code, 0)
        self.assertIn("1", out)
        self.assertIn("User Story", out)
        self.assertIn("Claim intake form", out)
        self.assertIn("delivered", out)
        code, out, _ = run_main(["--home", self.dir, "explain", "--team",
                                 "Claims", "--sprint", self.sprint_id("S38"),
                                 "people.anna.returns"])
        self.assertEqual(code, 0)
        self.assertIn("rev 4", out)

    def test_explain_unknown_figure(self):
        self.write()
        run_main(["--home", self.dir, "analyze", "--team", "Claims"])
        code, _, err = run_main(["--home", self.dir, "explain", "--team",
                                 "Claims", "--sprint", self.sprint_id("S38"),
                                 "figures.nope"])
        self.assertEqual(code, 2)
        self.assertIn("figures.nope", err)
        self.assertIn("figures.delivered", err)

    def test_trace_output(self):
        self.write()
        run_main(["--home", self.dir, "analyze", "--team", "Claims"])
        code, out, _ = run_main(["--home", self.dir, "trace", "1"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("Claim intake form", out)
        code, out, _ = run_main(["--home", self.dir, "--show-text", "trace",
                                 "1"])
        self.assertEqual(code, 0, out)
        self.assertIn("Claim intake form", out)
        self.assertIn("In QA", out)
        self.assertIn("9.0", out)
        self.assertIn("Anna K.", out)
        self.assertIn("returned", out)
        self.assertIn("S38", out)
        self.assertIn("Delivered", out)

    def test_trace_unknown_ticket(self):
        self.write()
        code, _, err = run_main(["--home", self.dir, "trace", "999"])
        self.assertEqual(code, 2)
        self.assertIn("999", err)

    def test_unmapped_state_surfaces_from_analyze(self):
        b = portfolio(phases=[p for p in DEFAULT_PHASES
                              if p["state"] != "In QA"])
        self.write(b)
        code, _, err = run_main(["--home", self.dir, "analyze", "--team",
                                 "Claims"])
        self.assertEqual(code, 2)
        self.assertIn("In QA", err)


if __name__ == "__main__":
    unittest.main()
