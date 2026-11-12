import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger import annotations
from ledger.cli import main
from ledger.errors import LedgerError
from ledger.home import Home
from tests.test_metrics import make, rich


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.home = Home(Path(tempfile.mkdtemp()))
        b, anna, bence = make()
        rich(b, anna, bence)
        # a late revision so the cutoff falls after demo-claims-s37 ends
        b.ticket(99, team="Claims").on("2026-11-23 09:00")
        b.write(self.home)
        code, _, err = run_main(["--home", str(self.home.root), "analyze",
                                 "--team", "Claims", "--sprint", "demo-claims-s38"])
        self.assertEqual(code, 0, err)
        d = self.home.team_sprint_dir("Claims", "demo-claims-s37")
        self.metrics_path = d / "metrics.json"
        self.metrics = json.loads(self.metrics_path.read_text())

    def entries(self):
        return annotations.load(self.home, "Claims", "demo-claims-s37")

    def test_pending_lists_carryovers_and_returns_not_awaiting(self):
        items = annotations.pending(self.metrics, [])
        carry = [i["ticket"] for i in items if i["kind"] == "carry"]
        rets = [i["key"] for i in items if i["kind"] == "return"]
        # 7 and 8 are carried with another party at the sprint's end: they
        # are not in the carried-over table, so nobody is asked about them
        self.assertEqual(sorted(carry), [4, 6, 11])
        self.assertFalse(self.metrics["tickets"]["8"]["held_by_vendor_at_end"])
        self.assertNotIn(3, carry)  # awaiting acceptance
        self.assertEqual(self.metrics["tickets"]["3"]["outcome"],
                         "awaiting_acceptance")
        self.assertEqual(len(rets), 3)
        self.assertTrue(all(k.startswith("return:6:") for k in rets))
        kinds = [i["kind"] for i in items]
        self.assertEqual(kinds, sorted(kinds))  # carries first
        counts = [i["sprints_carried"] for i in items if i["kind"] == "carry"]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(set(items[0]), {"key", "ticket", "title", "kind",
                                         "sprints_carried"})
        annotations.record(self.home, "Claims", "demo-claims-s37", "carry:4:demo-claims-s37")
        left = annotations.pending(self.metrics, self.entries())
        self.assertEqual(len(left), len(items) - 1)

    def test_record_versions_and_current(self):
        a = annotations.record(self.home, "Claims", "demo-claims-s37", "carry:8:demo-claims-s37",
                               agreed="no", reason="dependency",
                               note="  first  ", now="2026-11-10T09:00:00+00:00")
        b = annotations.record(self.home, "Claims", "demo-claims-s37", "carry:8:demo-claims-s37",
                               agreed="yes", reason="planned leave")
        self.assertEqual((a["version"], b["version"]), (1, 2))
        self.assertEqual(a["note"], "first")
        self.assertEqual(len(self.entries()), 2)
        cur = annotations.current(self.entries())
        self.assertEqual(cur["carry:8:demo-claims-s37"]["agreed"], "yes")
        self.assertEqual(self.entries()[0]["agreed"], "no")

    def test_invalid_reason_or_key_rejected(self):
        rec = annotations.record
        with self.assertRaises(LedgerError):
            rec(self.home, "Claims", "demo-claims-s37", "carry:999:demo-claims-s37")
        with self.assertRaises(LedgerError):
            rec(self.home, "Claims", "demo-claims-s37", "carry:8:demo-claims-s37", reason="defect")
        with self.assertRaises(LedgerError):
            rec(self.home, "Claims", "demo-claims-s37", "carry:8:demo-claims-s37", agreed="maybe")
        rkey = [i["key"] for i in annotations.pending(self.metrics, [])
                if i["kind"] == "return"][0]
        with self.assertRaises(LedgerError):
            rec(self.home, "Claims", "demo-claims-s37", rkey, agreed="yes")
        with self.assertRaises(LedgerError):
            rec(self.home, "Claims", "demo-claims-s37", rkey, reason="dependency")
        self.assertEqual(self.entries(), [])
        self.assertEqual(rec(self.home, "Claims", "demo-claims-s37", rkey,
                             reason="defect")["kind"], "return")

    def test_annotations_do_not_change_metrics(self):
        before = self.metrics_path.read_bytes()
        annotations.record(self.home, "Claims", "demo-claims-s37", "carry:8:demo-claims-s37",
                           agreed="yes", reason="dependency", note="x")
        self.assertEqual(self.metrics_path.read_bytes(), before)

    def test_interactive_reads_answers(self):
        items = annotations.pending(self.metrics, [])
        first = items[0]
        self.assertEqual(first["kind"], "carry")
        stdin = io.StringIO("y\n1\nnote text\n")
        out = io.StringIO()
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", out):
            code = main(["--home", str(self.home.root), "annotate", "--team",
                         "Claims", "--sprint", "demo-claims-s37", "--interactive"])
        self.assertEqual(code, 0)
        e = annotations.current(self.entries())[first["key"]]
        self.assertEqual((e["agreed"], e["reason"], e["note"]),
                         ("yes", "dependency", "note text"))
        self.assertEqual(len(self.entries()), 1)  # input ended cleanly
        self.assertIn("Agreed with the PO and communicated in advance? [y/n/u]",
                      out.getvalue())
        self.assertIn("1 annotation", out.getvalue())

    def test_interactive_invalid_input_records_unknown(self):
        stdin = io.StringIO("zz\nqq\n99\n7x\nsome note\n")
        with mock.patch("sys.stdin", stdin), mock.patch("sys.stdout", io.StringIO()):
            code = main(["--home", str(self.home.root), "annotate", "--team",
                         "Claims", "--sprint", "demo-claims-s37", "--interactive"])
        self.assertEqual(code, 0)
        e = self.entries()[0]
        self.assertEqual((e["agreed"], e["reason"], e["note"]),
                         ("unknown", None, "some note"))

    def test_list_and_set_commands(self):
        argv = ["--home", str(self.home.root), "annotate", "--team", "Claims",
                "--sprint", "demo-claims-s37"]
        code, out, _ = run_main(argv + ["--list"])
        self.assertEqual(code, 0)
        self.assertIn("carry:4:demo-claims-s37", out)
        self.assertNotIn("carry:8:demo-claims-s37", out)  # another party's
        code, out, err = run_main(argv + ["--set", "carry:4:demo-claims-s37", "--agreed",
                                          "no", "--reason", "dependency"])
        self.assertEqual(code, 0, err)
        code, out, _ = run_main(["--json"] + argv + ["--list"])
        keys = [i["key"] for i in json.loads(out)["pending"]]
        self.assertNotIn("carry:4:demo-claims-s37", keys)

    def test_list_json_includes_allowed_reasons(self):
        argv = ["--json", "--home", str(self.home.root), "annotate", "--team",
                "Claims", "--sprint", "demo-claims-s37", "--list"]
        code, out, _ = run_main(argv)
        self.assertEqual(code, 0)
        reasons = json.loads(out)["reasons"]
        self.assertEqual(reasons, {"carry": list(annotations.REASONS),
                                   "return": list(annotations.RETURN_REASONS)})

    def test_missing_metrics_hints_analyze(self):
        code, _, err = run_main(["--home", str(self.home.root), "annotate",
                                 "--team", "Claims", "--sprint", "demo-claims-s99",
                                 "--list"])
        self.assertEqual(code, 2)
        self.assertIn("analyze", err)

    def test_carried_forward_orders_by_entered(self):
        other = self.home.team_sprint_dir("Claims", "demo-claims-s36")
        rel = other.relative_to(self.home.root).parts
        self.home.write_json([{"key": "carry:11:demo-claims-s36", "kind": "carry",
                               "ticket": 11, "agreed": "no", "reason": None,
                               "note": "", "entered": "2026-10-01T00:00:00+00:00",
                               "version": 1}], *rel, "annotations.json")
        code, out, _ = run_main(["--home", str(self.home.root), "annotate",
                                 "--team", "Claims", "--sprint",
                                 "demo-claims-s37", "--list"])
        self.assertIn("earlier (demo-claims-s36)", out)
        annotations.record(self.home, "Claims", "demo-claims-s37",
                           "carry:11:demo-claims-s37",
                           now="2026-11-01T00:00:00+00:00")
        got = annotations.carried_forward(self.home, "Claims", 11)
        self.assertEqual([e["key"] for e in got],
                         ["carry:11:demo-claims-s36", "carry:11:demo-claims-s37"])
        self.assertEqual(annotations.carried_forward(self.home, "Claims", 4), [])


if __name__ == "__main__":
    unittest.main()
