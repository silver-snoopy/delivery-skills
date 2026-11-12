"""agent_text_access governs what every command prints (finding C3).

Runs on the invented demo portfolio, whose config says "none"."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ledger import cli
from ledger.home import Home
from ledger.testing.portfolio import AS_OF, CLAIMS, PRICING, build_demo
from ledger.textaccess import NONE_MARKER, TextAccess, redact

CLAIMS_SID = "demo-claims-intake-sprint-37"
PM23 = "demo-pricing-models-pm-23"


def _portfolio_texts():
    b = build_demo()
    out = set()
    for t in b._tickets:
        for text in (t.title, t.parent_title):
            if text and len(text) >= 6:
                out.add(text)
    return sorted(out)


class TextAccessUnitTests(unittest.TestCase):
    def test_levels(self):
        none, titles, full = (TextAccess(x) for x in ("none", "titles",
                                                      "full"))
        self.assertEqual(none.title("Tariff"), NONE_MARKER)
        self.assertEqual(none.body("note"), NONE_MARKER)
        self.assertEqual(titles.title("Tariff"), "Tariff")
        self.assertEqual(titles.body("note"),
                         "[text hidden: agent_text_access is titles]")
        self.assertEqual((full.title("Tariff"), full.body("note")),
                         ("Tariff", "note"))
        self.assertEqual(none.title(""), "")
        self.assertEqual(TextAccess("bogus").title("x"), NONE_MARKER)

    def test_redact_helper(self):
        class Cfg:
            agent_text_access = "titles"
        self.assertEqual(
            redact(Cfg, title="T", description="D"),
            {"title": "T",
             "description": "[text hidden: agent_text_access is titles]"})
        self.assertEqual(redact(Cfg, title="T", description="D",
                                show_text=True),
                         {"title": "T", "description": "D"})


class CommandOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._base = Path(tempfile.mkdtemp())
        cls.base = cls._base / "home"
        with contextlib.redirect_stdout(io.StringIO()):
            assert cli.main(["demo", "--home", str(cls.base)]) == 0
        cls.texts = _portfolio_texts()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls._base), ignore_errors=True)

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name) / "home"
        shutil.copytree(str(self.base), str(self.root))
        self.home = Home(self.root)

    def run_cli(self, *argv, show=False, as_json=True):
        out, err = io.StringIO(), io.StringIO()
        pre = ["--home", str(self.root)] + (["--json"] if as_json else []) \
            + (["--show-text"] if show else [])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(pre + list(argv))
        return code, out.getvalue() + err.getvalue()

    def out(self, *argv, **kw):
        code, text = self.run_cli(*argv, **kw)
        self.assertEqual(code, 0, text)
        return text

    def set_access(self, level):
        cfg = self.home.read_json("config.json")
        cfg["agent_text_access"] = level
        self.home.write_json(cfg, "config.json")

    def leaked(self, text):
        return [t for t in self.texts if t in text]

    def check(self, *argv, expect):
        """No portfolio text at none (text and JSON output); `expect` is
        there with --show-text."""
        for as_json in (True, False):
            text = self.out(*argv, as_json=as_json)
            self.assertEqual(self.leaked(text), [], argv)
            self.assertNotIn(expect, text)
        self.assertIn(NONE_MARKER, text)
        self.assertIn(expect, self.out(*argv, show=True))
        self.assertNotIn(NONE_MARKER, self.out(*argv, show=True))

    def test_annotate_list(self):
        self.check("annotate", "--team", PRICING, "--sprint", PM23,
                   "--list", expect="Compare tariff versions side by side")

    def test_annotate_list_keeps_ids_and_counts(self):
        r = json.loads(self.out("annotate", "--team", PRICING, "--sprint",
                                PM23, "--list"))
        self.assertTrue(r["pending"])
        for item in r["pending"]:
            self.assertEqual(item["title"], NONE_MARKER)
            self.assertIsInstance(item["ticket"], int)
            self.assertIn(str(item["ticket"]), item["key"])

    def test_annotate_notes_hidden_below_full(self):
        note = "Waiting for the address lookup service"
        argv = ("annotate", "--team", CLAIMS, "--sprint", CLAIMS_SID,
                "--set", "carry:40197:" + CLAIMS_SID, "--agreed", "yes",
                "--note", note + " still")
        self.assertNotIn(note, self.out(*argv))
        self.set_access("titles")
        self.assertNotIn(note, self.out(*argv))
        self.set_access("full")
        self.assertIn(note, self.out(*argv))

    def test_explain(self):
        self.check("explain", "--team", PRICING, "--sprint", PM23,
                   "figures.carried.in_progress",
                   expect="Compare tariff versions side by side")

    def test_trace(self):
        self.check("trace", "40655",
                   expect="Version the rate tables by effective date")

    def test_risk_candidates_and_register(self):
        cfg = self.home.read_json("config.json")
        cfg["thresholds"] = {"one_contributor_min_tickets": 1}
        self.home.write_json(cfg, "config.json")
        text = self.out("risks", "--as-of", AS_OF, "--candidates",
                        show=True)
        cands = json.loads(text)["candidates"]
        feature = next(c for c in cands
                       if c["detector"] == "one_vendor_contributor")
        quoted = feature["title"].split("'")[1]
        self.check("risks", "--as-of", AS_OF, "--candidates", expect=quoted)
        hidden = json.loads(self.out("risks", "--as-of", AS_OF,
                                     "--candidates"))["candidates"]
        same = next(c for c in hidden if c["id"] == feature["id"])
        self.assertEqual(same["tickets"], feature["tickets"])
        self.assertIn("%d tickets" % len(feature["tickets"]), same["text"])
        # accepted into the register: still hidden when listed
        self.assertEqual(self.leaked(self.out(
            "risks", "--as-of", AS_OF, "--accept", feature["id"])), [])
        self.check("risks", "--list", expect=quoted)
        self.check("risks", "--as-of", AS_OF, "--candidates", expect=quoted)
        # a text the delivery manager edited may hold anything
        other = next(c for c in cands
                     if c["detector"] == "chronic_carryover")
        text = self.out("risks", "--as-of", AS_OF, "--accept", other["id"],
                        "--text", "Blocked by the tariff comparison work.")
        self.assertNotIn("tariff comparison", text)
        self.assertNotIn("tariff comparison", self.out("risks", "--list"))
        self.assertIn("tariff comparison",
                      self.out("risks", "--list", show=True))

    def plant_name(self):
        draft = self.root / "reports" / AS_OF / "draft" / "executive.md"
        with open(str(draft), "a", encoding="utf-8") as f:
            f.write("\nSecret tariff wording agreed with Anna Kovacs "
                    "yesterday.\n")

    def test_names_check_hides_context(self):
        roster = self.home.read_json("roster.json")
        roster["people"][0]["display_name"] = "Anna Kovacs"
        self.home.write_json(roster, "roster.json")
        self.plant_name()
        for level in ("none", "titles"):
            self.set_access(level)
            text = self.out("names-check", "--as-of", AS_OF)
            self.assertNotIn("Secret tariff wording", text)
            hits = json.loads(text)["hits"]
            self.assertTrue(hits)
            self.assertEqual({h["file"] for h in hits}, {"executive.md"})
            self.assertIn("Anna Kovacs", {h["term"] for h in hits})
            self.assertTrue(all(h["context"].startswith("[text hidden")
                                for h in hits))
            self.assertIn("--show-text", text)
        self.set_access("none")
        self.assertIn("Secret tariff wording",
                      self.out("names-check", "--as-of", AS_OF, show=True))
        # a refused approval does not quote the contexts either
        code, text = self.run_cli("names-check", "--as-of", AS_OF,
                                  "--approve", "Anna Kovacs", "--context",
                                  "Anna Kovacs nowhere")
        self.assertEqual(code, 2)
        self.assertNotIn("Secret tariff wording", text)

    def test_titles_level_shows_titles(self):
        self.set_access("titles")
        text = self.out("annotate", "--team", PRICING, "--sprint", PM23,
                        "--list")
        self.assertIn("Compare tariff versions side by side", text)

    def test_other_commands_print_no_ticket_text(self):
        for argv in (("status",), ("report", "--as-of", AS_OF),
                     ("evidence", "--team", PRICING, "--as-of", AS_OF),
                     ("analyze", "--team", PRICING),
                     ("narrative-input", "--team", PRICING, "--sprint",
                      PM23, "--force-ids")):
            for as_json in (True, False):
                self.assertEqual(self.leaked(self.out(
                    *argv, as_json=as_json)), [], argv)


if __name__ == "__main__":
    unittest.main()
