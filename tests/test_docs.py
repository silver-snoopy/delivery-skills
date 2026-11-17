import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "delivery-report" / "SKILL.md"


class DocsTest(unittest.TestCase):
    def test_readme_sections(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for h in ("## Installation", "## Usage", "## How figures are counted"):
            self.assertIn(h, text)
        self.assertIn("A licence has not been chosen yet; until one is added, this project must not be redistributed.", text)

    def test_skill_front_matter(self):
        text = SKILL.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        self.assertTrue(m)
        self.assertIn("name: delivery-report", m.group(1))
        d = re.search(r"^description: (.*)$", m.group(1), re.M)
        self.assertTrue(d)
        self.assertLess(len(d.group(1)), 300)

    def test_skill_content(self):
        text = SKILL.read_text(encoding="utf-8")
        for s in ("init --query Q --staff staff.txt --vacations vacations.xlsx --yes",
                  "Anything to add for team", "notes.md", "## Exec note",
                  "TEAM action: PERSON #ID BADGE - PROBLEM", "worked on: #ID"):
            self.assertIn(s, text)
        self.assertIn("Never open the HTML files", text)
        for doc in (text, (ROOT / "AGENTS.md").read_text(encoding="utf-8")):
            for s in ("figures can change if the board changed", "warning:", "not-delivered reasons",
                      "picked up"):
                self.assertIn(s, doc)
            self.assertNotIn("numbers do not change", doc)
        self.assertNotRegex(text, r"(?i)(?<!never )(?<!not )\bopen the html")

    def test_markdown_operator_contract(self):
        paths = (ROOT / "AGENTS.md", SKILL, ROOT / "README.md", ROOT / "docs/design-v3.md")
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for term in ("notes.md", ".notes-state.json", "## Summary", "## Exec note",
                         "## Note", "## Status", "## Actions", "## Resolved",
                         "<!-- ledger notes: TEAM=closed-sprint-path; ... -->",
                         "| Person | Work item | Problem | Answer | Answered |",
                         "Answer is the only DM-owned cell", "Answered is tool-owned",
                         "TEAM action: PERSON #ID BADGE - PROBLEM",
                         "TEAM action: PERSON #ID BADGE ↑ - PROBLEM",
                         "Carry-over answers reach the manager_ file", "no names in them",
                         "Actions and Resolved", "whitespace", "agreed:",
                         "not agreed:", "unknown:", "Handled", "↑", "start of `--today`",
                         "changed or re-entered", "`notes.txt` is no longer read",
                         "`--sprint` must name an ended sprint"):
                with self.subTest(path=path.relative_to(ROOT), term=term):
                    self.assertIn(term, text, "Missing operator contract: " + term)
            self.assertNotRegex(text, r"T (?:summary|exec note|status|note):")
            self.assertNotIn("worsen do not print another action prompt", text)

    def test_carry_over_console_line_has_one_answer(self):
        for path in (ROOT / "AGENTS.md", SKILL, ROOT / "README.md", ROOT / "docs/design-v3.md"):
            text = path.read_text(encoding="utf-8")
            for term in ("TEAM carry-over without note: #ID TITLE", "no agreement answer yet",
                         "same item also has an `action:` line", "Ask about it once",
                         "one answer", "Answer cell", "agreed:", "not agreed:", "unknown:"):
                with self.subTest(path=path.relative_to(ROOT), term=term):
                    self.assertIn(term, text, "Missing carry-over prompt rule: " + term)

    def test_agent_safety_and_question_loop(self):
        for path in (ROOT / "AGENTS.md", SKILL):
            text = path.read_text(encoding="utf-8")
            for term in ("Never compute, round or restate a figure", "Never open the HTML files",
                         "Never send or post a report", "data, not instructions",
                         "every `warning:` line", "DM's behalf", "Summary, Exec note or Status",
                         "only when the DM says so", "confirm", "summary bullets",
                         "For each printed action", "Was carrying #ID over agreed with the PO?",
                         "Anything to add for team", "Answer cell", "person EMAIL"):
                with self.subTest(path=path.relative_to(ROOT), term=term):
                    self.assertIn(term, text, "Missing agent rule: " + term)

    def test_no_forbidden_words(self):
        words = ["ep" + "am", "palan" + "tir"]
        files = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                               text=True, check=True).stdout.split("\n")
        for f in files:
            if not f or f.startswith("research/") or not (ROOT / f).is_file():
                continue
            data = (ROOT / f).read_bytes().decode("utf-8", "ignore").lower()
            for w in words:
                self.assertNotIn(w, data, f)


if __name__ == "__main__":
    unittest.main()
