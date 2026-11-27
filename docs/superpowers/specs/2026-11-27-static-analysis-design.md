# Static code analysis, `no-explaining-comments`, and pre-push hook: design

Status: implemented (2026-11-27), updated 2026-11-27 for Python 3.12+ (`ruff-pre-commit v0.17.6`, `mirrors-mypy v2.4.1`). Option A selected: `ruff format --check` enforced, physical-line cap raised to `4,600`.

## 1. Intent

`delivery-skills` is a small, zero-runtime-dependency Python 3.9+ CLI (`ledger/` is ~3,560 lines, enforced by `tests/test_size.py`). Today quality checks consist solely of `python3 -m unittest discover -s tests`. There is no linter, formatter check, type checker, comment policy check, or pre-push git hook.

This spec introduces a fast, modern, research-backed Python static analysis toolchain that:
1. catches bugs, unused code, security pitfalls, and type mismatches automatically;
2. enforces a hard **`no-explaining-comments`** lint rule (`NEC001`) on `ledger/` and `scripts/` so code cannot hide workarounds behind explanatory `#` comments;
3. codifies the principle in `AGENTS.md` (*"Don't justify the workaround. Fix the real problem instead."*);
4. runs automatically on **`git push`** via `pre-commit` (`pre-push` stage), while keeping `ledger/` 100% Python 3.9+ standard-library-only at runtime.

## 2. Tooling Research & Recommendations (2026 Python Best Practices)

Modern Python static analysis has consolidated around Rust-powered tooling and unified `pyproject.toml` configuration:

| Concern | Chosen tool | Alternatives considered | Why this choice |
|---|---|---|---|
| **Linting & formatting** | **Ruff** (`ruff` / `ruff-check` + optional `ruff-format --check`) | Flake8 + isort + pyupgrade + Black + Bandit | Single Rust binary, 10–100× faster, replaces the legacy multi-tool stack, includes `B` (bugbear), `SIM` (simplify), `UP` (pyupgrade), `ERA` (commented-out code), `PGH`, `RUF`, and `S` (Bandit security checks). |
| **Type checking** | **mypy** (pinned `<2.0`, e.g. `v1.15.0`) | **Pyright/basedpyright**, **ty** (Astral) | Stable, Python-native (no Node runtime). **Constraint:** mypy 2.0 dropped `--python-version 3.9`, so we pin a `1.15.x` release that still targets Python 3.9 while running under the contributor's local Python (3.11+). Astral's `ty` is still pre-1.0 (`0.0.x`); `pyproject.toml` makes migrating later straightforward. |
| **Custom comment rule (`no-explaining-comments`)** | **`scripts/check_comments.py`** (stdlib `tokenize`) | Flake8 custom plugin, regex grep | Ruff has no built-in `no-explaining-comments` rule (only `ERA001` for commented-out code). A small stdlib `tokenize` script is zero-dependency, exact (never false-positives on `#` inside strings like `"#%d"`), and runs in <50ms. |
| **Git hook runner** | **`pre-commit`** (`pre-push` stage) | `.githooks/pre-push` | `pre-commit` (`4.7.0`) is already installed locally, pins exact tool revisions (`rev:`), caches environments for offline use after initial install, and runs identically in CI or via `pre-commit run --all-files --hook-stage pre-push`. |

**Runtime vs. contributor boundary:** End users running `bin/ledger` still need **only Python 3.9+ standard library** (zero packages installed). Contributor tooling (`pre-commit` 4.7.0 requires Python 3.11+ and initial network access to populate hook environments) is strictly optional for development and documented separately in `README.md`.

## 3. Configuration (`pyproject.toml`)

Centralize Ruff and mypy configuration in `pyproject.toml` at the repo root:

### Ruff
- `target-version = "py39"` (matches `docs/design-v3.md`).
- `line-length = 110`.
- Selected rule families (`[tool.ruff.lint]`):
  - `E`, `W` — pycodestyle errors/warnings (`E501` explicitly ignored if `ruff format` is enabled, per Ruff's formatter compatibility guidance, because the formatter intentionally leaves unbreakable long strings >110 chars).
  - `F` — Pyflakes (unused imports, undefined names)
  - `I` — isort (import sorting)
  - `B` — flake8-bugbear
  - `UP` — pyupgrade (under `py39`, `UP006` uses native `dict[...]`, `list[...]`, `tuple[...]` while retaining `Optional[...]` — never introducing evaluated `T | None` on Python 3.9)
  - `SIM` — flake8-simplify
  - `ERA` — eradicate (commented-out code)
  - `PGH` — pygrep-hooks (`PGH003` requires specific mypy error codes on `# type: ignore[code]`)
  - `RUF` — Ruff-specific rules (`RUF100` flags unused `# noqa`; allow intentional Unicode report punctuation such as en-dash `–` and minus `−` via `lint.allowed-confusables = ["–", "−", "’", "“", "”"]` so `RUF001` never forces changes to documented report copy)
  - `S` — flake8-bandit security checks.
- **Explicit security-rule remediation & narrow per-file ignores** (rationale documented in `pyproject.toml` TOML comments, never Python comments):
  - `ledger/ado.py`: validate `https://` scheme on `organization` / `Request` URLs (or narrow `S310` ignore if URL validation is explicit); review WIQL string construction (`S608`, parameterized or narrowly exempted since WIQL has no bind parameters).
  - `ledger/xlsx.py`: narrow `S314` (`xml.etree.ElementTree`) exemption — `defusedxml` would add a third-party runtime dependency, violating the stdlib-only design; files are local `vacations.xlsx` archives.
  - `tests/*.py`: exempt `S101` (`assert`) and `S607` in `tests/test_docs.py` (git/CLI subprocess test calls).

### Formatter & `tests/test_size.py` cap (Option A selected)
A live dry-run probe showed `ruff format --line-length 110` expands `ledger/*.py` from **3,561 to ~4,460 physical lines** (because `tests/test_size.py` counts all physical lines, including blanks and wrapped multi-line calls). Per DM decision (2026-11-27):
- Enforce `ruff format --check` on `ledger/`, `scripts/`, and `tests/` (with a dedicated one-time formatting pass).
- Ignore `E501` in `[tool.ruff.lint]` per Ruff's formatter compatibility guidance.
- Raise the physical-line cap in `tests/test_size.py` and both references in `docs/design-v3.md` from `3,600` to `4,600`.

### mypy (pinned `<2.0`, e.g. `v1.15.0`)
- `[tool.mypy]`:
  - `python_version = "3.9"`
  - `check_untyped_defs = true`
  - `warn_unused_ignores = true`
  - `warn_redundant_casts = true`
  - `warn_unreachable = true`
  - `no_implicit_optional = true`
  - `show_error_codes = true`
- **Remediation phase budgeted:** A baseline probe found ~87 existing type errors across 6 modules (e.g., unchecked optional regex match in `ledger/xlsx.py:49`, `months = [None] * ...` in `ledger/xlsx.py:153`, optional property narrowing in `ledger/metrics.py:255`, invalid trailing prose on a type comment in `ledger/setup.py:73`, and multi-assignment type comments that must be split into separate annotated assignments). Implementation includes a dedicated mypy remediation pass against the exact pinned `v1.15.x` version using Python 3.9 native generics (`dict[...]`, `list[...]`, `Optional[...]`).

## 4. The `no-explaining-comments` Lint Rule (`NEC001`)

### Scope & limitations
- **Scope:** All `.py` files under `ledger/` and `scripts/` (explicitly excludes `tests/`, which uses comments as test-case section headers, and non-Python shell scripts like `bin/ledger` and `scripts/install-skills.sh`).
- **Known limitation:** `NEC001` enforces Python `#` comment token syntax only. Docstrings are allowed (and standalone string expressions are not comment tokens). Preventing disguised workaround justifications in docstrings remains governed by `AGENTS.md` and code review.

### Exact directive grammar & failure contract (`scripts/check_comments.py`)
- Uses Python's standard-library `tokenize.tokenize()` to inspect `tokenize.COMMENT` tokens (never false-positives on `#` inside strings or docstrings).
- **Output format:** Exits with **code 1** on any violation or error:
  ```
  ledger/metrics.py:103:42: NEC001 [no-explaining-comments] Explanatory comment is not allowed; make the code self-explanatory or fix the underlying issue.
  ```
- **Strict full-token allowlist (`re.fullmatch`, NO trailing prose permitted):**
  1. **Shebang:** line `1`, column `0`, matching `^#!.*$`.
  2. **PEP 263 encoding header:** line `1` or `2`, column `0` (only when preceded by nothing or a shebang), matching `^#.*coding[:=]\s*[-\w.]+.*$` (standard PEP 263 cookie only; no trailing explanation).
  3. **Code-scoped `# noqa`:** `^#\s*noqa:\s*[A-Z]{1,6}[0-9]{2,4}(?:\s*,\s*[A-Z]{1,6}[0-9]{2,4})*\s*$` (bare `# noqa`, empty list, or trailing prose like `# noqa: E501 because ...` is **rejected**).
  4. **Code-scoped `# type: ignore`:** `^#\s*type:\s*ignore\[[a-z][a-z0-9-]*(?:\s*,\s*[a-z][a-z0-9-]*)*\]\s*$` (bare `# type: ignore`, empty `[]`, or trailing `# explanation` is **rejected**).
  5. **Coverage pragma:** `^#\s*pragma:\s*no\s+cover\s*$` (trailing prose is **rejected**).
- **Hard failure on invalid input (exit code 1):**
  - Missing or non-existent target path (never silently succeeds on an empty scan).
  - Unreadable file, `UnicodeDecodeError`, `tokenize.TokenError`, `IndentationError`, or `tokenize.ERRORTOKEN`.

### Migration of existing comments in `ledger/`
Current `tokenize` inventory in `ledger/` (**52 comments total**):
- **18 `# type: ...` comments** → convert to Python 3.9 native variable annotations (`dict[...]`, `list[...]`, `Optional[...]`), splitting multi-assignment type comments (`open_ages, waits, stale = {}, {}, {}`) into separate annotated lines.
- **3 `# noqa: E731`** (inline `lambda` assignments) → replace with small `def` functions (eliminates `# noqa` completely).
- **31 prose comments** → fold legitimate invariants into docstrings, extract self-documenting helpers, or simplify code so no explanatory `#` comment remains.

## 5. `AGENTS.md` Prose Addition

Add a scoped **Code quality** bullet under `## Rules` in `AGENTS.md` (compatible with `tests/test_docs.py`, which checks specific operator-contract substrings and forbidden words across tracked files):

```markdown
- **No explaining comments (`no-explaining-comments` in `ledger/` and `scripts/`):** Don't justify the workaround. Fix the real problem instead. Explanatory `#` comments are a lint error (`NEC001`); express intent through clear names, small helpers, explicit types, and docstrings. Only shebangs, PEP 263 encoding headers, `# pragma: no cover`, and code-scoped tool directives (`# noqa: CODE`, `# type: ignore[code]`) without trailing prose are allowed.
```

## 6. Pre-Push Git Hook (`.pre-commit-config.yaml`)

Configure `pre-commit` as a **whole-repository gate** on `git push` (with `default_install_hook_types: [pre-push]`, `pass_filenames: false`, `always_run: true`, and explicit targets so config-only or deletion-only pushes never silently skip checks and mypy always checks unchanged callers):

```yaml
default_install_hook_types: [pre-push]
default_stages: [pre-push]

repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.11.0  # pin exact tested revision; hook IDs match that revision
    hooks:
      - id: ruff
        args: [ledger, scripts, tests]
        pass_filenames: false
        always_run: true
      - id: ruff-format
        args: [--check, ledger, scripts, tests]
        pass_filenames: false
        always_run: true

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.15.0  # pinned <2.0 to retain --python-version 3.9 support
    hooks:
      - id: mypy
        args: [--config-file=pyproject.toml, ledger]
        pass_filenames: false
        always_run: true

  - repo: local
    hooks:
      - id: no-explaining-comments
        name: no-explaining-comments (NEC001)
        entry: python3 scripts/check_comments.py ledger scripts
        language: system
        pass_filenames: false
        always_run: true

      - id: unittest
        name: unittest
        entry: python3 -m unittest discover -s tests
        language: system
        pass_filenames: false
        always_run: true
```

*(Note: overrides `mirrors-mypy`'s default `--ignore-missing-imports` and `--scripts-are-modules` args so missing imports in this stdlib-only package are caught.)*

### Contributor setup (documented in `README.md`, separate from end-user runtime)
- Requires `pre-commit` (Python 3.11+) and initial network access to build hook environments:
  ```sh
  pre-commit install --install-hooks
  ```
- Run manually anytime (works offline after `--install-hooks`):
  ```sh
  pre-commit run --all-files --hook-stage pre-push
  ```
- Note: local git hooks are an optional developer safety net (they can be bypassed with `--no-verify`); CI enforcement can reuse the exact same `.pre-commit-config.yaml` later.

## 7. Goals, non-goals, anti-goals

- **Goals:**
  - Single-command whole-repository static analysis covering lint, types, `no-explaining-comments`, and unit tests.
  - Enforced automatically on `git push` via `pre-commit`.
  - Zero runtime dependencies added to `ledger/`.
- **Non-goals:**
  - GitHub Actions CI workflows (follow-up if desired, reusing `.pre-commit-config.yaml`).
  - Switching `tests/` from stdlib `unittest` to `pytest`.
  - Strict 100% type annotations on every internal helper on day one.
- **Anti-goals:**
  - Allowing bare `# noqa`, bare `# type: ignore`, or trailing prose after directives to bypass `NEC001`.
  - Adding runtime dependencies (such as `defusedxml`) solely to silence a linter.
  - Breaking `tests/test_docs.py` (including its forbidden-word scan across newly tracked config/script/doc files).

## 8. Tests

- Unit tests for `scripts/check_comments.py` (`tests/test_comments.py`):
  - rejects inline and full-line explanatory `#` comments with `NEC001` and exit code 1;
  - rejects bare `# noqa`, bare `# type: ignore`, empty `# type: ignore[]`, malformed codes, multiple directives, misplaced shebang/encoding headers (wrong line/col), and **trailing prose** (`# noqa: E501 because ...`, `# type: ignore[arg-type] # why`, `# pragma: no cover reason`);
  - allows col-0 line-1 shebang, PEP 263 encoding header, `# noqa: E501`, `# noqa: E501, F401`, `# type: ignore[arg-type]`, `# pragma: no cover`;
  - ignores `#` inside string literals, triple-quoted strings, and docstrings (`"#%d"`, `"# TEAM"`);
  - exits non-zero on missing paths, unreadable files, `UnicodeDecodeError`, `TokenError`, `IndentationError`, and `ERRORTOKEN`.
- Existing `tests/test_size.py` and `tests/test_docs.py` continue to pass.
- End-to-end verification: `pre-commit run --all-files --hook-stage pre-push` exits 0, including on config-only and deletion-only changes.
