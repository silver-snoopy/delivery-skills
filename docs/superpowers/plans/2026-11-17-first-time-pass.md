# First-time Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a team-level "first-time pass" figure (share of delivered work items never sent back from testing and never reopened) to the Delivery stats and trend table.

**Architecture:** `History.returned()` in `ledger/replay.py` detects returns from revision history. `team_sprint()` in `ledger/metrics.py` scores the delivered items of each sprint in the trend window and decides "tracked". `ledger/render.py` shows the figure, the trend row, the footnote sentence and a config warning. `ledger/setup.py` suggests `test_states` during `init`.

**Tech Stack:** Python 3.9+, standard library only; tests with `python3 -m unittest discover -s tests`.

**Spec:** `docs/superpowers/specs/2026-11-17-first-time-pass-design.md`

## Global Constraints

- Python 3.9+, standard library only.
- No names, customer data or real ticket data in the repo, including tests. Use invented names and emails (`@vendor.example`, `@customer.example`) and small invented IDs.
- Team level only: no per-person first-time pass figure anywhere.
- Code cap: `tests/test_size.py` limit goes from 3,200 to 3,300 lines under `ledger/`.
- Do not commit. Leave all changes in the working tree; the controller commits.

## Review Focus

1. An item delivered with a not-delivered reason or Removed is not scored (it is not in `delivered`). Task 1 test `test_cut_item_not_scored`.
2. A revision that stays in a test state (for example an assignee change while in Ready for QA) is not a return. Task 1 test `test_change_inside_test_state_is_not_a_return`.
3. An item reopened and re-closed in a later sprint keeps its credit in the first sprint and counts as returned there. Task 1 test `test_reopen_after_sprint_counts_against_credit_sprint`.
4. An old `config.json` without `test_states` loads with `test_states == []`. Task 1 test `test_config_without_test_states_loads`.
5. The figure shows on the dm_ page too (shared figure row), still team-level only. Task 2 test `test_dm_page_shows_first_pass`.

---

### Task 1: Return detection, scoring, config and init suggestion

Effort: high (subtle state rules).

**Files:**
- Modify: `ledger/config.py` (Config field)
- Modify: `ledger/replay.py` (History method)
- Modify: `ledger/metrics.py` (`team_sprint` output)
- Modify: `ledger/setup.py` (`suggest`, init defaulting, init print)
- Test: `tests/test_first_pass.py` (new), `tests/test_setup.py`

**Interfaces:**
- Produces:
  - `Config.test_states: List[str]`, default `[]`, placed after `hold_states`.
  - `History.returned(self, test_states: Sequence[str]) -> bool`: True when any consecutive revision pair (prev, new) is either
    - a test return: `prev["state"] in test_states and new["state"] not in test_states and new["category"] not in ("Completed", "Removed")`; or
    - a reopen: `prev["category"] == "Completed" and new["category"] in ("Proposed", "InProgress", "Resolved")`.
    Uses the whole history. Tags are never read.
  - `team_sprint(...)` output gains `"first_pass": {"scored": int, "returned": List[int], "share": Optional[float], "tracked": bool}` where `scored = len(f["delivered"])`, `returned` = sorted delivered IDs with `returned(cfg.test_states)`, `share = (scored - len(returned)) / scored` or `None` when `scored == 0`, `tracked` = at least one returned item among the delivered items of the trend window sprints (`window`, the same one the trend uses).
  - Each `trend` entry gains `"first_pass": Optional[float]`, the share for that sprint (`None` when nothing delivered).
  - `setup.suggest(...)` result gains `"test": List[str]`: state names in category InProgress or Resolved whose lowercased name contains one of `TEST_WORDS = ("qa", "test", "review", "verify", "uat", "accept")`.
  - `init`: `if not cfg.test_states: cfg.test_states = sug["test"]`, and prints `Proposed test states: A, B` (or `none`) on the line after `Proposed hold states: ...`.

- [ ] **Step 1: Write the failing tests** in `tests/test_first_pass.py`, reusing `board`, `run`, `config`, `MAYA`, `S28`, `S29` from `tests.test_metrics`. Use `cfg = config(); cfg.test_states = ["Ready for QA", "In Review"]` unless the test says otherwise. Each item is planned in S29 for MAYA, made Active on `2026-10-27 09:00`, and closed in S29 unless stated.
  - `test_return_from_test_state_counts`: Active → Ready for QA → Active → Closed. Assert `t["first_pass"] == {"scored": 1, "returned": [ID], "share": 0.0, "tracked": True}`.
  - `test_forward_and_between_test_states_do_not_count`: one item Ready for QA → In Review → Closed. Assert `returned == []`, `share == 1.0`.
  - `test_return_to_new_counts`: Ready for QA → New → Active → Closed is returned.
  - `test_test_state_to_removed_is_not_a_return`: call `History.returned` directly on a history Ready for QA → Removed; assert False.
  - `test_change_inside_test_state_is_not_a_return`: while in Ready for QA, change only `assigned`; then Closed. Not returned.
  - `test_blocked_tag_never_counts`: Active with `tags=["Blocked"]`, then `tags=[]`, then Closed. Not returned.
  - `test_reopen_after_sprint_counts_against_credit_sprint`: closed in S29, reopened (`state="Active"`) on 2026-11-12 with iteration moved to a later sprint, closed again 2026-11-14. Run for S29: the item is in `delivered` and in `first_pass["returned"]`.
  - `test_item_counted_once`: two bounces on one item, plus one clean item. Assert `returned == [ID]`, `share == 0.5`.
  - `test_not_tracked_without_returns_in_window`: two clean delivered items in S28 and S29. Assert `tracked is False` and `share == 1.0`.
  - `test_tracked_by_return_in_earlier_window_sprint`: return only in S28, clean in S29. Run for S29: `tracked is True`, `share == 1.0`, and `[e["first_pass"] for e in t["trend"]] == [0.0, 1.0]`.
  - `test_nothing_delivered_share_none`: S29 has only an open item. `scored == 0`, `share is None`.
  - `test_empty_test_states_counts_reopens_only`: `cfg.test_states = []`. A QA → Active bounce is not returned; a reopen is returned.
  - `test_cut_item_not_scored`: closed with a reason from `cfg.not_delivered_reasons`. `scored == 0`.
  - `test_config_without_test_states_loads`: write a `config.json` without the key to a temp dir; `load_config(dir).test_states == []`.
  - In `tests/test_setup.py`: `test_suggest_test_states` asserts `"Ready for QA"` and `"In Review"` are in `suggest(...)["test"]` and `"Active"` and `"On Hold"` are not; extend the existing print-order test to assert `text.index("Proposed hold states") < text.index("Proposed test states")`.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_first_pass tests.test_setup -v`
Expected: failures on the missing `first_pass` key, `test_states` field and `"test"` suggestion.

- [ ] **Step 3: Implement** the Interfaces above in `ledger/config.py`, `ledger/replay.py`, `ledger/metrics.py`, `ledger/setup.py`. In `team_sprint`, compute one per-sprint helper over `figs` (already built for every sprint in `upto`) so the current sprint and the trend share the same code.

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests`
Expected: all pass. If `tests/test_size.py` fails on the cap, raise its limit to 3,300 here (Task 2 owns the cap otherwise).

---

### Task 2: Show the figure, trend row, footnote, warning and docs

Effort: medium.

**Files:**
- Modify: `ledger/render.py` (`_figs`, `_trend_table`, `_footnote`, report warning)
- Modify: `tests/test_size.py` (cap 3,300)
- Modify: `docs/design-v3.md`, `AGENTS.md`, `skills/delivery-report/SKILL.md`, `README.md` (only where it lists the figures)
- Test: `tests/test_render.py`, `tests/test_docs.py` if it checks doc phrases

**Interfaces:**
- Consumes: `t["first_pass"]` and `trend[i]["first_pass"]` from Task 1; `Config.test_states`.
- Produces: no new interfaces.

Exact copy (from the spec):
- Figure, tracked: value `92%` (whole percent, `round(share * 100)`), label `first-time pass (2 returned)`.
- Figure, not tracked: value `not tracked`, label `first-time pass: no item moved back on the board in the last 6 sprints` (use `TREND` for the 6).
- Figure, nothing delivered: value `–`, label `first-time pass`. When `scored == 0`, show this even if the team is not tracked.
- Trend row label `First-time pass`; cells: percent, `–` for `None`, `not tracked` when `t["first_pass"]["tracked"]` is False.
- Footnote sentence on both pages, appended to the Method paragraph: `First-time pass = share of delivered work items never sent back from a test state and never reopened, counted per team; “not tracked” means no item moved back in the last 6 sprints.` (use `TREND` for the 6).
- Warning, printed once per `report` run when `cfg.test_states` is empty: `warning: no test states in config.json; first-time pass counts reopens only`.

- [ ] **Step 1: Write the failing tests** in `tests/test_render.py`, using the existing render helpers:
  - `test_first_pass_figure_tracked`: a team dict with `first_pass = {"scored": 25, "returned": [1, 2], "share": 0.92, "tracked": True}` renders `92%` and `first-time pass (2 returned)` in the manager_ HTML.
  - `test_first_pass_not_tracked`: `tracked False`, `scored 3` renders `not tracked` and the not-tracked label; does not contain `100%` in the figure.
  - `test_first_pass_nothing_delivered`: `scored 0`, `share None` renders `–` with `first-time pass`.
  - `test_trend_row_first_pass`: trend shares `[0.5, None, 1.0]`, tracked, render cells `50%`, `–`, `100%` under `First-time pass`.
  - `test_dm_page_shows_first_pass`: the dm_ HTML for the same team contains the figure; no person row or person table contains `first-time pass`.
  - `test_footnote_defines_first_pass`: both pages contain `First-time pass = share of delivered work items`.
  - `test_warning_without_test_states`: a `report --offline` run with a config lacking `test_states` prints the exact warning line once; with `test_states` set it does not.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_render -v`
Expected: the new tests fail.

- [ ] **Step 3: Implement** in `ledger/render.py`: a small `_first_pass(t) -> str` figure added to `_figs` after the waiting-share figure; the trend row in `_trend_table`; the footnote sentence; the warning via the existing `_warn` path in the report orchestration. Raise the cap in `tests/test_size.py` to 3300.

- [ ] **Step 4: Update docs.** `docs/design-v3.md`: add the first-time pass definition, `test_states` config key and the not-tracked rule next to the other figures. `AGENTS.md` and `skills/delivery-report/SKILL.md` flow step 1: also show the proposed test states and ask the DM to confirm them. `README.md`: mention the figure only if it lists the Delivery stats.

- [ ] **Step 5: Run all tests**

Run: `python3 -m unittest discover -s tests`
Expected: all pass; `ledger/` under 3,300 lines.
