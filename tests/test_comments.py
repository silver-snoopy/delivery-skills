import importlib.util
import io
import os
import stat
import subprocess
import sys
import tempfile
import tokenize
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_comments.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_comments", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


class CheckCommentsTest(unittest.TestCase):
    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )

    def _check_source(self, source: bytes, name: str = "sample.py") -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / name
            target.write_bytes(source)
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = CHECKER.main([str(target)])
            return code, buf.getvalue()

    def test_rejects_inline_and_full_line_explanatory_comments(self):
        source = b"# full-line explanation\nx = 1  # inline explanation\n"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "bad.py"
            target.write_bytes(source)
            res = self._run_cli(str(target))
            self.assertEqual(res.returncode, 1)
            lines = res.stdout.strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(
                lines[0],
                f"{target}:1:1: NEC001 [no-explaining-comments] "
                "Explanatory comment is not allowed; make the code self-explanatory or fix the underlying issue.",
            )
            self.assertEqual(
                lines[1],
                f"{target}:2:8: NEC001 [no-explaining-comments] "
                "Explanatory comment is not allowed; make the code self-explanatory or fix the underlying issue.",
            )

    def test_rejects_disallowed_directives_and_trailing_prose(self):
        cases = [
            ("bare_noqa", b"x = 1  # noqa\n"),
            ("empty_noqa", b"x = 1  # noqa:\n"),
            ("malformed_noqa_lower", b"x = 1  # noqa: foo\n"),
            ("malformed_noqa_alpha", b"x = 1  # noqa: INVALID\n"),
            ("noqa_trailing_prose", b"x = 1  # noqa: E501 because reasons\n"),
            ("bare_type_ignore", b"x = 1  # type: ignore\n"),
            ("empty_type_ignore", b"x = 1  # type: ignore[]\n"),
            ("malformed_type_ignore_digits", b"x = 1  # type: ignore[123]\n"),
            ("malformed_type_ignore_upper", b"x = 1  # type: ignore[ArgType]\n"),
            ("type_ignore_trailing_comment", b"x = 1  # type: ignore[arg-type] # why\n"),
            ("type_ignore_trailing_prose", b"x = 1  # type: ignore[arg-type] reason\n"),
            ("pragma_trailing_prose", b"x = 1  # pragma: no cover because reasons\n"),
            ("multiple_directives", b"x = 1  # noqa: E501 # type: ignore[arg-type]\n"),
            ("shebang_line2", b"\n#!/usr/bin/env python3\n"),
            ("shebang_indented", b"  #!/usr/bin/env python3\n"),
            ("shebang_inline", b"x = 1  #!/usr/bin/env python3\n"),
            ("encoding_line3", b"\n\n# coding: utf-8\n"),
            ("encoding_after_code", b"x = 1\n# coding: utf-8\n"),
            ("encoding_after_encoding", b"# coding: utf-8\n# coding: utf-8\n"),
            ("encoding_indented", b"  # coding: utf-8\n"),
            ("encoding_trailing_prose", b"# coding: utf-8 because reasons\n"),
            ("emacs_encoding_trailing_prose", b"# -*- coding: utf-8 -*- extra\n"),
        ]
        for label, source in cases:
            with self.subTest(label=label):
                code, output = self._check_source(source)
                self.assertEqual(code, 1, f"{label} should fail, got output: {output!r}")
                self.assertIn("NEC001 [no-explaining-comments]", output)

    def test_allows_permitted_headers_and_directives(self):
        cases = [
            ("shebang_only", b"#!/usr/bin/env python3\nx = 1\n"),
            ("encoding_line1_emacs", b"# -*- coding: utf-8 -*-\nx = 1\n"),
            ("encoding_line1_equals", b"# coding=utf-8\nx = 1\n"),
            ("encoding_line1_colon", b"# coding: utf-8\nx = 1\n"),
            (
                "shebang_then_encoding",
                b"#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\nx = 1\n",
            ),
            ("blank_then_encoding", b"\n# coding: utf-8\nx = 1\n"),
            ("noqa_single", b"x = 1  # noqa: E501\n"),
            ("noqa_multiple", b"x = 1  # noqa: E501, F401\n"),
            ("type_ignore_single", b"x = 1  # type: ignore[arg-type]\n"),
            ("type_ignore_multiple", b"x = 1  # type: ignore[arg-type, return-value]\n"),
            ("pragma_no_cover", b"if False:  # pragma: no cover\n    pass\n"),
        ]
        for label, source in cases:
            with self.subTest(label=label):
                code, output = self._check_source(source)
                self.assertEqual(code, 0, f"{label} should pass, got output: {output!r}")
                self.assertEqual(output, "")

    def test_ignores_hashes_in_strings_fstrings_and_docstrings(self):
        source = (
            b'"""Module docstring with # TEAM and # not a comment."""\n'
            b'A = "#%d"\n'
            b'B = "# TEAM"\n'
            b'C = f"prefix #{A} # still string"\n'
            b'D = """\n'
            b"# inside triple-quoted string\n"
            b'"""\n'
        )
        code, output = self._check_source(source)
        self.assertEqual(code, 0, output)
        self.assertEqual(output, "")

    def test_directory_discovery_sorted_and_skips_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b.py").write_text("x = 1  # second\n", encoding="utf-8")
            (root / "a.py").write_text("x = 1  # first\n", encoding="utf-8")
            (root / "notes.txt").write_text("# ignored non-python file\n", encoding="utf-8")
            cache_dir = root / "__pycache__"
            cache_dir.mkdir()
            (cache_dir / "ignored.py").write_text("# cached comment\n", encoding="utf-8")

            res = self._run_cli(str(root))
            self.assertEqual(res.returncode, 1)
            lines = res.stdout.strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].startswith(f"{root / 'a.py'}:1:8: NEC001"))
            self.assertTrue(lines[1].startswith(f"{root / 'b.py'}:1:8: NEC001"))

    def test_missing_path_and_no_args_fail_nonzero(self):
        no_args = self._run_cli()
        self.assertNotEqual(no_args.returncode, 0)

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist.py"
            res = self._run_cli(str(missing))
            self.assertEqual(res.returncode, 1)
            self.assertIn("does not exist", res.stdout)

    def test_unreadable_file_fails(self):
        if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
            self.skipTest("chmod 0o000 is not enforced under root or Windows")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "secret.py"
            target.write_text("x = 1\n", encoding="utf-8")
            target.chmod(0)
            try:
                res = self._run_cli(str(target))
                self.assertEqual(res.returncode, 1)
                self.assertIn("cannot read file", res.stdout)
            finally:
                target.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_invalid_utf8_fails(self):
        for label, payload in [
            ("raw_invalid_utf8", b'x = "\xff"\n'),
            ("declared_utf8_later_invalid", b'# coding: utf-8\nx = 1\ny = "\xff"\n'),
        ]:
            with self.subTest(label=label):
                code, output = self._check_source(payload)
                self.assertEqual(code, 1)
                self.assertIn("cannot decode file", output)

    def test_token_error_indentation_error_and_errortoken_fail(self):
        for label, payload in [
            ("unclosed_string", b'x = "unterminated\n'),
            ("bad_indentation", b"if True:\n    x = 1\n  y = 2\n"),
            ("stray_dollar", b"x = $1\n"),
            ("stray_backtick", b"x = `1`\n"),
        ]:
            with self.subTest(label=label):
                code, output = self._check_source(payload)
                self.assertEqual(code, 1, f"{label} should exit 1, got {output!r}")
                self.assertIn("error:", output)

    def test_explicit_errortoken_fails(self):
        fake_tokens = [tokenize.TokenInfo(tokenize.ERRORTOKEN, "$", (1, 4), (1, 5), "x = $1\n")]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "err.py"
            target.write_text("x = 1\n", encoding="utf-8")
            with mock.patch.object(CHECKER.tokenize, "tokenize", return_value=iter(fake_tokens)):
                errors = CHECKER.check_file(target)
            self.assertEqual(len(errors), 1)
            self.assertIn(f"{target}:1:5: error: invalid token '$'", errors[0])

    def test_self_check_passes(self):
        res = self._run_cli(str(SCRIPT))
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout, "")


if __name__ == "__main__":
    unittest.main()
