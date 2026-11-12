import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ledger.ado.collect import RUN_FILE
from ledger.cli import main
from ledger.home import Home
from unittest import mock


def run_status(root):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--home", str(root), "--json", "status"])
    return code, json.loads(out.getvalue()) if code == 0 else None, err.getvalue()


def run_text(root):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--home", str(root), "status"])
    return out.getvalue()


class StatusTest(unittest.TestCase):
    def mkdtemp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def test_empty_home(self):
        root = self.mkdtemp()
        code, r, _ = run_status(root)
        self.assertEqual(code, 0)
        self.assertEqual(r["home"], root)
        self.assertFalse(r["config_ok"])
        self.assertFalse(r["roster_ok"])
        self.assertEqual(r["problems"], ["config.json: missing",
                                         "roster.json: missing"])
        self.assertIsNone(r["agent_text_access"])
        self.assertIsNone(r["timezone"])
        self.assertEqual(r["teams"], [])
        self.assertIsNone(r["latest_draft"])
        self.assertIn("data home: %s" % root, r["summary"])

    def test_malformed_config_is_one_line_problem(self):
        root = Path(self.mkdtemp())
        (root / "config.json").write_text("{not json", encoding="utf-8")
        (root / "roster.json").write_text('{"people": []}', encoding="utf-8")
        code, r, err = run_status(root)
        self.assertEqual(code, 0, err)
        self.assertFalse(r["config_ok"])
        self.assertTrue(r["roster_ok"])
        self.assertEqual(len(r["problems"]), 1)
        self.assertTrue(r["problems"][0].startswith("config.json: "))
        self.assertNotIn("\n", r["problems"][0])
        self.assertNotIn("Traceback", err)
        (root / "config.json").write_text('{"ado": {}}', encoding="utf-8")
        code, r, err = run_status(root)
        self.assertEqual(code, 0, err)
        self.assertFalse(r["config_ok"])
        self.assertIn("config.json: ", r["problems"][0])

    def test_demo_home(self):
        root = Path(self.mkdtemp()) / "demo"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["demo", "--home", str(root)]), 0)
        home = Home(root)
        pm = home.team_sprint_dir("Pricing Models", "demo-pricing-models-pm-23")
        home.write_json({"edition": 2, "as_of": "2026-11-07",
                         "issued_at": "2026-11-07T10:00:00Z"},
                        *pm.relative_to(root).parts, "issued.json")
        home.write_json({"cutoff": "x"}, *(pm / "snapshot").relative_to(
            root).parts, RUN_FILE)
        (pm / "snapshot" / "tickets.jsonl").write_text("not json\n")
        code, r, err = run_status(root)
        self.assertEqual(code, 0, err)
        self.assertTrue(r["config_ok"] and r["roster_ok"], r["problems"])
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["agent_text_access"], "none")
        self.assertEqual(r["timezone"], "Europe/Zurich")
        self.assertEqual(r["latest_draft"], "2026-11-07")
        self.assertEqual([t["name"] for t in r["teams"]],
                         ["Claims Intake", "Pricing Models", "Data Platform"])
        t = r["teams"][1]
        self.assertEqual(t["latest_collected"], {
            "id": "demo-pricing-models-pm-23", "name": "PM-23",
            "finish": "2026-11-06", "cutoff": "2026-11-07 08:00"})
        self.assertEqual(t["latest_analysed"], {
            "id": "demo-pricing-models-pm-23", "name": "PM-23",
            "finish": "2026-11-06"})
        self.assertEqual(t["latest_issued"],
                         {"edition": 2, "as_of": "2026-11-07"})
        self.assertEqual(t["interrupted_collection"],
                         ["demo-pricing-models-pm-23"])
        claims = r["teams"][0]
        self.assertIsNone(claims["latest_issued"])
        self.assertEqual(claims["interrupted_collection"], [])
        text = run_text(root)
        self.assertIn("Pricing Models: collected PM-23 (finished 2026-11-06, "
                      "cut-off 2026-11-07 08:00); analysed PM-23; issued in "
                      "edition 002 of 2026-11-07; interrupted collection",
                      text)
        self.assertIn("agent text access: none", text)


    def demo(self):
        root = Path(self.mkdtemp()) / "demo"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["demo", "--home", str(root)]), 0)
        return root, Home(root)

    def assert_one_problem(self, root, *words):
        code, r, err = run_status(root)
        self.assertEqual(code, 0, err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(len(r["problems"]), 1, r["problems"])
        self.assertNotIn("\n", r["problems"][0])
        for w in words:
            self.assertIn(w, r["problems"][0])
        run_text(root)  # the text summary renders too
        return r

    def test_malformed_issued_marker(self):
        root, home = self.demo()
        pm = home.team_sprint_dir("Pricing Models", "demo-pricing-models-pm-23")
        for marker in ({"as_of": "2026-11-07"}, {"edition": None}, [1]):
            (pm / "issued.json").write_text(json.dumps(marker))
            r = self.assert_one_problem(root, "issued.json")
            self.assertIsNone(r["teams"][1]["latest_issued"])

    def test_metrics_that_is_a_list(self):
        root, home = self.demo()
        pm = home.team_sprint_dir("Pricing Models", "demo-pricing-models-pm-23")
        (pm / "metrics.json").write_text("[]")
        r = self.assert_one_problem(root, "metrics.json")
        self.assertEqual(r["teams"][1]["latest_analysed"]["name"], "PM-22")

    def test_non_dict_sprint_entries(self):
        root, home = self.demo()
        pm = home.team_sprint_dir("Pricing Models", "demo-pricing-models-pm-23")
        (pm / "snapshot" / "sprints.json").write_text(
            json.dumps({"Pricing Models": [1, "x", None], "Other": 3}))
        r = self.assert_one_problem(root, "sprints.json")
        self.assertIsNone(r["teams"][1]["latest_collected"])

    def test_validate_failure_that_is_not_a_ledger_error(self):
        root, _ = self.demo()
        with mock.patch("ledger.status.validate",
                        side_effect=ValueError("bad\nvalue")):
            r = self.assert_one_problem(root, "bad value")
        self.assertFalse(r["config_ok"])


if __name__ == "__main__":
    unittest.main()
