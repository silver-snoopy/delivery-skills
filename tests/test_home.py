import stat
import tempfile
import unittest
from pathlib import Path

from ledger.errors import LedgerError
from ledger.home import Home, slug


class HomeTest(unittest.TestCase):
    def test_resolve_precedence(self):
        self.assertEqual(Home.resolve("/tmp/a", {"DELIVERY_LEDGER_HOME": "/tmp/b"}).root, Path("/tmp/a"))
        self.assertEqual(Home.resolve(None, {"DELIVERY_LEDGER_HOME": "/tmp/b"}).root, Path("/tmp/b"))
        self.assertEqual(Home.resolve(None, {}).root, Path.home() / "delivery-ledger-data")

    def test_team_sprint_dir_rejects_unsafe_ids(self):
        h = Home(Path("/tmp/h"))
        for bad in ("", ".", "..", "a/b", "a\\b", "../x"):
            with self.assertRaises(LedgerError, msg=bad):
                h.team_sprint_dir("T", bad)
        self.assertEqual(h.team_sprint_dir("T", "s-1").name, "s-1")

    def test_refuses_windows_mount(self):
        with self.assertRaises(LedgerError) as c:
            Home.resolve("/mnt/c/Users/x/data", {})
        self.assertIn("Linux filesystem", c.exception.hint)

    def test_resolve_creates_nothing(self):
        target = Path(tempfile.mkdtemp()) / "fresh"
        Home.resolve(str(target), {})
        self.assertFalse(target.exists())

    def test_write_json_is_private_and_atomic(self):
        h = Home(Path(tempfile.mkdtemp()) / "home")
        h.ensure()
        h.write_json({"a": 1}, "teams", "x", "m.json")
        p = h.path("teams", "x", "m.json")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        self.assertEqual(h.read_json("teams", "x", "m.json"), {"a": 1})
        self.assertEqual(h.read_json("nope.json", default=[]), [])
        self.assertEqual([f.name for f in p.parent.iterdir()], ["m.json"])

    def test_read_json_invalid_json_is_ledger_error(self):
        h = Home(Path(tempfile.mkdtemp()))
        h.path("config.json").write_text("{not json")
        with self.assertRaises(LedgerError) as cm:
            h.read_json("config.json")
        self.assertIn("config.json", cm.exception.message)
        self.assertIn("not valid JSON", cm.exception.hint)

    def test_ensure_makes_private_root(self):
        h = Home(Path(tempfile.mkdtemp()) / "home")
        h.ensure()
        self.assertEqual(stat.S_IMODE(h.root.stat().st_mode), 0o700)

    def test_team_sprint_dir(self):
        h = Home(Path("/x"))
        self.assertEqual(h.team_sprint_dir("Team A", "s37"), Path("/x/teams/team-a/sprints/s37"))

    def test_slug(self):
        self.assertEqual(slug("Proj\\Release 1\\Sprint 37"), "proj-release-1-sprint-37")


if __name__ == "__main__":
    unittest.main()
