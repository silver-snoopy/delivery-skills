"""Correcting an issued sprint with `reopen`, end to end on the demo
portfolio through the CLI."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ledger import cli
from ledger import reopen as reopen_mod
from ledger.home import Home
from ledger.testing.portfolio import AS_OF, PRICING

PM23 = "demo-pricing-models-pm-23"


def _rmtree(path: Path) -> None:
    for root, dirs, _ in os.walk(str(path)):
        os.chmod(root, 0o700)
        for d in dirs:
            os.chmod(os.path.join(root, d), 0o700)
    shutil.rmtree(str(path), ignore_errors=True)


def _hashes(directory: Path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.iterdir()}


class ReopenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.root = self.tmp / "home"
        self.home = Home(self.root)
        self.ok("demo", "--home", str(self.root))
        self.sprint = self.home.team_sprint_dir(PRICING, PM23)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--home", str(self.root), "--json"] + list(argv))
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv):
        code, out, err = self.cli(*argv)
        self.assertEqual(code, 0, "%s: %s" % (argv, err))
        return json.loads(out)

    def fails(self, *argv):
        code, out, err = self.cli(*argv)
        self.assertEqual(code, 2, out)
        self.assertIn("next:", err)
        return err

    def issue(self, *extra):
        self.ok("report", "--as-of", AS_OF)
        self.assertEqual(self.ok("names-check", "--as-of", AS_OF)["hits"], [])
        return self.ok("issue", "--as-of", AS_OF, *extra)

    def remap_review_as_doing(self):
        p = self.root / "config.json"
        cfg = json.loads(p.read_text("utf-8"))
        team = next(t for t in cfg["teams"] if t["name"] == PRICING)
        for rule in team["phases"]:
            if rule["state"] == "In Review":
                rule["phase"] = "doing"
                rule.pop("wait_label", None)
        p.write_text(json.dumps(cfg), encoding="utf-8")

    def test_reopen_correct_and_reissue(self):
        first = Path(self.issue()["path"])
        edition_hashes = _hashes(first)
        self.remap_review_as_doing()
        r = self.ok("analyze", "--team", PRICING)
        self.assertIn("issued, kept", r["summary"])
        self.assertIn("ledger reopen", r["summary"])
        self.assertEqual(r["moved"], [])

        self.fails("reopen", "--team", PRICING, "--sprint", PM23)
        self.fails("reopen", "--team", PRICING, "--sprint", PM23,
                   "--note", "  ")
        note = "Review state was mapped as a wait <by mistake>."
        r = self.ok("reopen", "--team", PRICING, "--sprint", PM23,
                    "--note", note)
        self.assertEqual(r["edition"], 1)
        sup = self.sprint / "superseded" / "001"
        for name in ("metrics.json", "ledger.json", "annotations.json",
                     "snapshot/tickets.jsonl", "snapshot/meta.json"):
            p = sup / name
            self.assertTrue(p.is_file(), name)
            self.assertFalse(p.stat().st_mode & stat.S_IWUSR, name)
        self.assertFalse(sup.stat().st_mode & stat.S_IWUSR)
        self.assertFalse((self.sprint / "issued.json").exists())
        entries = json.loads((self.sprint / "reopened.json").read_text())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["edition"], 1)
        self.assertEqual(entries[0]["as_of"], AS_OF)
        self.assertEqual(entries[0]["note"], note)
        self.assertTrue(entries[0]["reopened_at"].endswith("Z"))
        self.assertEqual(_hashes(first), edition_hashes)

        st = self.ok("status")
        pm = next(t for t in st["teams"] if t["name"] == PRICING)
        self.assertEqual(pm["reopened"]["edition"], 1)
        self.assertIsNone(pm["latest_issued"])
        self.assertIn("reopened", st["summary"])

        r = self.ok("analyze", "--team", PRICING)
        self.assertIn("recomputed", r["summary"])
        keys = {m["key"] for m in r["moved"]}
        self.assertIn("figures.time_split.waiting:review", keys)
        for m in r["moved"]:
            self.assertNotEqual(m["old"], m["new"])
        metrics = json.loads((self.sprint / "metrics.json").read_text())
        self.assertEqual(metrics["reopened"]["edition"], 1)
        self.assertEqual(metrics["reopened"]["note"], note)

        r = self.ok("report", "--as-of", AS_OF)
        self.assertIn(PRICING, r["summary"])
        self.assertIn("reopened", r["summary"])
        self.assertIn("%d figure" % len(metrics["reopened"]["moved"]), r["summary"])
        for key in ("executive_html", "delivery_manager_html"):
            text = Path(r["files"][key]).read_text("utf-8")
            self.assertIn("corrected since edition 001", text)
            self.assertIn("&lt;by mistake&gt;", text)
            self.assertNotIn("<by mistake>", text)
        md = Path(r["files"]["executive_md"]).read_text("utf-8")
        self.assertIn("corrected since edition 001", md)
        self.assertIn("\\<by mistake\\>", md)
        self.assertEqual(self.ok("names-check", "--as-of", AS_OF)["hits"], [])

        err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("--note", err)
        r = self.ok("issue", "--as-of", AS_OF, "--note", "Corrected PM-23.")
        self.assertEqual(r["edition"], 2)
        manifest = json.loads((Path(r["path"]) / "manifest.json").read_text())
        self.assertEqual(manifest["supersedes"], [
            {"team": PRICING, "sprint_id": PM23, "edition": 1,
             "as_of": AS_OF}])
        self.assertEqual(_hashes(first), edition_hashes)
        st = self.ok("status")
        pm = next(t for t in st["teams"] if t["name"] == PRICING)
        self.assertIsNone(pm["reopened"])
        self.assertEqual(pm["latest_issued"]["edition"], 2)

        # A second correction keeps both superseded copies.
        self.ok("reopen", "--team", PRICING, "--sprint", PM23,
                "--note", "Second correction.")
        self.assertTrue((self.sprint / "superseded" / "001").is_dir())
        self.assertTrue((self.sprint / "superseded" / "002").is_dir())
        entries = json.loads((self.sprint / "reopened.json").read_text())
        self.assertEqual([e["edition"] for e in entries], [1, 2])

    def test_reopen_never_issued_refused(self):
        err = self.fails("reopen", "--team", PRICING, "--sprint", PM23,
                         "--note", "x")
        self.assertIn("not been issued", err)

    def test_collect_skip_and_analyze_kept_name_reopen(self):
        self.issue()
        r = self.ok("analyze", "--all")
        self.assertIn("ledger reopen", r["summary"])

    def reopen_pm23(self, note="Fix the review mapping."):
        return self.ok("reopen", "--team", PRICING, "--sprint", PM23,
                       "--note", note)

    def test_report_after_reopen_needs_analyze(self):
        self.issue()
        self.reopen_pm23()
        err = self.fails("report", "--as-of", AS_OF)
        self.assertIn("analyze --team %s" % PRICING, err)

    def test_issue_after_reopen_needs_analyze(self):
        self.issue()
        self.ok("report", "--as-of", AS_OF)  # a draft from before the reopen
        self.reopen_pm23()
        err = self.fails("issue", "--as-of", AS_OF, "--note", "x")
        self.assertIn("analyze --team %s" % PRICING, err)

    def test_deleting_reopened_json_keeps_the_correction(self):
        self.issue()
        self.remap_review_as_doing()
        self.reopen_pm23()
        self.ok("analyze", "--team", PRICING)
        (self.sprint / "reopened.json").unlink()
        st = self.ok("status")
        pm = next(t for t in st["teams"] if t["name"] == PRICING)
        self.assertEqual(pm["reopened"]["edition"], 1)
        self.ok("report", "--as-of", AS_OF)
        err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("--note", err)
        r = self.ok("issue", "--as-of", AS_OF, "--note", "Corrected.")
        manifest = json.loads((Path(r["path"]) / "manifest.json").read_text())
        self.assertEqual(manifest["supersedes"][0]["edition"], 1)
        self.assertEqual(manifest["supersedes"][0]["as_of"], AS_OF)

    def test_failed_marker_on_reissue_is_repaired_not_duplicated(self):
        self.issue()
        self.remap_review_as_doing()
        self.reopen_pm23()
        self.ok("analyze", "--team", PRICING)
        self.ok("report", "--as-of", AS_OF)
        real = Home.write_json
        calls = []

        def flaky(home, obj, *parts):
            if parts and parts[-1] == "issued.json":
                calls.append(parts)
                if len(calls) == 2:
                    raise OSError("disk full")
            return real(home, obj, *parts)

        with mock.patch.object(Home, "write_json", flaky):
            err = self.fails("issue", "--as-of", AS_OF, "--note", "Fix.")
        self.assertIn("marking its sprints as issued failed", err)
        self.assertFalse((self.sprint / "issued.json").exists())
        r = self.ok("issue", "--as-of", AS_OF, "--note", "Fix.")
        self.assertEqual(r["edition"], 2)
        editions = self.root / "reports" / AS_OF / "editions"
        self.assertEqual(sorted(p.name for p in editions.iterdir()),
                         ["001", "002"])
        marker = json.loads((self.sprint / "issued.json").read_text())
        self.assertEqual(marker["edition"], 2)

    def test_reopen_retry_reuses_identical_copy(self):
        self.issue()
        real = Home.write_json

        def failing(home, obj, *parts):
            if parts and parts[-1] == "reopened.json":
                raise OSError("disk full")
            return real(home, obj, *parts)

        with mock.patch.object(Home, "write_json", failing):
            err = self.fails("reopen", "--team", PRICING, "--sprint", PM23,
                             "--note", "x")
        self.assertIn("reopen", err)
        self.assertTrue((self.sprint / "issued.json").exists())
        self.assertTrue((self.sprint / "superseded" / "001").is_dir())
        self.reopen_pm23()
        self.assertEqual(sorted(p.name for p in
                                (self.sprint / "superseded").iterdir()),
                         ["001"])
        entries = json.loads((self.sprint / "reopened.json").read_text())
        self.assertEqual(len(entries), 1)

    def test_corrupt_copy_is_removed_and_refused(self):
        self.issue()
        real = shutil.copyfile

        def corrupting(src, dst, *a, **kw):
            out = real(src, dst, *a, **kw)
            with open(dst, "ab") as f:
                f.write(b" ")
            return out

        with mock.patch.object(reopen_mod.shutil, "copyfile", corrupting):
            err = self.fails("reopen", "--team", PRICING, "--sprint", PM23,
                             "--note", "x")
        self.assertIn("did not match", err)
        self.assertTrue((self.sprint / "issued.json").exists())
        sup = self.sprint / "superseded"
        self.assertEqual(list(sup.iterdir()) if sup.exists() else [], [])
        self.assertFalse((self.sprint / "reopened.json").exists())

    def test_missing_superseded_metrics_moves_nothing(self):
        self.issue()
        self.remap_review_as_doing()
        self.reopen_pm23()
        sup = self.sprint / "superseded" / "001"
        os.chmod(str(sup), 0o700)
        os.chmod(str(sup / "metrics.json"), 0o600)
        (sup / "metrics.json").unlink()
        r = self.ok("analyze", "--team", PRICING)
        self.assertEqual(r["moved"], [])
        self.assertTrue(any("not available for comparison" in w
                            for w in r["warnings"]), r["warnings"])

    def test_corrected_marker_stays_with_the_sprint(self):
        self.issue()
        self.remap_review_as_doing()
        self.reopen_pm23()
        self.ok("analyze", "--team", PRICING)
        self.ok("report", "--as-of", AS_OF)
        self.ok("issue", "--as-of", AS_OF, "--note", "Corrected.")
        later = "2026-11-10"
        r = self.ok("report", "--as-of", later)
        md = Path(r["files"]["executive_md"]).read_text("utf-8")
        self.assertIn("corrected since edition 001", md)
        # A newer Pricing sprint replaces PM-23 in later reports.
        m = json.loads((self.sprint / "metrics.json").read_text())
        m.pop("reopened")
        m["sprint"] = dict(m["sprint"], id="demo-pricing-models-pm-24",
                           name="PM-24", start="2026-11-09",
                           finish="2026-11-20")
        self.home.write_json(m, *self.home.team_sprint_dir(
            PRICING, "demo-pricing-models-pm-24").relative_to(
            self.root).parts, "metrics.json")
        r = self.ok("report", "--as-of", "2026-11-21")
        md = Path(r["files"]["executive_md"]).read_text("utf-8")
        self.assertNotIn("corrected since edition", md)


class ReopenScopeTest(ReopenTest):
    """Ruling R4: only the team's latest collected sprint can be reopened."""

    PM22 = "demo-pricing-models-pm-22"
    OLD = "2026-10-24"  # a report date on which PM-22 is the latest sprint

    def issue_older(self):
        self.ok("report", "--as-of", self.OLD)
        self.assertEqual(self.ok("names-check", "--as-of",
                                 self.OLD)["hits"], [])
        self.ok("issue", "--as-of", self.OLD)
        older = self.home.team_sprint_dir(PRICING, self.PM22)
        self.assertTrue((older / "issued.json").is_file())
        return older

    def test_older_sprint_is_refused_and_nothing_changes(self):
        older = self.issue_older()
        before = _hashes(older)
        err = self.fails("reopen", "--team", PRICING, "--sprint", self.PM22,
                         "--note", "Wrong mapping.")
        self.assertIn("PM-23", err)
        self.assertIn("not supported in this version", err)
        self.assertEqual(_hashes(older), before)
        self.assertFalse((older / "superseded").exists())
        self.assertFalse((older / "reopened.json").exists())

    def test_sprint_without_its_own_snapshot_is_refused(self):
        older = self.issue_older()
        _rmtree(self.sprint)  # no newer sprint is left
        err = self.fails("reopen", "--team", PRICING, "--sprint", self.PM22,
                         "--note", "Wrong mapping.")
        self.assertIn("no collected snapshot of its own", err)
        self.assertTrue((older / "issued.json").is_file())

    def test_reopened_latest_sprint_stays_live_and_keeps_its_narrative(self):
        sid = "demo-claims-intake-sprint-37"
        claims = "Claims Intake"
        narr = self.tmp / "narr.json"
        narr.write_text(json.dumps({"teams": {claims: {
            "delivered": [
                {"heading": "Claim triage rules",
                 "text": "New claims are now routed to the right queue by "
                         "triage rules.",
                 "tickets": [40132, 40134, 40192]},
                {"heading": "First notice of loss intake",
                 "text": "The intake form takes photos and looks up the "
                         "policy.", "tickets": [40133, 40135, 40136]},
                {"heading": "Claim summary page",
                 "text": "The summary page shows the excess split.",
                 "tickets": [40196]}],
            "awaiting": [],
            "not_delivered": [
                {"heading": "Intake follow-up",
                 "text": "Address lookup and two form steps continue.",
                 "tickets": [40193, 40194, 40197]}]}}}), encoding="utf-8")
        self.ok("narrative-validate", "--team", claims, "--sprint", sid,
                "--file", str(narr))
        self.issue()
        self.ok("reopen", "--team", claims, "--sprint", sid, "--note",
                "Late answer recorded.")
        r = self.ok("analyze", "--team", claims)
        row = next(x for x in r["sprints"] if x["id"] == sid)
        self.assertEqual((row["recomputed"], row["backfilled"]),
                         (True, False))
        m = json.loads((self.home.team_sprint_dir(claims, sid)
                        / "metrics.json").read_text(encoding="utf-8"))
        self.assertFalse(m["backfilled"])
        r = self.ok("report", "--as-of", AS_OF)
        self.assertEqual(r["narrative_fallbacks"], [])
        md = Path(r["files"]["executive_md"]).read_text(encoding="utf-8")
        self.assertIn("The intake form takes photos and looks up the "
                      "policy.", md)


def _inherit_only(cls, keep):
    for name in dir(cls):
        if name.startswith("test_") and name not in keep:
            setattr(cls, name, None)


_inherit_only(ReopenScopeTest, {
    "test_older_sprint_is_refused_and_nothing_changes",
    "test_sprint_without_its_own_snapshot_is_refused",
    "test_reopened_latest_sprint_stays_live_and_keeps_its_narrative"})


if __name__ == "__main__":
    unittest.main()
