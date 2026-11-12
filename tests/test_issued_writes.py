"""An issued sprint refuses late answers and narratives until it is
reopened (ruling R1): nothing an edition's manifest points to may change
silently."""
import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ledger import cli
from ledger.home import Home
from ledger.testing.portfolio import AS_OF, CLAIMS
try:  # run by discovery (tests/ on the path) or as tests.<module>
    from test_narrative import SID, _valid_entry
except ImportError:
    from tests.test_narrative import SID, _valid_entry


def _rmtree(path: Path) -> None:
    for root, dirs, _ in os.walk(str(path)):
        os.chmod(root, 0o700)
    shutil.rmtree(str(path), ignore_errors=True)


def _tree(directory: Path):
    return {str(p.relative_to(directory)):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


class IssuedSprintWritesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.root = self.tmp / "home"
        self.home = Home(self.root)
        self.ok("demo", "--home", str(self.root))
        self.sprint = self.home.team_sprint_dir(CLAIMS, SID)
        self.ok("report", "--as-of", AS_OF)
        self.ok("names-check", "--as-of", AS_OF)
        self.ok("issue", "--as-of", AS_OF)
        self.narr = self.tmp / "narr.json"
        self.narr.write_text(json.dumps({"teams": {CLAIMS: _valid_entry()}}),
                             encoding="utf-8")

    def cli(self, *argv, stdin=""):
        import sys
        out, err = io.StringIO(), io.StringIO()
        old, sys.stdin = sys.stdin, io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = cli.main(["--home", str(self.root), "--json"]
                                + list(argv))
        finally:
            sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv, **kw):
        code, out, err = self.cli(*argv, **kw)
        self.assertEqual(code, 0, "%s: %s" % (argv, err))
        return json.loads(out)

    def refused(self, *argv, **kw):
        before = _tree(self.root)
        code, out, err = self.cli(*argv, **kw)
        self.assertEqual(code, 2, out)
        self.assertIn("has been issued", err)
        self.assertIn("ledger reopen --team", err)
        self.assertEqual(_tree(self.root), before)

    def test_late_answers_and_narratives_are_refused_until_reopen(self):
        # --list stays allowed on an issued sprint
        self.assertIn("pending", self.ok("annotate", "--team", CLAIMS,
                                         "--sprint", SID, "--list"))
        key = "carry:40197:" + SID  # the ticket the vendor carried over
        self.refused("annotate", "--team", CLAIMS, "--sprint", SID,
                     "--set", key, "--agreed", "yes")
        self.refused("annotate", "--team", CLAIMS, "--sprint", SID,
                     "--interactive", stdin="y\n1\nlate\n")
        self.refused("narrative-validate", "--team", CLAIMS, "--sprint",
                     SID, "--file", str(self.narr))

        self.ok("reopen", "--team", CLAIMS, "--sprint", SID, "--note",
                "Late answer from the product owner.")
        r = self.ok("annotate", "--team", CLAIMS, "--sprint", SID,
                    "--set", key, "--agreed", "yes")
        self.assertEqual(r["entry"]["agreed"], "yes")
        self.assertTrue(self.ok("narrative-validate", "--team", CLAIMS,
                                "--sprint", SID, "--file",
                                str(self.narr))["ok"])
        # the issued copy is kept beside the corrected data
        self.assertTrue((self.sprint / "superseded" / "001"
                         / "metrics.json").is_file())


if __name__ == "__main__":
    unittest.main()
