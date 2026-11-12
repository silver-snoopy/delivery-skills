import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from ledger.cli import main

REPO = Path(__file__).resolve().parent.parent


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_status_on_empty_home(self):
        code, out, _ = run_main(["--home", tempfile.mkdtemp(), "status"])
        self.assertEqual(code, 0)
        self.assertIn("config.json: missing", out)
        self.assertIn("roster.json: missing", out)

    def test_status_json(self):
        import json
        code, out, _ = run_main(["--home", tempfile.mkdtemp(), "--json", "status"])
        self.assertEqual(code, 0)
        self.assertIn("summary", json.loads(out))

    def test_ledger_error_exit_code(self):
        code, _, err = run_main(["--home", "/mnt/c/x", "status"])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        self.assertIn("next:", err)

    def test_launcher_runs_from_other_cwd(self):
        r = subprocess.run([str(REPO / "bin" / "ledger"), "--home", tempfile.mkdtemp(), "status"],
                           cwd="/tmp", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("config.json", r.stdout)


if __name__ == "__main__":
    unittest.main()
