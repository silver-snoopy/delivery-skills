"""Whole-pipeline check: a real user's sequence of commands, through the CLI
only, from a freshly set-up data home to an issued edition.

Ticket history comes from FakeAdo over the golden demo portfolio, selected
with the LEDGER_TEST_TRANSPORT hook, so the collection step runs exactly as
it would against Azure DevOps."""
import contextlib
import hashlib
from html import escape
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ledger import cli
from ledger.home import Home, slug
from ledger.testing import fake_ado
from ledger.testing.fake_ado import SQUAD_ACTOR, FakeAdo
from ledger.testing.portfolio import AS_OF, build_demo

TRANSPORT = "pipeline-test"
ENV = "LEDGER_TEST_TRANSPORT"
# 2026-11-07 08:00 in Europe/Zurich (the demo's cut-off), as ISO.
COLLECT_AS_OF = "2026-11-07T08:00:00+01:00"
PRICING = "Pricing Models"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.builder = build_demo()
        self.fake = FakeAdo(self.builder)
        fake_ado.REGISTRY[TRANSPORT] = self.fake
        self.addCleanup(fake_ado.REGISTRY.pop, TRANSPORT, None)
        saved = os.environ.get(ENV)
        os.environ[ENV] = TRANSPORT
        self.addCleanup(self._restore_env, saved)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove_tmp)
        self.root = self.tmp / "home"
        home = Home(self.root)
        home.ensure()
        # What a user has after setup: config and a roster, nothing else.
        home.write_json(self.builder._config_json(), "config.json")
        roster = self.builder._roster_json()
        patch = self.fake.roster_patch()
        for p in roster["people"]:
            p.update(patch[p["id"]])
        home.write_json(roster, "roster.json")
        self.roster = roster

    @staticmethod
    def _restore_env(saved):
        if saved is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = saved

    def _remove_tmp(self):
        # Editions are read-only; make everything writable before removal.
        for root, dirs, _ in os.walk(str(self.tmp)):
            os.chmod(root, 0o700)
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)
        shutil.rmtree(str(self.tmp))

    # ---------------------------------------------------------------- helpers
    def run_cli(self, *argv, home=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--home", str(home or self.root), "--json"]
                            + list(argv))
        self.assertEqual(code, 0, "%s failed: %s" % (argv, err.getvalue()))
        return json.loads(out.getvalue())

    def sprint_dirs(self, root=None):
        return sorted((root or self.root).glob("teams/*/sprints/*"))

    def sprint_files(self):
        """Every snapshot and metrics file of every sprint, by path."""
        out = {}
        for d in self.sprint_dirs():
            for p in d.rglob("*"):
                if p.is_file() and (p.name == "metrics.json"
                                    or p.parent.name == "snapshot"):
                    out[p] = p.read_bytes()
        return out

    def latest_metrics(self, root):
        out = {}
        for team_dir in sorted((root / "teams").iterdir()):
            sprints = sorted(team_dir.glob("sprints/*/metrics.json"))
            m = json.loads(sprints[-1].read_text("utf-8"))
            out[team_dir.name] = (sprints[-1].parent.name, m["figures"])
        return out

    def non_roster_identities(self):
        actors = {SQUAD_ACTOR}
        for t in self.builder._tickets:
            for r in t.revisions:
                for a in (r.by, r.assignee):
                    if a and not a.startswith("p:"):
                        actors.add(a)
        names = set()
        for a in actors:
            ident = self.fake.identity(a)
            names |= {ident["displayName"], ident["uniqueName"]}
        return names

    def all_files_text(self):
        return {p: p.read_bytes().decode("utf-8", errors="replace")
                for p in self.root.rglob("*") if p.is_file()}

    # ---------------------------------------------------------------- the test
    def test_whole_pipeline(self):
        r = self.run_cli("status")
        self.assertTrue(r["config_ok"] and r["roster_ok"], r)
        self.assertEqual([t["latest_collected"] for t in r["teams"]],
                         [None, None, None])

        r = self.run_cli("setup-check")
        self.assertTrue(r["ok"], r)

        self.run_cli("probe")
        export = self.root / "probe" / "probe-export.json"
        self.assertTrue(export.is_file())
        text = export.read_text("utf-8") + (
            self.root / "probe" / "probe-export.md").read_text("utf-8")
        for t in self.builder._tickets:
            self.assertNotIn(str(t.id), text)
        for team in self.builder._teams:
            self.assertNotIn(team, text)
        for p in self.roster["people"]:
            self.assertNotIn(p["display_name"], text)

        r = self.run_cli("collect", "--all", "--as-of", COLLECT_AS_OF)
        self.assertEqual(len(r["teams"]), 3)
        r = self.run_cli("analyze", "--all")
        self.assertIn("PM-23", r["summary"])

        # Collecting again at the same instant gives byte-identical files.
        # meta.json has no time-of-run field (cutoff is the --as-of instant,
        # not the wall clock), so it is compared whole as well.
        first = self.sprint_files()
        self.assertTrue(any(p.name == "tickets.jsonl" for p in first))
        r = self.run_cli("collect", "--all", "--as-of", COLLECT_AS_OF,
                         "--refresh")
        self.assertFalse([t for t in r["teams"] if t.get("skipped")])
        self.run_cli("analyze", "--all")
        self.assertEqual(self.sprint_files(), first)

        # Collecting through ADO gives the same figures as the demo's path.
        demo_root = self.tmp / "demo"
        self.run_cli("demo", "--home", str(demo_root))
        collected, direct = (self.latest_metrics(self.root),
                             self.latest_metrics(demo_root))
        self.assertEqual(sorted(collected), sorted(direct))
        for team in direct:
            self.assertEqual(collected[team], direct[team], team)

        pm23 = "demo-pricing-models-pm-23"
        r = self.run_cli("annotate", "--team", PRICING, "--sprint", pm23,
                         "--list")
        keys = {i["key"] for i in r["pending"]}
        carry, ret = "carry:40655:%s" % pm23, "return:40655:13"
        self.assertTrue({carry, ret} <= keys, keys)
        self.run_cli("annotate", "--team", PRICING, "--sprint", pm23,
                     "--set", carry, "--agreed", "no",
                     "--reason", "requirement changed",
                     "--note", "Criteria rewritten after each review.")
        self.run_cli("annotate", "--team", PRICING, "--sprint", pm23,
                     "--set", ret, "--reason", "requirement changed")
        r = self.run_cli("annotate", "--team", PRICING, "--sprint", pm23,
                         "--list")
        self.assertFalse({carry, ret} & {i["key"] for i in r["pending"]})

        r = self.run_cli("risks", "--as-of", AS_OF, "--candidates")
        risk = next(c for c in r["candidates"]
                    if c["detector"] == "chronic_carryover")
        self.run_cli("risks", "--as-of", AS_OF, "--accept", risk["id"])

        r = self.run_cli("report", "--as-of", AS_OF)
        executive = Path(r["files"]["executive_html"])
        html = executive.read_text("utf-8")
        self.assertIn(escape(risk["title"]), html)
        for p in self.roster["people"]:
            self.assertNotIn(p["display_name"], html)

        r = self.run_cli("names-check", "--as-of", AS_OF)
        self.assertEqual(r["hits"], [])

        r = self.run_cli("issue", "--as-of", AS_OF)
        edition = Path(r["path"])
        manifest = json.loads((edition / "manifest.json").read_text("utf-8"))
        listed = {(ts["team"], ts["sprint_id"])
                  for ts in manifest["team_sprints"]}
        latest = {(team, sid) for team, (sid, _) in
                  self._team_names(self.latest_metrics(self.root)).items()}
        self.assertEqual(listed, latest)
        for name, digest in manifest["outputs"].items():
            self.assertEqual(_sha(edition / name), digest, name)
        for rel, digest in manifest["inputs"].items():
            self.assertEqual(_sha(self.root / rel), digest, rel)
        self.assertTrue(any(rel.endswith("snapshot/tickets.jsonl")
                            for rel in manifest["inputs"]))

        r = self.run_cli("trace", "40655")
        self.assertIn("40655", r["summary"])
        holder = self._holder_of_40655()
        r = self.run_cli("evidence", "--person", holder, "--as-of", AS_OF)
        self.assertIn("40655", Path(r["file"]).read_text("utf-8"))
        r = self.run_cli("evidence", "--team", PRICING, "--executive",
                         "--as-of", AS_OF)
        brief = Path(r["file"]).read_text("utf-8")
        self.assertIn(PRICING, brief)
        for p in self.roster["people"]:
            self.assertNotIn(p["display_name"], brief)
        self.assertIn("delivered", collected["pricing-models"][1])
        self.run_cli("explain", "--team", PRICING, "--sprint", pm23,
                     "figures.delivered")

        r = self.run_cli("status")
        pm = next(t for t in r["teams"] if t["name"] == PRICING)
        self.assertEqual(pm["latest_collected"]["name"], "PM-23")
        self.assertEqual(pm["latest_issued"]["edition"], 1)
        self.assertEqual(r["latest_draft"], AS_OF)

        # After issue, collect leaves every issued sprint alone.
        before = self.sprint_files()
        mtimes = {p: p.stat().st_mtime_ns for p in before}
        r = self.run_cli("collect", "--all", "--as-of", COLLECT_AS_OF)
        self.assertEqual([t.get("skipped") for t in r["teams"]],
                         ["issued"] * 3)
        for t in r["teams"]:
            self.assertIn("was issued", t["summary"])
        self.assertEqual(self.sprint_files(), before)
        self.assertEqual({p: p.stat().st_mtime_ns for p in before}, mtimes)

        # No identity outside the roster reached the disk.
        # A backlog role's sign-in names are the DM's own input: they may
        # stand in config.json and nowhere else.
        names = self.non_roster_identities()
        self.assertGreaterEqual(len(names), 6)
        cfg = json.loads((self.root / "config.json").read_text("utf-8"))
        role_aliases = {a for r in cfg.get("backlog_roles", [])
                        for a in r["aliases"]}
        self.assertTrue(role_aliases & set(names))
        for path, content in self.all_files_text().items():
            for n in names:
                if n in role_aliases and Path(path).name == "config.json":
                    continue
                self.assertNotIn(n, content, path)

    def _team_names(self, latest):
        cfg = json.loads((self.root / "config.json").read_text("utf-8"))
        by_slug = {slug(t["name"]): t["name"] for t in cfg["teams"]}
        return {by_slug[s]: v for s, v in latest.items()}

    def _holder_of_40655(self) -> str:
        t = next(t for t in self.builder._tickets if t.id == 40655)
        holder = [r.assignee for r in t.revisions if r.assignee][-1]
        self.assertTrue(holder.startswith("p:"))
        return holder[2:]


if __name__ == "__main__":
    unittest.main()
