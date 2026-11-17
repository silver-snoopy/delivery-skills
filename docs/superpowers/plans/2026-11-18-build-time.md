# Build time and a simpler manager card: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. The steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the manager_ card shows:
- throughput per 10 person-days, build time and pooled first-time pass, each with an arrow against the team's own past;
- a scope line;
- a "Where the time goes" line;
- a Late badge in Delivered items.

dm_ is unchanged.

**Architecture:**
- `ledger/metrics.py` (`_Team`) computes the per-item timings: build seconds, and the split into building, waiting and other.
- `team_sprint` aggregates them over the now window (last 3 sprints), the before window (the 3 sprints before) and the 6-sprint window.
- `ledger/render.py` changes only the manager_ card.

**Tech stack:** Python 3.9+ standard library; `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-17-build-time-design.md`. Read sections 3–6 before starting any task.

## Global constraints

- Use the Python 3.9+ standard library only. Run the tests with `python3 -m unittest discover -s tests`; all of them must pass at the end of every task.
- No real company, customer, team or person names and no real ticket data in code, tests or docs. Use invented names only (ALFA, Ada Vale, ada@vendor.example).
- Do not commit. The controller snapshots the tree after each task.
- Keep the `ledger/` line cap in `tests/test_size.py` (now 3300). Either task may raise it to 3400 if needed, and must say so in its report.
- dm_ output must not change. Cycle time, `cycle_window`, `waiting_share` and the signals keep their current behaviour.
- Exact copy for manager_ comes from spec section 4.

## Review focus

1. **A story whose only ours-assignment is on a child task.** The build start must use the child's assignment. Test in Task 1.
2. **An item created straight into a test state, or closed from Proposed.** It has no build start before the handoff, so it is not measured and does not crash. Test in Task 1.
3. **An item reopened after close, or returned from test and rebuilt.** Build time ends at the first handoff, and the rework goes to "other". Test in Task 1.
4. **A now window with zero available person-days** (everyone on leave). Throughput is None and shows "–", with no ZeroDivisionError. Test in Task 2.
5. **A team with 3 or fewer sprints of history.** The before window is empty, so there are no arrows and nothing breaks. Tests in Tasks 2 and 3.

---

### Task 1: Per-item build time and time split (high effort)

**Files:**
- Modify: `ledger/metrics.py` (`_Team`)
- Test: `tests/test_build_time.py` (new)

**Interfaces:**
- Produces `_Team.build_span(h: History) -> Optional[Tuple[datetime, datetime]]`:
  - it returns (build start, handoff) or None;
  - build start = the earliest revision time of `h` or of any child in `self.kids(h, t)` at which `h.at(t)["category"] in ACTIVE` and `self._ours_at(h, t)`;
  - handoff = the first revision time of `h` after the start whose state is in `cfg.test_states` and whose previous state is not, else `h.done_at`;
  - it returns None when `done_at` is None, when there is no start, or when start ≥ handoff.
- Produces `_Team.build_seconds(h) -> Optional[float]`: the span length minus the seconds within the span where `is_waiting(row, cfg.waiting_states)` or `row["state"] in cfg.hold_states`. "Blocked" is already inside `is_waiting`.
- Produces `_Team.time_split(h) -> Optional[Tuple[float, float, float]]`, returning (building, waiting, other) seconds over first_active → done_at:
  - it returns None when `cycle_days(h)` is None;
  - building = `build_seconds(h) or 0`;
  - waiting = `h.waiting_seconds(first_active, done_at, cfg.waiting_states)`;
  - other = max(0, total − building − waiting).
- Produces `_Team.waiting_states_seen(h) -> List[str]`: the states in `cfg.waiting_states` that `h` was in between first_active and done_at.
- Add a general interval helper to `History` if it keeps `metrics.py` short, for example `seconds_where(start, end, pred)`. `waiting_seconds` may delegate to it, but its behaviour must not change.

- [ ] **Step 1: Write the failing tests** in `tests/test_build_time.py`. Use `tests.fixture.Board`, `tests.test_metrics.config` with `test_states=["Ready for QA"]`, `waiting_states=["Ready for QA", "PO Review"]` and `hold_states=["On Hold"]`, and the invented engineer `ada@vendor.example`. Times are local and whole days, so the expected values are exact:
  - `test_assignment_while_proposed_does_not_start_the_clock`: assigned to Ada on 11-02 in New, Active on 11-04, Ready for QA on 11-06 09:00, Closed 11-09 → `build_seconds == 2 * 86400`.
  - `test_active_before_ours_is_not_build_time`: Active on 11-02 assigned to `po@customer.example`, assigned to Ada on 11-03 09:00, Ready for QA on 11-05 09:00 → `2 * 86400`.
  - `test_story_starts_when_child_task_is_ours`: story Active on 11-02 unassigned; child task assigned to Ada created 11-03 09:00; story to Ready for QA on 11-04 09:00 → `1 * 86400`.
  - `test_handoff_falls_back_to_done_without_test_states`: `test_states=[]`; Active 11-02, Closed 11-05 → `3 * 86400`.
  - `test_hold_and_blocked_inside_span_are_excluded`: Active 11-02, On Hold 11-03 → Active 11-04, `tags=["Blocked"]` 11-05 → no tags 11-06, Ready for QA 11-07 → `3 * 86400`.
  - `test_rework_after_return_is_not_build_time`: Active 11-02, Ready for QA 11-03, Active 11-04, Ready for QA 11-06, Closed 11-07 → `build_seconds == 86400`. `time_split == (86400, 2 * 86400, 2 * 86400)`, that is building 1 day, waiting 2 days (two Ready for QA stretches, 11-03 → 11-04 and 11-06 → 11-07), other 2 days. Assert the sum equals `(done_at - first_active).total_seconds()`.
  - `test_not_measured_without_build_start`: created in Ready for QA and assigned to Ada, then Closed → `build_span is None`, `build_seconds is None`. Also: New → Closed → `build_span is None`.
  - `test_waiting_states_seen_in_config_order`: an item visits PO Review, then Ready for QA → `["Ready for QA", "PO Review"]`.

- [ ] **Step 2: Run** `python3 -m unittest tests.test_build_time -v`. Expected: FAIL with AttributeError on `build_span`.
- [ ] **Step 3: Implement** the four `_Team` methods, plus the optional `History.seconds_where`.
- [ ] **Step 4: Run** `python3 -m unittest discover -s tests`. Expected: all tests pass.
- [ ] **Step 5:** Do not commit. Write the report.

### Task 2: Window figures in `team_sprint` (high effort)

**Files:**
- Modify: `ledger/metrics.py` (`team_sprint`)
- Test: `tests/test_build_time.py`, `tests/test_first_pass.py` (update for pooling)

**Interfaces:**
- Consumes the Task 1 methods.
- Produces these new keys in the `team_sprint` result:
  - `"windows": {"now": [sprint names], "before": [sprint names]}`. Take `window = upto[-TREND:]` (existing). `now = window[-3:]`, and `before = window[-6:-3]`, which can be empty. Add a module constant `NOW = 3`.
  - `"build": {"median": Optional[float], "n": int, "before": Optional[float]}`: the median of `build_seconds / 86400` over the measured items delivered in each window. Do not round.
  - `"throughput": {"per10": Optional[float], "before": Optional[float]}`: delivered count ÷ the summed `available` of `t.members(people, team, sprints, s, days_off)` per sprint, × 10. It is None when the available sum is 0 or when the window is empty. Do not round.
  - `"flow": Optional[dict]` with `{"total": int, "building": int, "waiting": int, "other": int, "states": [str]}` over the 6-sprint `window_ids`:
    - total = `cycle_window["median"]`;
    - each part = round(total × pooled share), with building and waiting first, and other = total − building − waiting. If that is negative, take the deficit from waiting;
    - states = the configured order of the states seen;
    - None when no item has a `time_split` or the total is None.
  - `"first_pass"`: pooled over the now window. `scored` = the sum, `returned` = the sorted union of returned ids, `share` as before, `tracked` unchanged (all 6 sprints), and a new `"before"` = the pooled share over the before window, or None.
  - Each `trend` entry gains `"build"` (that sprint's median, or None) and `"per10"` (that sprint's value, or None). `"first_pass"` stays per sprint. Remove `"p85"` from trend entries only if nothing else reads it: grep `ledger/` first.
  - Each `delivered_items` entry gains `"late": i in f["late"]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_build_time.py`, using a 7-sprint board with `Board().sprint(...)` per sprint, and the `team_sprint` call pattern from `tests/test_first_pass.py`:
  - `test_windows_split_last_six_sprints`: names now = the last 3 sprints, before = the 3 before them, and the oldest sprint is in neither.
  - `test_build_median_pooled_over_now_window`: items with 1, 2 and 4 build days across the 3 now sprints → `median == 2.0`, `n == 3`. Before-window items with 5 and 7 → `before == 6.0`.
  - `test_throughput_per_ten_person_days`: 6 delivered in now, 2 engineers × 10 working days × 3 sprints with no leave = 60 PD → `per10 == 1.0`.
  - `test_throughput_none_with_zero_capacity`: every working day of the now window is off for both engineers → `per10 is None`.
  - `test_no_before_window_with_three_sprints`: a 3-sprint board → `windows["before"] == []`, and `build["before"]`, `throughput["before"]` and `first_pass["before"]` are all None.
  - `test_flow_parts_add_up`: assert `building + waiting + other == total`, all parts ≥ 0, and `states` in config order.
  - `test_delivered_items_mark_late`: a planned item closed after the sprint end → `late is True`; the others are False.
- [ ] **Step 2: Update `tests/test_first_pass.py`** where it asserts a single sprint's figure but the now window now pools earlier sprints. Add `test_first_pass_pools_now_window_not_median`: sprint A has 1 scored and 1 returned, sprint B has 9 scored and 0 returned → pooled share `0.9`, where a median of the sprint shares would give 0.5 or 1.0.
- [ ] **Step 3: Run** `python3 -m unittest tests.test_build_time tests.test_first_pass -v`. Expected: FAIL.
- [ ] **Step 4: Implement** in `team_sprint`. Compute members per window sprint once and reuse them.
- [ ] **Step 5: Run** the full suite. Expected: all tests pass. dm_ render tests must pass without edits.
- [ ] **Step 6:** Do not commit. Write the report.

### Task 3: Manager card, arrows and docs (medium effort)

**Files:**
- Modify: `ledger/render.py` (manager_ only: `_figs`, `_first_pass`, `_delivered`, `_trend_table`, `_exec_team`, `_footnote(dm=False)`, CSS)
- Modify: `AGENTS.md`, `README.md`, `skills/delivery-report/SKILL.md`, `docs/design-v3.md` wherever they describe manager_ figures
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes the Task 2 keys.
- Formatting:
  - build time `"%.1f <small>days</small>"`;
  - per10 `"%.1f"`;
  - first-time pass `_pct(share, 99 if returned else 100)`;
  - missing values are `–`.
- Produces `_arrow(now_text: str, before_text: Optional[str], better: bool, sprints: List[str]) -> str`:
  - it returns `<span class="d up|down">▲|▼ from BEFORE vs sprints A–C</span>`, or `""` when `before_text` is None or equals `now_text`;
  - `better` decides the colour: `up` = green, `down` = red;
  - the triangle follows the number's direction, comparing the parsed floats;
  - "A–C" is the first and last before-sprint names; with one sprint, show only that name.
- CSS: `.d{font-size:12px;font-weight:600;margin-top:2px}.d.up{color:var(--good)}.d.down{color:var(--bad)}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_render.py`, building a team dict from the existing `teams()` helper with the Task 2 keys added:
  - `test_manager_figures_order_and_labels`: in order, "planned work items delivered", "work items delivered in total", "work items per 10 person-days (last 3 sprints)", "build time, median (last 3 sprints)", "first-time pass (last 3 sprints, 1 of 25 returned)".
  - `test_manager_drops_old_figures`: none of "cycle time median", "of cycle time spent in waiting states", "completed after the sprint ended", or a figure labelled "work items carried over" / "added / removed".
  - `test_scope_line`: "Scope: +2 added / −1 removed mid-sprint · 1 work item carried over", and "Scope: no changes mid-sprint · nothing carried over". The plural is "N work items carried over".
  - `test_arrows`: lower build time → `class="d up"` with "▼ from 5.5 days vs sprints S37–S39"; a throughput drop → `class="d down"` with "▼"; equal formatted values → no `class="d`; `before` None → no arrow; "not tracked" → no arrow.
  - `test_where_the_time_goes`: "12 days typical from start to done = 4 days building · 6 days waiting (Ready for QA, PO Review) · 2 days other". The section is absent when `flow` is None. Without states the text reads "6 days waiting".
  - `test_delivered_items_late_badge`: the summary "Delivered items (9, 2 late)" and `<span class="chip amber">Late</span>` twice. With no late items: "Delivered items (3)" and no extra `<th>`.
  - `test_trend_rows`: "Work items per 10 PD" and "Build time median (days)" rows are present (None → "–"), and "Cycle time P85" is absent.
  - `test_dm_unchanged_by_build_time`: a render_dm page for the same teams contains none of "build time", "per 10 person-days" or "Where the time goes".
- [ ] **Step 2: Run** `python3 -m unittest tests.test_render -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the manager rendering:
  - add the scope line as `<p class="note">` under the figs;
  - add the flow block with the three-part bar (inline flex spans: `var(--accent)`, `var(--warn)`, `#B8C0CA`);
  - add the manager footnote definitions from the spec;
  - delete code that is now unused (`_cycle`, if nothing else calls it).
  - Update the existing render tests that asserted the removed figures.
- [ ] **Step 4: Update the docs:** every place that lists the manager_ figures or the cycle time box now describes the new card, matching spec section 4.
- [ ] **Step 5: Run** the full suite. Expected: all tests pass.
- [ ] **Step 6:** Do not commit. Write the report.
