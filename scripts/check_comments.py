#!/usr/bin/env python3
"""Check Python files for disallowed explanatory comments (NEC001)."""

import argparse
import io
import os
import re
import sys
import tokenize
from pathlib import Path
from typing import Optional

RULE_CODE = "NEC001"
RULE_NAME = "no-explaining-comments"
VIOLATION_MESSAGE = (
    "Explanatory comment is not allowed; make the code self-explanatory or fix the underlying issue."
)

SHEBANG_RE = re.compile(r"^#!.*$")
ENCODING_RE = re.compile(
    r"^#\s*(?:-\*-\s*(?:en)?coding[:=]\s*[-\w.]+\s*-\*-|(?:en)?coding[:=]\s*[-\w.]+)\s*$"
)
NOQA_RE = re.compile(r"^#\s*noqa:\s*[A-Z]{1,6}[0-9]{2,4}(?:\s*,\s*[A-Z]{1,6}[0-9]{2,4})*\s*$")
TYPE_IGNORE_RE = re.compile(r"^#\s*type:\s*ignore\[[a-z][a-z0-9-]*(?:\s*,\s*[a-z][a-z0-9-]*)*\]\s*$")
PRAGMA_NO_COVER_RE = re.compile(r"^#\s*pragma:\s*no\s+cover\s*$")


def is_allowed_comment(
    text: str,
    line: int,
    col: int,
    line1_allows_encoding: bool,
) -> bool:
    """Return True if a comment token matches the strict NEC001 allowlist."""
    if line == 1 and col == 0 and SHEBANG_RE.fullmatch(text):
        return True
    if col == 0 and (line == 1 or (line == 2 and line1_allows_encoding)) and ENCODING_RE.fullmatch(text):
        return True
    return bool(
        NOQA_RE.fullmatch(text) or TYPE_IGNORE_RE.fullmatch(text) or PRAGMA_NO_COVER_RE.fullmatch(text)
    )


def check_file(path: Path) -> list[str]:
    """Check a single Python file for disallowed comments or tokenization errors."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return [f"{path}: error: cannot read file: {exc}"]

    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        raw.decode(encoding)
    except (SyntaxError, LookupError, UnicodeDecodeError) as exc:
        return [f"{path}: error: cannot decode file: {exc}"]

    errors: list[str] = []
    line1_allows_encoding = True
    try:
        for tok in tokenize.tokenize(io.BytesIO(raw).readline):
            if tok.type == tokenize.ERRORTOKEN or (tok.type == tokenize.OP and tok.exact_type == tokenize.OP):
                line, col = tok.start
                errors.append(f"{path}:{line}:{col + 1}: error: invalid token {tok.string!r}")
                return errors

            tok_line, tok_col = tok.start
            if tok_line == 1:
                is_line1_shebang = (
                    tok.type == tokenize.COMMENT and tok_col == 0 and bool(SHEBANG_RE.fullmatch(tok.string))
                )
                if tok.type not in (tokenize.NL, tokenize.ENDMARKER) and not is_line1_shebang:
                    line1_allows_encoding = False

            if tok.type == tokenize.COMMENT and not is_allowed_comment(
                tok.string, tok_line, tok_col, line1_allows_encoding
            ):
                errors.append(
                    f"{path}:{tok_line}:{tok_col + 1}: {RULE_CODE} [{RULE_NAME}] {VIOLATION_MESSAGE}"
                )
    except (
        tokenize.TokenError,
        IndentationError,
        SyntaxError,
        UnicodeDecodeError,
    ) as exc:
        errors.append(f"{path}: error: failed to tokenize file: {exc}")

    return errors


def _walk_directory(target: Path) -> tuple[list[Path], list[str]]:
    """Recursively collect sorted .py files under target, skipping __pycache__."""
    files: list[Path] = []
    errors: list[str] = []

    def record_walk_error(exc: OSError) -> None:
        location = exc.filename or str(target)
        errors.append(f"{location}: error: cannot walk directory: {exc}")

    for root, dirnames, filenames in os.walk(target, onerror=record_walk_error):
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                files.append(Path(root) / filename)

    return files, errors


def discover_files(targets: list[str]) -> tuple[list[Path], list[str]]:
    """Resolve CLI targets into Python files to check plus any path errors."""
    files: list[Path] = []
    errors: list[str] = []

    for raw_target in targets:
        target = Path(raw_target)
        try:
            exists = target.exists()
        except OSError as exc:
            errors.append(f"{raw_target}: error: cannot access path: {exc}")
            continue

        if not exists:
            errors.append(f"{raw_target}: error: path does not exist")
            continue

        if target.is_dir():
            dir_files, dir_errors = _walk_directory(target)
            files.extend(dir_files)
            errors.extend(dir_errors)
        else:
            files.append(target)

    return files, errors


def main(argv: Optional[list[str]] = None) -> int:
    """Run the no-explaining-comments checker from the command line."""
    parser = argparse.ArgumentParser(description="Fail on explanatory Python comments (NEC001).")
    parser.add_argument(
        "paths",
        nargs="+",
        help="Python files or directories to check.",
    )
    args = parser.parse_args(argv)

    files, errors = discover_files(args.paths)
    for path in files:
        errors.extend(check_file(path))

    for error in errors:
        print(error)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
