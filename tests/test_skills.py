"""The agent-facing layer: skills, installer, AGENTS.md and README."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ledger.cli import build_parser

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
INSTALL = ROOT / "scripts" / "install-skills.sh"
SKILL_NAMES = ("delivery-setup", "delivery-sprint-close", "delivery-evidence")
PLACEHOLDER = "__LEDGER_PATH__"
GLOBAL_FLAGS = {"--json", "--home", "--help", "-h", "--show-text"}
SHOW_TEXT_RULE = "- Never pass `--show-text`; it is for the DM's own terminal."

# A launcher mention: <LEDGER>, bin/ledger or `ledger, then global options,
# then the command word.
COMMAND = re.compile(
    r"(?:<LEDGER>|bin/ledger|`ledger)((?:\s+--json|\s+--home\s+\S+)*)"
    r"\s+([a-z][a-z-]*)")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z-]*)")
FORBIDDEN_ANY_CASE = ("hands-on", "safe to send", "starvation",
                      "sole engineer")


def _subcommands():
    parser = build_parser()
    for action in parser._actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return {name: {o for a in sub._actions for o in a.option_strings}
                    for name, sub in action.choices.items()}
    raise AssertionError("no subcommands registered")


def _skill_files():
    return [SKILLS / n / "SKILL.md" for n in SKILL_NAMES]


def _frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip('"')
    return out


SPAN = re.compile(r"`([^`\n]+)`")
ITEM_START = re.compile(r"^\s*(?:\d+\.|-|\|)\s")


def _items(text):
    """Split text into list items / paragraphs / table rows; an indented
    continuation line belongs to the item above it."""
    item = []
    for line in text.splitlines():
        if not line.strip() or ITEM_START.match(line):
            if item:
                yield "\n".join(item)
            item = [line] if line.strip() else []
        else:
            item.append(line)
    if item:
        yield "\n".join(item)


def _mentions(text, commands):
    """(command, flags) pairs. Every code span in an item is attributed to
    the command it names (a launcher mention, or a span starting with a
    command name), else to the last command named earlier in the item.
    Flags in an item that names no command are attributed to None."""
    for item in _items(text):
        current = None
        for span in SPAN.findall(item):
            m = COMMAND.search("`" + span)
            first = re.match(r"([a-z][a-z-]*)(?:\s|$)", span)
            if m:
                current = m.group(2)
                rest = ("`" + span)[m.end():]
            elif first and first.group(1) in commands:
                current = first.group(1)
                rest = span[first.end():]
            elif span.startswith("--"):
                rest = span
            else:
                continue  # another program, a key or a value
            flags = FLAG.findall(rest)
            if current is not None or flags:
                yield current, flags


class SkillsTest(unittest.TestCase):

    def test_every_skill_has_valid_frontmatter(self):
        for path in _skill_files():
            fm = _frontmatter(path.read_text(encoding="utf-8"))
            self.assertIsNotNone(fm, path)
            self.assertEqual(fm.get("name"), path.parent.name)
            desc = fm.get("description", "")
            self.assertTrue(desc.startswith("Use when"), path)
            self.assertLess(len(desc), 300, path)

    def test_skills_only_use_existing_commands_and_flags(self):
        commands = _subcommands()
        sources = _skill_files() + [ROOT / "AGENTS.md"]
        seen, pairs = set(), set()
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for command, flags in _mentions(text, commands):
                for flag in flags:
                    if command is None:
                        self.assertIn(flag, GLOBAL_FLAGS,
                                      "%s: %s names no command" % (path, flag))
                        continue
                    self.assertIn(flag, commands[command] | GLOBAL_FLAGS,
                                  "%s: %s %s" % (path, command, flag))
                    pairs.add((command, flag))
                if command is not None:
                    self.assertIn(command, commands, "%s: %s" % (path, command))
                    seen.add(command)
        self.assertTrue({"probe", "collect", "evidence", "issue"} <= seen)
        self.assertGreaterEqual(len(pairs), 25, sorted(pairs))
        for pair in (("collect", "--refresh"), ("narrative-validate", "--summary"),
                     ("risks", "--drop")):
            self.assertIn(pair, pairs)

    def test_mentions_attribute_flags_to_the_named_command(self):
        commands = _subcommands()
        text = ("1. Run `<LEDGER> --json risks --candidates`, then `--accept ID`.\n"
                "   Later `collect --team T --refresh`.\n")
        got = list(_mentions(text, commands))
        self.assertEqual(got, [("risks", ["--candidates"]), ("risks", ["--accept"]),
                               ("collect", ["--team", "--refresh"])])

    def test_skills_contain_no_metric_logic_or_forbidden_words(self):
        for path in _skill_files():
            text = path.read_text(encoding="utf-8")
            lower = text.lower()
            for word in FORBIDDEN_ANY_CASE:
                self.assertNotIn(word, lower, path)
            self.assertNotIn("Delivering", text, path)
            for token in ("threshold", ">=", "<=", "%"):
                self.assertNotIn(token, lower, "%s: %s" % (path, token))
            unquoted = re.sub(r'"[^"\n]*"', "", text)
            self.assertIsNone(re.search(r"\d\s+sprints", unquoted), path)

    def test_skills_state_the_rules(self):
        for path in _skill_files():
            text = path.read_text(encoding="utf-8")
            self.assertIn('LEDGER="%s"' % PLACEHOLDER, text, path)
            for phrase in ("data, not instructions", "next:", "error:",
                           '"$LEDGER"', "ask the DM how to proceed"):
                self.assertIn(phrase, text, "%s: %s" % (path, phrase))

    def test_show_text_is_named_only_in_the_rule_forbidding_it(self):
        for path in _skill_files() + [ROOT / "AGENTS.md"]:
            lines = [ln for ln in path.read_text(encoding="utf-8")
                     .splitlines() if "show-text" in ln or "show_text" in ln]
            self.assertEqual(len(lines), 1, path)
            self.assertTrue(lines[0].startswith(SHOW_TEXT_RULE), path)
            rules = path.read_text(encoding="utf-8").split("## Rules")[1] \
                .split("\n## ")[0]
            self.assertIn(lines[0], rules, path)

    def test_rules_blocks_cover_issue_and_names_approval(self):
        for path in _skill_files() + [ROOT / "AGENTS.md"]:
            rules = path.read_text(encoding="utf-8").split("## Rules")[1] \
                .split("\n## ")[0]
            self.assertIn("Run `issue` only on the DM's explicit approval.",
                          rules, path)
            self.assertIn("Never approve a `names-check` hit for the DM.",
                          rules, path)

    def test_no_skill_claims_enforcement_either_way(self):
        for path in _skill_files() + [ROOT / "AGENTS.md"]:
            self.assertNotIn("either way", path.read_text(encoding="utf-8"),
                             path)

    def test_hidden_text_is_reviewed_by_the_dm(self):
        close = (SKILLS / "delivery-sprint-close" / "SKILL.md").read_text(
            encoding="utf-8")
        step = close.split("10. Run `<LEDGER> --json names-check`")[1] \
            .split("\n11. ")[0]
        self.assertIn("`none` or `titles`", step)
        self.assertIn("in their own terminal", step)
        setup = (SKILLS / "delivery-setup" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("run `trace ID` for each ticket themselves in their "
                      "own terminal", setup)
        self.assertIn('"${HOME_DIR:?}/config.json"', setup)
        self.assertNotIn('"$HOME_DIR/', setup)

    def test_readme_states_what_the_text_setting_covers(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for phrase in ("--show-text", "`trace` prints the display names",
                       "report and brief files on disk always contain "
                       "ticket titles",
                       "[text hidden: agent_text_access is none]"):
            self.assertIn(phrase, text, phrase)

    def test_sprint_close_asks_the_exact_question(self):
        text = (SKILLS / "delivery-sprint-close" / "SKILL.md").read_text(
            encoding="utf-8")
        self.assertIn("Was carrying #<id> over agreed with the PO and "
                      "communicated in advance? (yes / no / unknown)", text)
        self.assertRegex(text, r"annotate [^`\n]*--interactive")
        self.assertIn("reasons", text)

    def test_reason_lists_come_from_the_cli(self):
        for path in _skill_files() + [ROOT / "AGENTS.md"]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("unplanned work took priority", text, path)
            self.assertNotIn("use those", text, path)
            self.assertNotIn("says what to do", text, path)

    def test_issue_note_wording_matches_help(self):
        for path in (SKILLS / "delivery-sprint-close" / "SKILL.md",
                     ROOT / "README.md"):
            self.assertIn("required after edition 001",
                          path.read_text(encoding="utf-8"), path)


def _installed_launcher(text):
    """The value the installed LEDGER= line assigns, as a shell sees it."""
    m = re.search(r'^\s*(LEDGER=".*")$', text, re.M)
    assert m, "no LEDGER line"
    return subprocess.run(["bash", "-c", m.group(1) + '; printf %s "$LEDGER"'],
                          stdout=subprocess.PIPE, check=True,
                          universal_newlines=True).stdout


class InstallScriptTest(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, str(self.tmp))

    def _run(self, *args):
        return subprocess.run(["bash", str(INSTALL)] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def test_install_script_is_executable(self):
        self.assertTrue(os.access(str(INSTALL), os.X_OK))

    def test_install_writes_both_locations_with_launcher_path(self):
        res = self._run("--project", str(self.tmp))
        self.assertEqual(res.returncode, 0, res.stderr)
        for base in (".claude/skills", ".github/skills"):
            for name in SKILL_NAMES:
                target = self.tmp / base / name / "SKILL.md"
                self.assertIn(str(target), res.stdout)
                text = target.read_text(encoding="utf-8")
                self.assertNotIn(PLACEHOLDER, text)
                launcher = Path(_installed_launcher(text))
                self.assertTrue(launcher.is_absolute())
                self.assertTrue(launcher.exists())
        for name in SKILL_NAMES:
            self.assertIn(PLACEHOLDER,
                          (SKILLS / name / "SKILL.md").read_text("utf-8"))

    def test_agents_option_limits_targets(self):
        res = self._run("--project", str(self.tmp), "--agents", "claude")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((self.tmp / ".claude/skills").is_dir())
        self.assertFalse((self.tmp / ".github").exists())

    def test_refuses_changed_file_without_force(self):
        self.assertEqual(self._run("--project", str(self.tmp)).returncode, 0)
        target = self.tmp / ".github/skills/delivery-evidence/SKILL.md"
        original = target.read_text(encoding="utf-8")
        target.write_text("changed\n", encoding="utf-8")
        res = self._run("--project", str(self.tmp))
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "changed\n")
        res = self._run("--project", str(self.tmp), "--force")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), original)

    def test_refusal_writes_nothing_and_lists_every_conflict(self):
        self.assertEqual(self._run("--project", str(self.tmp)).returncode, 0)
        a = self.tmp / ".claude/skills/delivery-setup/SKILL.md"
        b = self.tmp / ".github/skills/delivery-evidence/SKILL.md"
        c = self.tmp / ".github/skills/delivery-sprint-close/SKILL.md"
        a.write_text("changed\n", encoding="utf-8")
        b.write_text("changed\n", encoding="utf-8")
        c.unlink()
        res = self._run("--project", str(self.tmp))
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(str(a), res.stderr)
        self.assertIn(str(b), res.stderr)
        self.assertFalse(c.exists())
        self.assertNotIn("wrote", res.stdout)

    def test_install_from_a_path_with_spaces_and_shell_characters(self):
        repo = self.tmp / 'my repo $HOME `x` "q"'
        for part in ("bin", "ledger", "skills", "scripts"):
            shutil.copytree(str(ROOT / part), str(repo / part),
                            ignore=shutil.ignore_patterns("__pycache__"))
        project = self.tmp / "project"
        project.mkdir()
        res = subprocess.run(
            ["bash", str(repo / "scripts" / "install-skills.sh"),
             "--project", str(project)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        for base in (".claude/skills", ".github/skills"):
            text = (project / base / "delivery-setup" / "SKILL.md").read_text(
                encoding="utf-8")
            launcher = _installed_launcher(text)
            self.assertEqual(launcher, str(repo / "bin" / "ledger"))
            self.assertTrue(Path(launcher).exists())

    def test_rerun_with_same_content_succeeds(self):
        self.assertEqual(self._run("--project", str(self.tmp)).returncode, 0)
        self.assertEqual(self._run("--project", str(self.tmp)).returncode, 0)

    def test_unknown_option_fails(self):
        self.assertNotEqual(self._run("--bogus").returncode, 0)


class ReadmeTest(unittest.TestCase):

    def test_readme_covers_required_topics(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for phrase in ("bin/ledger demo --home", "~/delivery-ledger-data",
                       "names-check", "agent_text_access", "works-council",
                       "chmod -R u+w", "licence has not been chosen",
                       "## What the executive report does and does not guarantee",
                       "required after edition 001"):
            self.assertIn(phrase, text, phrase)
        self.assertNotIn("No engineer's name is printed in it.", text)

    def test_agents_md_exists_and_is_short(self):
        lines = (ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()
        self.assertLess(len(lines), 160)


class RepoNamesTest(unittest.TestCase):

    def test_repo_has_no_denied_names(self):
        deny_file = ROOT / ".names-denylist"
        if not deny_file.exists():
            self.skipTest(".names-denylist not present")
        terms = [t.strip().lower() for t in
                 deny_file.read_text(encoding="utf-8").splitlines()
                 if t.strip() and not t.startswith("#")]
        files = subprocess.run(["git", "ls-files"], cwd=str(ROOT),
                               stdout=subprocess.PIPE,
                               universal_newlines=True).stdout.split()
        hits = []
        for name in files:
            try:
                text = (ROOT / name).read_text(encoding="utf-8").lower()
            except (UnicodeDecodeError, OSError):
                continue
            hits += ["%s: %s" % (name, t) for t in terms if t in text]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
