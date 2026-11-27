# Static Analysis, `no-explaining-comments`, and Pre-Push Hook: Implementation Plan

Spec: `docs/superpowers/specs/2026-11-27-static-analysis-design.md`
Worktree: `.worktrees/static-analysis` (branch `feat/static-analysis`)

## Tasks

### Task 1: Build `scripts/check_comments.py` (`NEC001`) and `tests/test_comments.py`
- Implement stdlib `tokenize`-based checker `scripts/check_comments.py` (Python 3.9+, zero third-party deps, executable, passes its own `NEC001` rule!).
- Exact full-token allowlist (`re.fullmatch`, no trailing prose):
  - Line 1 col 0 shebang (`^#!.*$`)
  - PEP 263 encoding header on line 1 or line 2 col 0 (only when preceded by nothing or a line-1 shebang; strict PEP 263 cookie with no trailing explanation)
  - `# noqa: <CODES>` (non-empty comma-separated Ruff/flake8 codes, no trailing prose)
  - `# type: ignore[<codes>]` (non-empty comma-separated mypy error codes, no trailing prose)
  - `# pragma: no cover` (no trailing prose)
- Hard failure (exit code 1) on:
  - any disallowed `tokenize.COMMENT` (`NEC001 [no-explaining-comments]`)
  - missing/non-existent target path
  - unreadable file, `UnicodeDecodeError`, `tokenize.TokenError`, `IndentationError`, `tokenize.ERRORTOKEN`
- Comprehensive unit tests in `tests/test_comments.py` covering every allowed/rejected pattern and failure mode.

### Task 2: Add `pyproject.toml` & `.pre-commit-config.yaml`, migrate comments & remediate `ruff check` + `mypy`
- Create `pyproject.toml` with Ruff (`target-version = "py39"`, `line-length = 110`, rules `E, W, F, I, B, UP, SIM, ERA, PGH, RUF, S`, ignore `E501`, `allowed-confusables`, documented narrow per-file ignores) and mypy (`python_version = "3.9"`, `check_untyped_defs`, `warn_unused_ignores`, `warn_redundant_casts`, `warn_unreachable`, `no_implicit_optional`, `show_error_codes`).
- Create `.pre-commit-config.yaml` (`default_install_hook_types: [pre-push]`, `default_stages: [pre-push]`, pinned `ruff-pre-commit v0.11.0`, `mirrors-mypy v1.15.0` overriding inherited args, `no-explaining-comments`, `unittest`, all `pass_filenames: false` and `always_run: true`).
- Migrate all 52 comments in `ledger/` (18 `# type:` → Python 3.9 native annotations; 3 `# noqa: E731` → `def`; 31 prose comments → docstrings or cleaner self-explanatory code).
- Remediate all `ruff check` findings across `ledger/`, `scripts/`, `tests/` and all `mypy` findings across `ledger/` using pinned `ruff==0.11.0` and `mypy==1.15.0`.
- Verify `python3 -m unittest discover -s tests`, `scripts/check_comments.py ledger scripts`, `ruff check ledger scripts tests`, and `mypy --config-file=pyproject.toml ledger` all exit 0.

### Task 3: One-time `ruff format` pass & raise `tests/test_size.py` cap to `4600`
- Run pinned `ruff format ledger scripts tests`.
- Raise `tests/test_size.py` cap from `3600` to `4600` and verify `wc -l ledger/*.py` <= 4,600.
- Verify `ruff format --check ledger scripts tests`, `ruff check ledger scripts tests`, `mypy --config-file=pyproject.toml ledger`, `scripts/check_comments.py ledger scripts`, and `unittest` all pass.

### Task 4: Update `AGENTS.md`, `README.md`, `docs/design-v3.md`, and spec status
- Add the scoped `no-explaining-comments` + *"Don't justify the workaround. Fix the real problem instead."* rule to `AGENTS.md`.
- Update both code-cap references in `docs/design-v3.md` to `4,600`.
- Document optional contributor pre-push hook setup (`pre-commit install --install-hooks`, `pre-commit run --all-files --hook-stage pre-push`, clearly separated from the zero-dependency Python 3.9+ runtime) in `README.md`.
- Update `docs/superpowers/specs/2026-11-27-static-analysis-design.md` status to `implemented`.
- Verify `tests/test_docs.py` and full test suite pass.

### Task 5: Install & verify `pre-commit` pre-push hook end-to-end
- Run `pre-commit install --install-hooks` and `pre-commit run --all-files --hook-stage pre-push` in the worktree.
- Verify all 5 hooks (`ruff`, `ruff-format`, `mypy`, `no-explaining-comments`, `unittest`) pass with exit code 0, including on config-only / non-Python changes.
