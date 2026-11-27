# PO Satisfaction & Configurable Manager Metrics: Implementation Plan

Specs:
- `docs/superpowers/specs/2026-11-27-po-satisfaction-design.md`
- `docs/superpowers/specs/2026-11-27-configurable-metrics-design.md`

## Tasks

### Task 1: Config validation, resolution & `run_init()` case-insensitive reuse
- **Files:** `ledger/config.py`, `ledger/setup.py`, `ledger/metrics.py`, `tests/test_config.py`, `tests/test_setup.py`
- Add `DEFAULT_MANAGER_METRICS = ("planned", "delivered", "throughput", "build_time", "first_pass")` and `KNOWN_METRICS = DEFAULT_MANAGER_METRICS + ("po_satisfaction",)`.
- Extend `Config` with `manager_metrics: List[str]` (defaulting to `list(DEFAULT_MANAGER_METRICS)`) and `show_flow: bool = True`.
- Validate `teams` (must be dict, every entry dict, no case-colliding team keys), `manager_metrics` / per-team `metrics` (list of strings, known keys, no duplicates, reject `null` and non-strings before set check), `show_flow` (global & per-team, must be `bool`), warn on unknown per-team keys.
- Update `setup.py:run_init()` to reuse any existing case-insensitive team key in `cfg.teams` rather than duplicating via `setdefault(team, {})`, preserving custom `metrics` and `show_flow`.
- Add `metrics_of(cfg, team)` and `show_flow_of(cfg, team)` in `ledger/metrics.py` and attach `"manager_metrics"` and `"show_flow"` to `team_sprint()` output.
- Tests: full validation matrix in `tests/test_config.py` + `load -> init -> save -> reload` round-trip in `tests/test_setup.py`.

### Task 2: `## PO satisfaction` notes parsing, reused-folder clearing, history & `team_sprint()` resolution
- **Files:** `ledger/config.py`, `ledger/metrics.py`, `ledger/render.py` (missing-sprint dict), `tests/test_notes.py`, `tests/test_metrics.py`
- Add `"PO satisfaction"` right after `"Exec note"` in `_SECTIONS` with its guide line in `_SECTION_GUIDE`.
- Add `po_satisfaction: Dict[str, Tuple[int, str]]` and `po_history: Dict[str, Dict[str, Optional[int]]]` to `Notes`.
- Parse `## PO satisfaction` in `_values()`: single-line `1-5`, `1-5/5`, optional `-`/`–`/`—` comment; invalid/multi-line emits `notes.md <TEAM> has invalid PO satisfaction: <text>` and preserves verbatim.
- Update `load_report_notes()`:
  - Always run a lightweight `parse_notes()` discovery pass over candidate report folders to build read-only `po_history` (authoritative folder = lexicographically greatest path for `(team, sprint_path)`, preserving blank/invalid as `None`) and locate candidate predecessors.
  - When `not exists`, call `_read_notes()` only for selected predecessor folders so `.notes-state.json` baselines remain intact.
  - When `exists` and `sprint is not None` and `prior != sprint["path"]` (including missing/unresolved `prior`), clear non-empty `sec["PO satisfaction"]` before replacing `notes.markers[team]` and warn `notes.md <TEAM> PO satisfaction cleared because the closed sprint changed`.
  - When `sprint is None`, preserve `sec["PO satisfaction"]` untouched.
- Resolve `t["po_satisfaction"]` (`None` or `{score, comment, before, before_sprint}`) and `trend[i]["po_satisfaction"]` in `team_sprint()` against `window` keyed by `sprint["path"]`, with current folder always overriding (including blank/invalid).
- Attach `manager_metrics` and `show_flow` to missing-sprint dicts in `render.py:run_report()`.
- Tests: update `tests/test_notes.py` (including 7-section assertion) + add full parsing, carry-forward, reused-folder, missing-sprint, history, tie-break, and `team_sprint()` tests in `tests/test_notes.py` and `tests/test_metrics.py`.

### Task 3: Configurable `manager_` rendering, PO tile/feedback/trend, dynamic footnote & warning suppression
- **Files:** `ledger/render.py`, `tests/test_render.py`, `tests/test_cli.py`
- Render `.figs` tiles and `_trend_table` rows in exact configured `t["manager_metrics"]` order (omitting `.figs` and trend `<details>` when `metrics == []`).
- Render `po_satisfaction` tile safely (preserving `_fig()` label escaping) with `X <small>/ 5</small>`, `PO satisfaction`, own-line `<span class="chip green|amber|red">Label</span>` (5 Very satisfied, 4 Satisfied, 3 Mixed, 2 Dissatisfied, 1 Very dissatisfied), and `_arrow(str(score), str(before), score > before, [before_sprint])`, or `–` when unrated.
- Conditionally append any extra PO CSS only when PO satisfaction is rendered (never to shared `CSS`).
- Render escaped `PO feedback: <comment>` line below `Scope:` only when `po_satisfaction` is enabled and comment is non-empty.
- Gate `_flow(t)` on `t["show_flow"]`.
- Build `_footnote(False)` in fixed canonical order from configured metrics and `show_flow` across teams, preserving combined throughput/build paragraph and exact byte-for-byte default HTML (including no-flow and all-missing cases), adapting arrow wording to enabled subset, and adding PO sentence + qualifying delivery arrows & board figures when any team enables `po_satisfaction`.
- Suppress `no test states` warning in `run_report()` when no reported team enables `first_pass`.
- Tests: configurable subsets/ordering, `show_flow: false`, `metrics: []`, PO tile/chip/arrow/feedback/trend, dynamic footnote, byte-for-byte default `manager_` and `dm_` HTML, two consecutive `--offline` runs, full `run_report()` two-pass reconciliation, and CLI name-leak warning.

### Task 4: Docs, skill instructions, size cap & final verification
- **Files:** `docs/design-v3.md`, `AGENTS.md`, `skills/delivery-report/SKILL.md`, `tests/test_size.py`
- Update `docs/design-v3.md` (config keys, `PO satisfaction` section, manager card, "same board history" qualification, both code-cap references).
- Update `AGENTS.md` and `skills/delivery-report/SKILL.md` (`notes.md` 7 sections, step 3 PO rating prompt, manager card reference).
- Update size cap in `tests/test_size.py` if needed (keep `ledger/*.py` lean).
- Run full test suite (`python3 -m unittest discover -s tests`) and review `git diff`.
