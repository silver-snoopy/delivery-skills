import contextlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
import unicodedata
from unittest import mock
from datetime import date
from pathlib import Path

from ledger import annotations
from ledger.cli import main
from ledger.config import Person, Roster, load_config, load_roster
from ledger.home import Home
from ledger.issue import input_hashes, sha256_file
from ledger.names_check import names_check
from ledger.report import build_report
from ledger.testing.builder import PortfolioBuilder

AS_OF = "2026-11-07"
SID = "demo-claims-s37"
DSID = "demo-data-ds37"
SPRINTS = [("S36", "2026-10-12", "2026-10-23"), ("S37", "2026-10-26", "2026-11-06"),
           ("S38", "2026-11-09", "2026-11-20")]


def portfolio():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
    b.team("Data", area="Demo\\Data",
           sprints=[("D" + n, s, f) for n, s, f in SPRINTS])
    csilla = b.person("eng_csilla", "Csilla V.", team="Data", start="2026-01-05")
    b.ticket(31, team="Data", title="Story data").on(
        "2026-10-27 08:00", iteration="DS37").on(
        "2026-10-27 09:00", state="Active", assignee=csilla).on(
        "2026-10-27 15:00", state="Closed")
    b.ticket(91, team="Data").on("2026-11-23 09:00")
    anna = b.person("eng_anna", "Anna K.", team="Claims", start="2026-01-05")
    bence = b.person("eng_bence", "Bence T.", team="Claims", start="2026-01-05")
    b.ticket(1, team="Claims", title="Story one").on(
        "2026-10-27 08:00", iteration="S37").on(
        "2026-10-27 09:00", state="Active", assignee=anna).on(
        "2026-10-27 15:00", state="Closed")
    for tid in (21, 22):
        b.ticket(tid, team="Claims", title="Story %d" % tid).on(
            "2026-10-26 08:00", iteration="S37").on(
            "2026-10-27 09:00", state="Active", assignee=bence)
    b.ticket(90, team="Claims").on("2026-11-23 09:00")
    return b


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def person(name, aliases=()):
    return Person(name.lower(), name, (), tuple(aliases), (), ())


class NamesCheckUnitTests(unittest.TestCase):
    def test_short_name_parts_ignored(self):
        roster = Roster([person("Anna K.")])
        hits = names_check({"a.md": "Anna went. K was here. anna k. too"},
                           roster, [])
        terms = [h["term"] for h in hits]
        self.assertIn("Anna", terms)
        self.assertIn("Anna K.", terms)
        self.assertNotIn("K", terms)
        self.assertNotIn("K.", terms)

    def test_word_boundaries(self):
        roster = Roster([person("Emil R.")])
        self.assertEqual(names_check({"a": "Emilia and Semil"}, roster, []), [])
        self.assertEqual(len(names_check({"a": "<p>emil</p>"}, roster, [])), 1)

    def test_context_and_approval(self):
        roster = Roster([person("Dora M.")])
        text = "x" * 100 + " Dora   fixed it " + "y" * 100
        [hit] = names_check({"f": text}, roster, [])
        self.assertEqual(hit["file"], "f")
        self.assertEqual(hit["term"], "Dora")
        self.assertEqual(hit["context"], "x" * 39 + " Dora fixed it " + "y" * 28)
        self.assertEqual(names_check({"f": text}, roster,
                                     [{"term": "dora", "context": "Dora fixed"}]), [])
        self.assertEqual(names_check({"f": text}, roster,
                                     [{"term": "dora", "context": ""}]), [])
        self.assertEqual(len(names_check({"f": text}, roster,
                                         [{"term": "dora", "context": "nope"}])), 1)


    def test_hyphenated_and_apostrophe_name_parts(self):
        roster = Roster([person("Anne-Marie K."), person("Sean O'Brien")])
        terms = {h["term"] for h in names_check(
            {"a": "ask Marie today; Brien said no"}, roster, [])}
        self.assertEqual(terms, {"Marie", "Brien"})
        self.assertTrue(names_check({"a": "Sean O'Brien"}, roster, []))

    def test_decomposed_accent_is_a_hit(self):
        roster = Roster([person("D\u00e1vid N.")])
        text = unicodedata.normalize("NFD", "ask D\u00e1vid now")
        self.assertEqual([h["term"] for h in names_check({"a.md": text}, roster, [])],
                         ["D\u00e1vid"])

    def test_unaccented_name_is_a_hit(self):
        roster = Roster([person("D\u00e1vid R.")])
        hits = names_check({"a.md": "Ticket: David fixes export"}, roster, [])
        self.assertEqual([h["term"] for h in hits], ["D\u00e1vid"])
        roster = Roster([person("David R.")])
        hits = names_check({"a.md": "Ticket: D\u00e1vid fixes export"},
                           roster, [])
        self.assertEqual([h["term"] for h in hits], ["David"])

    def test_escaped_forms_are_hits(self):
        roster = Roster([person("Bence T.", ["bence_t@x.example"])])
        hits = names_check({"e.md": "mail bence\\_t@x.example",
                            "e.html": "<td>Fix Bence&#x27;s export</td>"},
                           roster, [])
        self.assertIn(("e.md", "bence_t@x.example"),
                      {(h["file"], h["term"]) for h in hits})
        self.assertIn(("e.html", "Bence"), {(h["file"], h["term"]) for h in hits})

    def test_approval_context_must_contain_term(self):
        roster = Roster([person("Dora M.")])
        self.assertEqual(len(names_check({"f": "the Dora thing"}, roster,
                                         [{"term": "Dora", "context": "the"}])), 1)


class IssueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        portfolio().write(Home(Path(cls.tmp)))
        code, _, err = run_main(["--home", cls.tmp, "analyze", "--all"])
        assert code == 0, err

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(self.tmp, self.dir, dirs_exist_ok=True)
        self.home = Home(Path(self.dir))

    def tearDown(self):
        for root, dirs, files in os.walk(self.dir):
            os.chmod(root, 0o700)
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)
        shutil.rmtree(self.dir, ignore_errors=True)

    def cli(self, *argv):
        return run_main(["--home", self.dir, "--json"] + list(argv))

    def ok(self, *argv):
        code, out, err = self.cli(*argv)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def fails(self, *argv):
        code, out, err = self.cli(*argv)
        self.assertEqual(code, 2, out)
        return err

    def dm(self, *argv):
        """As the delivery manager in their own terminal: with --show-text
        (the config says agent_text_access "none")."""
        return self.cli("--show-text", *argv)

    def note(self, key, text):
        annotations.record(self.home, "Claims", SID, key, agreed="yes", note=text)

    def report(self):
        return self.ok("report", "--as-of", AS_OF)

    def retitle(self, tid, title):
        # rewrite the ticket title in the analysed metrics (as a later collect would)
        p = self.home.team_sprint_dir("Claims", SID) / "metrics.json"
        m = json.loads(p.read_text())
        m["tickets"][str(tid)]["title"] = title
        p.write_text(json.dumps(m))

    def edition(self, n=1):
        return self.home.path("reports", AS_OF, "editions", "%03d" % n)

    def test_refuses_sprint_with_unfinished_collection(self):
        self.report()
        snap = self.home.team_sprint_dir("Claims", SID) / "snapshot"
        self.home.write_json({"cutoff": "x"},
                             *snap.relative_to(self.home.root).parts,
                             "collect-run.json")
        err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("unfinished collection", err)
        self.assertIn("next:", err)
        self.assertFalse(self.edition().exists())

    # ---- names-check ------------------------------------------------------

    def test_clean_draft_has_no_hits(self):
        self.report()
        r = self.ok("names-check", "--as-of", AS_OF)
        self.assertTrue(r["ok"])
        self.assertEqual(r["hits"], [])

    def test_name_in_ticket_title_is_a_hit(self):
        self.retitle(21, "Fix Bence's export")
        self.report()
        r = self.ok("names-check")  # defaults to latest draft date
        self.assertFalse(r["ok"])
        self.assertIn("Bence", {h["term"] for h in r["hits"]})
        self.assertEqual({h["file"] for h in r["hits"]},
                         {"executive.html", "executive.md"})

    def test_name_in_dm_note_is_a_hit(self):
        self.note("carry:21:" + SID, "Anna is waiting on the export service")
        self.report()
        r = self.ok("names-check", "--as-of", AS_OF)
        self.assertIn("Anna", {h["term"] for h in r["hits"]})

    def test_alias_email_in_note_is_a_hit(self):
        p = self.home.path("roster.json")
        roster = json.loads(p.read_text())
        roster["people"][1]["aliases"] = ["bence.t@vendor.example"]
        p.write_text(json.dumps(roster))
        self.note("carry:21:" + SID, "ask bence.t@vendor.example for status")
        self.report()
        r = self.ok("names-check", "--as-of", AS_OF)
        self.assertIn("bence.t@vendor.example", {h["term"] for h in r["hits"]})

    def test_approved_occurrence_passes(self):
        self.note("carry:21:" + SID, "Anna Karenina release is blocked")
        self.report()
        r = self.ok("names-check", "--as-of", AS_OF, "--approve", "Anna",
                    "--context", "Anna Karenina release")
        self.assertEqual(r["hits"], [])
        self.assertEqual(
            self.home.read_json("reports", AS_OF, "names-approved.json"),
            [{"term": "Anna", "context": "Anna Karenina release"}])
        self.assertTrue(self.ok("names-check", "--as-of", AS_OF)["ok"])

    def test_approval_by_context_covers_only_that_occurrence(self):
        self.note("carry:21:" + SID, "Anna Karenina release is blocked")
        self.note("carry:22:" + SID, "waiting for Anna to reply")
        self.report()
        self.ok("names-check", "--as-of", AS_OF, "--approve", "Anna",
                "--context", "Anna Karenina release")
        hidden = self.ok("names-check", "--as-of", AS_OF)["hits"]
        self.assertTrue(all(h["context"] == "[text hidden: "
                            "agent_text_access is none]" for h in hidden))
        hits = json.loads(self.dm("names-check", "--as-of", AS_OF)[1])["hits"]
        self.assertTrue(hits)
        self.assertTrue(all("Anna to reply" in h["context"] for h in hits))

    def test_approve_context_without_term_refused(self):
        self.note("carry:21:" + SID, "Anna Karenina release is blocked")
        self.report()
        self.fails("names-check", "--as-of", AS_OF, "--approve", "Anna",
                   "--context", "release")
        self.assertIsNone(self.home.read_json("reports", AS_OF, "names-approved.json"))

    def test_approve_context_matching_no_hit_refused(self):
        self.note("carry:21:" + SID, "Anna Karenina release is blocked")
        self.report()
        err = self.fails("names-check", "--as-of", AS_OF, "--approve", "Anna",
                         "--context", "Anna Bolena")
        self.assertNotIn("Anna Karenina", err)
        code, _, err = self.dm("names-check", "--as-of", AS_OF, "--approve",
                               "Anna", "--context", "Anna Bolena")
        self.assertEqual(code, 2)
        self.assertIn("Anna Karenina", err)

    def test_approval_leaves_other_sentence_uncovered(self):
        self.note("carry:21:" + SID, "Anna Karenina release is blocked. "
                  + "z" * 60 + " Then Anna left early")
        self.report()
        self.ok("names-check", "--as-of", AS_OF, "--approve", "Anna",
                "--context", "Anna Karenina")
        hits = json.loads(self.dm("names-check", "--as-of", AS_OF)[1])["hits"]
        self.assertTrue(any("Anna left" in h["context"] for h in hits))
        self.assertFalse(any("Karenina" in h["context"] for h in hits))

    def test_flag_combinations_refused(self):
        self.report()
        self.fails("names-check", "--context", "x")
        self.fails("names-check", "--all-occurrences")
        self.fails("names-check", "--approve", "Anna", "--context", "Anna",
                   "--all-occurrences")

    def test_all_occurrences(self):
        self.note("carry:21:" + SID, "Anna Karenina release is blocked")
        self.note("carry:22:" + SID, "waiting for Anna to reply")
        self.report()
        self.fails("names-check", "--as-of", AS_OF, "--approve", "Anna")
        r = self.ok("names-check", "--as-of", AS_OF, "--approve", "Anna",
                    "--all-occurrences")
        self.assertTrue(r["ok"])

    def test_names_check_without_draft(self):
        err = self.fails("names-check")
        self.assertIn("report", err)

    # ---- issue ------------------------------------------------------------

    def test_issue_without_draft_refused(self):
        self.assertIn("report", self.fails("issue", "--as-of", AS_OF))

    def test_issue_blocked_by_hit(self):
        self.retitle(21, "Fix Bence's export")
        self.report()
        err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("names-check", err)
        self.assertFalse(self.edition().exists())

    def test_issue_creates_immutable_edition_with_manifest(self):
        self.report()
        r = self.ok("issue", "--as-of", AS_OF)
        self.assertEqual((r["edition"], r["team_sprints"]), (1, 2))
        ed = self.edition()
        self.assertEqual(r["path"], str(ed))
        self.assertEqual(stat.S_IMODE(os.stat(str(ed)).st_mode), 0o500)
        names = sorted(os.listdir(str(ed)))
        self.assertEqual(names, ["delivery-manager.html", "executive.html",
                                 "executive.md", "inputs.json", "manifest.json"])
        for n in names:
            self.assertEqual(stat.S_IMODE(os.stat(str(ed / n)).st_mode), 0o400)
        m = json.loads((ed / "manifest.json").read_text())
        self.assertEqual(set(m), {"edition", "as_of", "issued_at", "note",
                                  "tool_version", "rules_version", "team_sprints",
                                  "outputs", "inputs", "names_approved"})
        self.assertEqual(m["edition"], 1)
        self.assertEqual(m["as_of"], AS_OF)
        self.assertRegex(m["issued_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(m["team_sprints"], [{"team": "Claims", "sprint_id": SID},
                                             {"team": "Data", "sprint_id": DSID}])
        self.assertEqual(set(m["outputs"]), {"delivery-manager.html",
                                             "executive.html", "executive.md"})
        for n, h in m["outputs"].items():
            self.assertEqual(sha256_file(ed / n), h)
        for rel, h in m["inputs"].items():
            self.assertEqual(sha256_file(self.home.path(rel)), h)
        sdir = "teams/claims/sprints/%s/" % SID
        for key in ("config.json", "roster.json", sdir + "metrics.json",
                    sdir + "ledger.json"):
            self.assertIn(key, m["inputs"])
        issued = self.home.read_json("teams", "claims", "sprints", SID, "issued.json")
        self.assertEqual(issued, {"edition": 1, "as_of": AS_OF,
                                  "issued_at": m["issued_at"]})

    def test_second_issue_needs_note_and_keeps_first(self):
        self.report()
        self.ok("issue", "--as-of", AS_OF)
        first = (self.edition() / "manifest.json").read_text()
        self.report()
        self.assertIn("--note", self.fails("issue", "--as-of", AS_OF))
        r = self.ok("issue", "--as-of", AS_OF, "--note", "corrected carry-over")
        self.assertEqual(r["edition"], 2)
        self.assertEqual((self.edition() / "manifest.json").read_text(), first)
        m2 = json.loads((self.edition(2) / "manifest.json").read_text())
        self.assertEqual(m2["note"], "corrected carry-over")
        self.assertGreater(m2["issued_at"], json.loads(first)["issued_at"])

    def test_failure_while_copying_leaves_nothing(self):
        self.report()
        with mock.patch("ledger.issue.shutil.copyfile",
                        side_effect=OSError("disk full")):
            err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("disk full", err)
        eds = self.home.path("reports", AS_OF, "editions")
        self.assertEqual(os.listdir(str(eds)) if eds.exists() else [], [])
        self.assertFalse((self.home.team_sprint_dir("Claims", SID)
                          / "issued.json").exists())
        self.assertEqual(self.ok("issue", "--as-of", AS_OF)["edition"], 1)

    def test_failure_writing_marker_is_repaired_by_rerun(self):
        self.report()
        real = Home.write_json
        calls = []

        def flaky(home, obj, *parts):
            if parts[-1] == "issued.json":
                calls.append(parts)
                if len(calls) == 2:
                    raise OSError("marker failed")
            return real(home, obj, *parts)

        with mock.patch.object(Home, "write_json", flaky):
            err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("issue", err)
        self.assertTrue((self.edition() / "manifest.json").is_file())
        self.assertFalse((self.home.team_sprint_dir("Claims", SID)
                          / "issued.json").exists())
        r = self.ok("issue", "--as-of", AS_OF)
        self.assertEqual(r["edition"], 1)
        self.assertFalse(self.edition(2).exists())
        for team, sid in (("Claims", SID), ("Data", DSID)):
            self.assertEqual(self.home.read_json(
                *self.home.team_sprint_dir(team, sid).relative_to(
                    self.home.root).parts, "issued.json")["edition"], 1)

    def test_stale_draft_refused(self):
        self.report()
        self.note("carry:21:" + SID, "agreed with the customer")
        err = self.fails("issue", "--as-of", AS_OF)
        self.assertIn("report again", err)
        self.report()
        self.assertEqual(self.ok("issue", "--as-of", AS_OF)["edition"], 1)

    def test_issued_sprint_not_recomputed(self):
        self.report()
        self.ok("issue", "--as-of", AS_OF)
        r = self.ok("analyze", "--all")
        claims = next(t for t in r["teams"] if t["team"] == "Claims")
        row = next(s for s in claims["sprints"] if s["id"] == SID)
        self.assertFalse(row["recomputed"])

    def test_build_report_marks_issued_teams_unchanged(self):
        self.report()
        self.ok("issue", "--as-of", AS_OF)
        self.report()
        self.ok("issue", "--as-of", AS_OF, "--note", "second")
        rep = build_report(self.home, load_config(self.home),
                           load_roster(self.home), date.fromisoformat(AS_OF))
        self.assertTrue(all(t["unchanged"] for t in rep["groups"][0]["teams"]))

    def test_input_hashes_cover_included_sprints(self):
        self.report()
        rep = build_report(self.home, load_config(self.home),
                           load_roster(self.home), date.fromisoformat(AS_OF))
        h = input_hashes(self.home, rep)
        self.assertEqual(h, self.home.read_json("reports", AS_OF, "draft",
                                                "inputs.json")["inputs"])


if __name__ == "__main__":
    unittest.main()
