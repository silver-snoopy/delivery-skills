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
                  "Anything to add for team", "notes.txt", "T exec note:",
                  "carry-over without note", "delivered: #ID"):
            self.assertIn(s, text)
        self.assertIn("Never open the HTML files", text)
        self.assertNotRegex(text, r"(?i)(?<!never )(?<!not )\bopen the html")

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
