"""Agent narrative: the input the agent may see, validation of what it
wrote, and its use in the report. Runs against the invented demo
portfolio."""
import contextlib
import copy
import io
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ledger import narrative
from ledger.analyze import _load_snapshot
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.home import Home
from ledger.report import build_report
from ledger.testing.portfolio import AS_OF, CLAIMS, run_demo

SID = "demo-claims-intake-sprint-37"
BACKFILLED = "demo-claims-intake-sprint-36"
CLAIM = "Rule set is live for motor claims."


def _valid_entry():
    return {
        "delivered": [
            {"heading": "Claim triage rules",
             "text": "New claims are now routed to the right queue by "
                     "triage rules.",
             "tickets": [40132, 40134, 40192]},
            {"heading": "First notice of loss intake",
             "text": "The intake form takes photos and looks up the policy.",
             "tickets": [40133, 40135, 40136]},
            {"heading": "Claim summary page",
             "text": "The summary page shows the excess split.",
             "tickets": [40196]},
        ],
        "awaiting": [],
        "not_delivered": [
            {"heading": "Intake follow-up",
             "text": "Address lookup and two form steps continue.",
             "tickets": [40193, 40194, 40197]},
        ],
    }


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, str):
        yield obj


class NarrativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name) / "demo"
        run_demo(Home(cls.base))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name) / "home"
        shutil.copytree(str(self.base), str(self.root))
        self.home = Home(self.root)
        self.roster = load_roster(self.home)

    def tearDown(self):
        self._t.cleanup()

    # ---- helpers -----------------------------------------------------------

    def run_cli(self, *argv, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        old = None
        if stdin is not None:
            import sys
            old, sys.stdin = sys.stdin, io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(err):
                code = main(["--home", str(self.root), "--json"] + list(argv))
        finally:
            if old is not None:
                import sys
                sys.stdin = old
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv, **kw):
        code, out, err = self.run_cli(*argv, **kw)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def fails(self, *argv, **kw):
        code, out, err = self.run_cli(*argv, **kw)
        self.assertEqual(code, 2, out)
        return err

    def set_access(self, level):
        cfg = self.home.read_json("config.json")
        cfg["agent_text_access"] = level
        self.home.write_json(cfg, "config.json")

    def sprint_parts(self, sid=SID):
        return self.home.team_sprint_dir(CLAIMS, sid).relative_to(
            self.root).parts

    def metrics(self, sid=SID):
        return self.home.read_json(*self.sprint_parts(sid), "metrics.json")

    def doc(self, entry=None, confirmed=()):
        doc = {"teams": {CLAIMS: entry or _valid_entry()}}
        if confirmed:
            doc["confirmed"] = list(confirmed)
        return doc

    def write_file(self, obj, name="narr.json"):
        p = Path(self._t.name) / name
        p.write_text(json.dumps(obj), encoding="utf-8")
        return str(p)

    def report(self):
        return build_report(self.home, load_config(self.home), self.roster,
                            date.fromisoformat(AS_OF))

    def claims(self, rep):
        return next(t for g in rep["groups"] for t in g["teams"]
                    if t["name"] == CLAIMS)

    def problems(self, entry, confirmed=()):
        return narrative.validate(self.doc(entry), self.metrics(),
                                  self.roster, list(confirmed))

    # ---- input -------------------------------------------------------------

    def test_access_levels(self):
        m = self.metrics()
        snap, _ = _load_snapshot(self.home, CLAIMS, SID)
        none = narrative.agent_input(m, snap, "none")
        keys = set(_keys(none))
        for k in ("title", "parent_title", "description"):
            self.assertNotIn(k, keys)
        self.assertEqual(none["figures"],
                         {"delivered": 7, "awaiting": 0, "not_delivered": 3})
        self.assertEqual(none["sprint"]["name"], "Sprint 37")
        titles = narrative.agent_input(m, snap, "titles")
        self.assertIn("title", set(_keys(titles)))
        self.assertNotIn("description", set(_keys(titles)))
        first = titles["delivered"][0]
        self.assertTrue(first["title"])
        full = narrative.agent_input(m, snap, "full")
        descs = [t["description"] for b in narrative.BUCKETS
                 for t in full[b]]
        self.assertTrue(descs)
        self.assertTrue(all(len(d) <= 1500 for d in descs))
        # long description truncated with an ellipsis
        tk = next(t for t in snap.tickets if t.id == 40196)
        tk.description = "x" * 4000
        cut = next(t for t in narrative.agent_input(m, snap, "full")
                   ["delivered"] if t["id"] == 40196)["description"]
        self.assertEqual(len(cut), 1500)
        self.assertTrue(cut.endswith("…"))

    def test_input_has_no_person_fields(self):
        m = self.metrics()
        snap, _ = _load_snapshot(self.home, CLAIMS, SID)
        ids = {p.id for p in self.roster.people}
        names = {p.display_name for p in self.roster.people}
        for level in ("none", "titles", "full"):
            inp = narrative.agent_input(m, snap, level)
            keys = set(_keys(inp))
            for k in ("people", "workers", "holder_at_end", "annotations",
                      "holder", "owner"):
                self.assertNotIn(k, keys, level)
            for s in _strings(inp):
                self.assertNotIn(s, ids | names, level)

    def test_command_refuses_at_none_without_force_ids(self):
        err = self.fails("narrative-input", "--team", CLAIMS,
                         "--sprint", SID)
        self.assertIn("ticket text", err)
        self.assertIn("ticket titles", err)
        r = self.ok("narrative-input", "--team", CLAIMS, "--sprint", SID,
                    "--force-ids")
        self.assertNotIn("title", set(_keys(r["input"])))
        self.set_access("titles")
        r = self.ok("narrative-input", "--team", CLAIMS, "--sprint", SID)
        self.assertIn("title", set(_keys(r["input"])))

    def test_backfilled_sprint_refused(self):
        self.set_access("full")
        err = self.fails("narrative-input", "--team", CLAIMS,
                         "--sprint", BACKFILLED)
        self.assertIn("backfilled", err)
        err = self.fails("narrative-validate", "--team", CLAIMS, "--sprint",
                         BACKFILLED, "--file", self.write_file(self.doc()))
        self.assertIn("backfilled", err)

    # ---- validation --------------------------------------------------------

    def test_valid_narrative_passes(self):
        self.assertEqual(self.problems(_valid_entry()), [])

    def test_uncited_and_wrong_bucket_rejected(self):
        entry = _valid_entry()
        entry["delivered"][2]["tickets"] = [40196, 40193]
        probs = self.problems(entry)
        self.assertTrue(any("40193" in p and "delivered" in p
                            and "Claim summary page" in p for p in probs),
                        probs)
        entry = _valid_entry()
        entry["delivered"].pop()
        probs = self.problems(entry)
        self.assertTrue(any("40196" in p for p in probs), probs)
        entry = _valid_entry()
        entry["delivered"][0]["tickets"] = []
        self.assertTrue(any("no tickets" in p for p in self.problems(entry)))
        entry = _valid_entry()
        entry["extra"] = []
        self.assertTrue(any("extra" in p for p in self.problems(entry)))

    def test_roster_name_rejected(self):
        entry = _valid_entry()
        entry["delivered"][0]["text"] = "Dávid finished the triage rules."
        probs = self.problems(entry)
        self.assertTrue(any("Dávid" in p for p in probs), probs)
        entry = _valid_entry()
        entry["delivered"][1]["text"] = ("The intake form was reviewed "
                                         "with Csilla before the demo.")
        self.assertTrue(any("Csilla" in p for p in self.problems(entry)))
        entry = _valid_entry()
        entry["not_delivered"][0]["heading"] = "Work for Hanna L."
        self.assertTrue(self.problems(entry))

    def test_claim_needs_confirmation(self):
        entry = _valid_entry()
        entry["delivered"][0]["text"] = "Triage is done. " + CLAIM
        probs = self.problems(entry)
        self.assertTrue(any("live" in p for p in probs), probs)
        self.assertEqual(self.problems(entry, [CLAIM]), [])
        self.assertTrue(self.problems(entry, ["Rule set is live."]))
        # whole words only: "delivery" is not "live", "olive" neither
        entry["delivered"][0]["text"] = "An olive-free delivery."
        self.assertEqual(self.problems(entry), [])

    def test_html_and_markdown_rejected(self):
        for text in ("<b>Done</b> triage.", "See [the board](http://x)."):
            entry = _valid_entry()
            entry["delivered"][0]["text"] = text
            self.assertTrue(self.problems(entry), text)

    def test_length_limits(self):
        entry = _valid_entry()
        entry["delivered"][0]["heading"] = "h" * 61
        entry["delivered"][1]["text"] = "word " * 60
        probs = self.problems(entry)
        self.assertEqual(len(probs), 2, probs)

    def test_validate_command(self):
        bad = self.doc()
        bad["teams"][CLAIMS]["delivered"][0]["text"] = CLAIM
        err = self.fails("narrative-validate", "--team", CLAIMS, "--sprint",
                         SID, "--file", self.write_file(bad))
        self.assertIn("live", err)
        self.assertIn("--confirm", err)
        self.assertIsNone(self.home.read_json(*self.sprint_parts(),
                                              "narrative.json"))
        r = self.ok("narrative-validate", "--team", CLAIMS, "--sprint", SID,
                    "--file", "-", "--confirm", CLAIM,
                    stdin=json.dumps(bad))
        self.assertTrue(r["ok"])
        stored = self.home.read_json(*self.sprint_parts(), "narrative.json")
        self.assertIn(CLAIM, stored["confirmed"])

    # ---- report ------------------------------------------------------------

    def test_valid_narrative_used_by_report_but_not_for_backfilled(self):
        self.ok("narrative-validate", "--team", CLAIMS, "--sprint", SID,
                "--file", self.write_file(self.doc()))
        rep = self.report()
        self.assertEqual(self.claims(rep)["narrative"], _valid_entry())
        self.assertEqual(rep["narrative_fallbacks"], [])
        r = self.ok("report", "--as-of", AS_OF)
        html = Path(r["files"]["executive_html"]).read_text(encoding="utf-8")
        self.assertIn("The summary page shows the excess split.", html)
        self.assertNotIn("Show excess and deductible split on the claim "
                         "summary", html)
        # a narrative.json placed in a backfilled sprint is never used
        m = self.metrics()
        m["backfilled"] = True
        self.home.write_json(m, *self.sprint_parts(), "metrics.json")
        rep = self.report()
        self.assertEqual(self.claims(rep)["narrative"],
                         narrative.fallback(m))

    def test_rebucketed_narrative_ignored(self):
        self.ok("narrative-validate", "--team", CLAIMS, "--sprint", SID,
                "--file", self.write_file(self.doc()))
        m = self.metrics()
        m["tickets"]["40196"]["outcome"] = "carried_in_progress"
        self.home.write_json(m, *self.sprint_parts(), "metrics.json")
        rep = self.report()
        self.assertEqual(self.claims(rep)["narrative"],
                         narrative.fallback(m))
        self.assertEqual(rep["narrative_fallbacks"], [CLAIMS])
        r = self.ok("report", "--as-of", AS_OF)
        self.assertIn(CLAIMS, r["summary"])
        self.assertEqual(r["narrative_fallbacks"], [CLAIMS])

    # ---- summary -----------------------------------------------------------

    def test_summary_numbers_and_percentages(self):
        rep = self.report()
        ok = ("9 items were delivered in the sprint ending 6 Nov 2026 and "
              "4 were carried over.")
        self.assertEqual(narrative.validate_summary(ok, rep, self.roster, []),
                         [])
        probs = narrative.validate_summary(
            "15 items were delivered.", rep, self.roster, [])
        self.assertTrue(any("15" in p for p in probs), probs)
        self.assertTrue(narrative.validate_summary(
            "Delivery reached 47% of plan.", rep, self.roster, []))
        self.assertTrue(narrative.validate_summary(
            "x" * 601, rep, self.roster, []))
        self.assertTrue(narrative.validate_summary(
            "Anna K. delivered 9 items.", rep, self.roster, []))
        self.assertTrue(narrative.validate_summary(
            "Delivered 9 items because of good weather.", rep,
            self.roster, []))

    def test_valid_summary_replaces_fallback(self):
        text = "9 items were delivered and 4 were carried over."
        self.fails("narrative-validate", "--summary", "--as-of", AS_OF,
                   "--file", self.write_file({"summary": "47% done."}))
        r = self.ok("narrative-validate", "--summary", "--as-of", AS_OF,
                    "--file", self.write_file({"summary": text}))
        self.assertTrue(r["ok"])
        self.assertEqual(self.report()["summary"], text)
        r = self.ok("report", "--as-of", AS_OF)
        html = Path(r["files"]["executive_html"]).read_text(encoding="utf-8")
        self.assertIn(text, html)

    def test_issue_detects_narrative_added_after_report(self):
        self.ok("report", "--as-of", AS_OF)
        self.ok("narrative-validate", "--team", CLAIMS, "--sprint", SID,
                "--file", self.write_file(self.doc()))
        err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("out of date", err)
        self.ok("report", "--as-of", AS_OF)
        self.ok("narrative-validate", "--summary", "--as-of", AS_OF,
                "--file", self.write_file({"summary": "9 items delivered."}))
        err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("out of date", err)

    # ---- fix round 1 -------------------------------------------------------

    def test_agent_cannot_confirm_its_own_claims(self):
        bad = self.doc(confirmed=[CLAIM])
        bad["teams"][CLAIMS]["delivered"][0]["text"] = CLAIM
        err = self.fails("narrative-validate", "--team", CLAIMS, "--sprint",
                         SID, "--file", self.write_file(bad))
        self.assertIn("--confirm by the delivery manager", err)
        del bad["confirmed"]
        self.ok("narrative-validate", "--team", CLAIMS, "--sprint", SID,
                "--file", self.write_file(bad), "--confirm", CLAIM,
                "--confirm", "An unused sentence.")
        stored = self.home.read_json(*self.sprint_parts(), "narrative.json")
        self.assertEqual(stored["confirmed"], [CLAIM])
        self.assertEqual(stored["teams"][CLAIMS]["delivered"][0]["text"],
                         CLAIM)
        self.assertEqual(self.claims(self.report())["narrative"]
                         ["delivered"][0]["text"], CLAIM)
        # summary mode too
        text = "9 items were delivered because of focus."
        self.fails("narrative-validate", "--summary", "--as-of", AS_OF,
                   "--file", self.write_file({"summary": text,
                                              "confirmed": [text]}))
        self.ok("narrative-validate", "--summary", "--as-of", AS_OF,
                "--file", self.write_file({"summary": text}),
                "--confirm", text)
        self.assertEqual(self.home.read_json("reports", AS_OF, "summary.json"),
                         {"summary": text, "confirmed": [text]})

    def summary_problems(self, text):
        return narrative.validate_summary(text, self.report(), self.roster,
                                          [])

    def test_summary_date_exemption_is_tight(self):
        for text in ("5 decisions were taken.", "15 junior staff joined.",
                     "13 Mayors visited.", "2000 tickets were closed.",
                     "Sprint 99 is next.", "Release 6 is next."):
            self.assertTrue(self.summary_problems(text), text)
        for text in ("9 items delivered by 6 Nov 2026.",
                     "9 items delivered by 6 November.",
                     "Sprint 37 delivered 9 items.",
                     "9 items were delivered in 2026."):
            self.assertEqual(self.summary_problems(text), [], text)

    def test_summary_number_words_rejected(self):
        for text in ("Nine items were delivered.", "A dozen items moved.",
                     "Half the items moved.", "Delivery doubled.",
                     "9 per cent more.", "9 percent more.",
                     "The percentage rose.", "Twenty items moved.",
                     "7.5 days of waiting.", "A 15x speed-up."):
            self.assertTrue(self.summary_problems(text), text)
        self.assertEqual(self.summary_problems("9.0 items, 4 carried."), [])

    def test_stale_summary_flagged(self):
        self.home.write_json({"summary": "15 items were delivered.",
                              "confirmed": []},
                             "reports", AS_OF, "summary.json")
        rep = self.report()
        self.assertTrue(rep["summary_fallback"])
        r = self.ok("report", "--as-of", AS_OF)
        self.assertIn("stored summary was not used", r["summary"])
        self.home.write_json({"summary": "9 items were delivered.",
                              "confirmed": []},
                             "reports", AS_OF, "summary.json")
        self.assertFalse(self.report()["summary_fallback"])

    def test_invisible_characters_rejected(self):
        for text in ("Dá​vid finished it.", "It is li­ve now.",
                     "Done\x07 now."):
            entry = _valid_entry()
            entry["delivered"][0]["text"] = text
            probs = self.problems(entry)
            self.assertTrue(any("Claim triage rules" in p for p in probs),
                            (text, probs))
        self.assertTrue(self.summary_problems("9 items​ delivered."))

    def test_unaccented_name_rejected(self):
        entry = _valid_entry()
        entry["delivered"][0]["text"] = "David finished the rules."
        self.assertTrue(any("D" in p and "names" in p
                            for p in self.problems(entry)))

    def test_summary_key_in_team_file_rejected(self):
        doc = self.doc()
        doc["summary"] = "9 items."
        probs = narrative.validate(doc, self.metrics(), self.roster, [])
        self.assertTrue(any("summary" in p for p in probs), probs)

    def test_claim_edge_cases(self):
        for text, conf in (
                ("The rules went live.", "The rules went live."),
                ("Released the rules to staff.",
                 "Released the rules to staff."),
                ("Rules done; they are deployed.",
                 "Rules done; they are deployed."),
                ("Status: accepted by the owner.",
                 "Status: accepted by the owner.")):
            entry = _valid_entry()
            entry["delivered"][0]["text"] = text
            self.assertTrue(self.problems(entry), text)
            self.assertEqual(self.problems(entry, [conf]), [], text)
        entry = _valid_entry()
        entry["delivered"][0]["text"] = "Rules done; they are deployed."
        self.assertTrue(self.problems(entry, ["they are deployed."]))


if __name__ == "__main__":
    unittest.main()
