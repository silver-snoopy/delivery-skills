import io
import unittest
from unittest import mock

from ledger import cli
from ledger.ado import AdoError


class CliTest(unittest.TestCase):
    def test_cli_error_lines(self):
        out = io.StringIO()
        with mock.patch("ledger.setup.run_init", side_effect=AdoError("no token", "az login")):
            code = cli.main(["init", "--query", "q", "--staff", "s", "--vacations", "v", "--yes"], out=out)
        self.assertEqual(code, 1)
        self.assertIn("error: no token", out.getvalue())
        self.assertIn("next: az login", out.getvalue())

    def test_value_error_has_no_next_line(self):
        out = io.StringIO()
        with mock.patch("ledger.setup.run_init", side_effect=ValueError("bad")):
            code = cli.main(["--dir", "x", "init", "--query", "q", "--staff", "s", "--vacations", "v", "--yes"], out=out)
        self.assertEqual((code, out.getvalue()), (1, "error: bad\n"))

    def test_non_tty_accepts_suggestions_and_says_so(self):
        out = io.StringIO()
        seen = {}

        def fake(args, client, ask, o):
            seen["answer"] = ask("Team?", "ALFA")
            return 0
        with mock.patch("ledger.setup.run_init", side_effect=fake), \
                mock.patch("sys.stdin", io.StringIO("")):
            code = cli.main(["init", "--query", "q", "--staff", "s", "--vacations", "v"], out=out)
        self.assertEqual((code, seen["answer"]), (0, "ALFA"))
        self.assertIn("suggestions were accepted", out.getvalue())


if __name__ == "__main__":
    unittest.main()
