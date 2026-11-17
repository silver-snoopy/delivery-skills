# DM Action View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the DM a current-sprint action table whose recorded answers survive reruns and reappear when problems worsen, while keeping manager reports confined to closed-sprint results.

**Architecture:** Keep replay and closed-sprint calculations intact. Add structured action rows in `metrics.py`, Markdown parsing and reconciliation in `config.py`, and report orchestration and rendering in `render.py`. Calculate raw signals before reconciling answers, then apply agreement to closed-sprint results and presentation state to actions.

**Tech Stack:** Python 3.9+ standard library, unittest, existing HTML renderer.

**Spec:** `docs/superpowers/specs/2026-11-16-dm-action-view-design.md`

## Global Constraints

- Python 3.9+ standard library only.
- No change to the manager_ report's content or layout (it reads summary and exec note from `notes.md` instead of `notes.txt`). Management sees only results of closed sprints.
- No forecasting, no burndown chart, no RAG status for a running sprint.
- No support for the old `notes.txt` format (clean switch).
- The `person` command is unchanged.
- The tool never changes the DM's Answer text or text-section content.
- Tests use invented fixtures only. Never copy local simulation data into this repository.
- Light theme for everything shown to the user; preserve the person command's content and calculations.
- Never open generated HTML files or send or post a report. Renderer tests inspect returned strings.
- Never supply DM answers or confirm summary text on the DM's behalf.
- Do not commit or push: the handoff explicitly reserves these actions for a separate user request. Capture per-task working-tree diffs for review instead of commit ranges.
- Exact implementation cap: 3,200 physical lines under `ledger/`, enforced by `tests/test_size.py`. This is a modest allowance over the design's approximate 2,900-line target for lossless Markdown reconciliation; do not compress code to evade it.

## Review Focus

- An edited answer must reset its baseline, while editing a tool-owned cell must not reset it (Task 2).
- Markdown pipes, backslashes, unknown sections and malformed table rows must survive repeated writes without duplication or loss (Task 2).
- Different team calendars and a missing closed sprint must not drop a running team's actions or another team's saved notes (Tasks 2 and 3).
- Revisions after local midnight, including later closure or reassignment, must not leak into the running snapshot (Task 1).
- Answered carry-over rows must not lose agreement merely because agreement removes their actionable signal; copied answers must not grant agreement in a new closed sprint (Tasks 2 and 3).

## Decisions for implementation

- Execute in an isolated worktree branched from `dm-action-view`; leave the existing checkout intact. No commits or pushes.
- Reject `--sprint` naming a sprint that has not ended before `--today`, using the CLI's existing error handling. This settles the conflict between the old explicit-sprint preview and the new closed-only manager requirement. A named past sprint suppresses the running view.
- Clamp the planned snapshot to the as-of instant on the first day: `min(end_of_first_day, now)`. This avoids reading the future before the first day has finished.
- Store answer provenance in a tool-owned JSON sidecar beside `notes.md`, named `.notes-state.json`. The visible Answered cell remains the documented date, level and complete signal-kind set. The sidecar stores the exact answer last observed, its baseline, and sprint identity so edits to Answered can be repaired and a changed Answer can be detected. A first run without state imports a valid Answered baseline conservatively; invalid metadata becomes an unanswered baseline with a warning, preserving the Answer text.
- Track both the closed sprint path (agreement scope) and the effective action sprint path (re-escalation scope). An item still open in a newly started running sprint becomes worse even if the closed report folder has not changed.
- An unchanged copied answer cannot renew agreement. The DM changes or clears and re-enters the Answer to renew it; document this behavior.
- Choose predecessor notes per team by the latest known closed-sprint end strictly no later than that team's selected closed sprint; break ties by path for determinism. Never import future answers into a historical report. Warn and skip markers whose sprint path cannot be resolved.
- Preserve unknown team sections and unparseable content in the file, warn, and exclude unknown teams from calculations. Missing-sprint teams retain notes but do not produce closed-sprint figures.
- Write notes and state through temporary files and replace only after successful serialization. State includes the observed answer, allowing recovery after an interrupted pair of replacements; do not erase unmatched prior state until reconciliation succeeds.

### Task 1: Structured running and closed action signals

**Files:** Modify `ledger/metrics.py`; add `tests/test_actions.py`; extend `tests/test_flags.py`; update `tests/test_size.py` cap.

**Interfaces:**
- Add `team_actions(data: dict, cfg: Config, people: List[Person], team: str, closed: Optional[dict], running: Optional[dict], today: date, days_off: dict) -> dict`.
- `closed` is the existing `team_sprint` result or `None`; `running` is an iteration dictionary or `None`.
- Return `{"people": list, "rows": list, "current": dict-or-None}`. People retain existing capacity/onboarding fields. Rows have `id` (integer or None), `person`, `title`, `sprint` (display), `sprint_path`, `level`, `badge`, `kinds` (complete sorted list), `problem`, `ask`, `age`, `open`, and `carry` (boolean). Item identity is its id within a team; person identity is name plus signal kind.
- `current` holds `sprint`, `day`, `days`, `planned`, `delivered_planned`, `added`, `removed`, `held`, `waiting`; item collections use ids, counts are rendered by the program.
- Add structured `id`, `person`, `kind`, and `badge` fields to `_attention` entries without changing existing text/ask consumers. Reuse `ASK` and existing wording, without parsing English messages to infer identity.

- [ ] Write failing unittest cases for each signal from spec sections 6–7. Assert no running carry-over and no running RAG status. Test WAIT_FLAG_DAYS=3 and WAIT_RED_DAYS=10 with equality and just-over boundaries, STALE_DAYS=5 with equality, WIP_FLAG=3, and NOT_STARTED_DAY=3 (Not started only after that working day).
- [ ] Test midnight snapshot isolation, first-day and weekend handling, proposed items suppressing No work, leave/onboarding excluding No work, and children contributing activity at stories level. Test all owner fallback rules, including reassignment and history-only ownership.
- [ ] Implement `team_actions`, `NOT_STARTED_DAY = 3`, and time-bounded running calculations. Do not call `team_sprint` with a fake sprint end. Restrict history used for running done/active calculations to the as-of instant; use closed-window P85.
- [ ] Test closed-only badge mapping and merged Now/carry-over rows: one row per item, joined problems, all kinds retained, maximum severity. Test fallback members with no rows and stable severity/id ordering.
- [ ] Run `python3 -m unittest tests.test_actions tests.test_flags tests.test_metrics tests.test_replay`; expect all pass. Run the full suite once and record output; do not commit.

### Task 2: Lossless Markdown notes and answer lifecycle

**Files:** Modify `ledger/config.py`; replace legacy parser cases in `tests/test_config.py`; add `tests/test_notes.py`.

**Interfaces:**
- Preserve existing `Notes.summary`, `note`, `exec_note`, `status`, `items`, `warnings` consumers. Add structured per-team sections, action/resolved rows, marker identities and raw preserved content as needed.
- Replace `parse_notes(text: str) -> Notes` with the Markdown-only parser.
- Add `load_report_notes(directory: str, folder: str, selected: dict, iterations: list, warn: Callable) -> Notes`, including predecessor selection and sidecar loading. `selected` maps team names to closed iteration dictionaries or None.
- Add `reconcile_notes(notes: Notes, actions: dict, selected: dict, today: date) -> dict`, where `actions` maps team names to Task 1 results. Return the same mapping with presentation fields `answer`, `answered`, `handled`, `worse` added to rows, update notes Actions/Resolved, and populate agreement only for the selected closed sprint.
- Add `write_report_notes(folder: str, notes: Notes) -> None` for notes and sidecar persistence.
- Add `notes_for_team(notes: Notes, team: str) -> Notes` to isolate item-agreement scope when an id appears for more than one team.

- [ ] Write failing parser/serializer tests for BOM, fixed section order, optional summary bullet prefixes, multiline sections, blank Status, valid/invalid status, escaped pipes/backslashes, empty answers, malformed table rows, unknown headings, and unknown teams. Verify a second round trip preserves every user-owned value and raw line without multiplying content.
- [ ] Implement Markdown parsing and rendering in `config.py`; remove legacy syntax acceptance. Preserve original section text separately from normalized values consumed by rendering. Maintain warning strings for malformed content rather than ignoring it.
- [ ] Write failing lifecycle tests: first answer -> Handled; same answer -> baseline retained; changed answer -> fresh baseline; cleared answer -> no Handled; edited Answered -> repaired. Assert amber->red, newly added kind, and newly started sprint each set worse. A pre-existing secondary kind must not count as new. Age alone must not escalate.
- [ ] Implement reconciliation and sidecar persistence. Keep answered disappearing rows under Resolved; restore them when their signal returns. Do not lose answers if a row becomes OK/grey. Do not let agreement-driven signal removal erase the agreement used by closed-sprint status.
- [ ] Test per-team carry-forward: unchanged closed sprint copies all sections and state; changed sprint copies only answers for still-open rows, with unknown agreement and worse state. Test out-of-order historical reports, mixed team calendars, malformed/missing state, and no predecessor.
- [ ] Test item reassignment preserves its answer, person row keys distinguish badges, and duplicate ids in separate teams do not leak agreement. Test `agreed:`, `not agreed:`, `unknown:` and unprefixed carry-over answers.
- [ ] Run `python3 -m unittest tests.test_config tests.test_notes`; expect all pass. Record full-suite transitional failures caused solely by old report callers/fixtures for Task 3; do not restore backward compatibility or commit.

### Task 3: Integrate report generation and the DM action table

**Files:** Modify `ledger/render.py`; extend `tests/test_render.py` and `tests/test_cli.py`; update Markdown fixtures in other affected tests.

**Interfaces:**
- Use Task 2 loading/reconciliation, Task 1 `team_actions`, and team-scoped notes for `team_sprint`.
- Attach each team's action result under `actions`; keep existing closed-sprint result keys intact.
- Reconcile before the final closed-sprint metrics calculation, then refresh carry-related presentation against resulting agreement without rebasing answers. Keep source signals and presentation state separate to avoid a rerun loop.

- [ ] Write failing orchestration tests: default reports contain closed manager results and a separate running DM view; named past sprint has no Now rows; named running/future sprint fails; missing closed sprint for one team does not hide that team's running view when another team supplies the report folder.
- [ ] Implement selection and notes lifecycle in `run_report`, preserving existing folder/file naming. Read/write only `notes.md`; ignore any old `notes.txt`. Feed team-scoped agreement to closed metrics. Preserve all existing console lines and add `TEAM action: PERSON #ID BADGE - PROBLEM` for open unanswered red/amber rows; omit id on person rows.
- [ ] Write renderer tests asserting the exact marker `Internal: delivery manager and team leads only. Not for the customer.`, column order Person/Work item/Sprint/Signal/Problem/Action/Age, person rowspans, capacity/onboarding, current line, and per-team comparison before actions. Assert old Needs attention and People sections are absent.
- [ ] Render action tables and ordering: people by worst unhandled level then name; rows by severity, then Handled, then OK, ties by id. Use grey Handled badge only, or original badge plus ` ↑` and tooltip `worse since your answer`. Answer replaces suggested action. Escape every user/board string and preserve linked item ids.
- [ ] Keep closed Team figures and manager card layout intact. Extend DM footnote with running and badge rules. Make DM and person pages light without changing person behavior.
- [ ] Add tests for manager output equivalence for equivalent old/new note content using the existing renderer's expected layout, name warnings, all parser warnings reaching CLI, unanswered console filtering, and same-day rerun stability. Test dangerous HTML in titles/answers by inspecting returned strings, never opening generated HTML.
- [ ] Run `python3 -m unittest discover -s tests`; expect all pass after updating affected fixtures. Record exact output and changed files; do not commit.

### Task 4: Update operator instructions and verify the complete workflow

**Files:** Modify `AGENTS.md`, `skills/delivery-report/SKILL.md`, `README.md`, `docs/design-v3.md`, `tests/test_docs.py`; extend `tests/test_cli.py` as necessary.

**Interfaces:** Document the implemented Markdown sections, action console lines, current-view boundaries, answer baseline lifecycle, and sidecar portability; preserve existing init/person procedures.

- [ ] Update doc assertions first for `notes.md`, `## Summary`, `## Actions`, Answer cells and the action-question loop. Keep privacy, warnings, no HTML opening, no invented figures/answers, and no report sending rules.
- [ ] Replace the carry-over-only questioning loop with asking about printed actions. Retain the PO agreement question for carry-over actions, per-team optional notes, executive-note explicit authorization, and DM confirmation of summary bullets. Names remain prohibited in Summary, Exec note and Status.
- [ ] Document notes and `.notes-state.json` travelling together, edited answers resetting their baseline, copied answers needing renewal for a new closed sprint, and the clean notes.txt break. Update current design documentation and line cap while leaving historical plans/findings as historical records.
- [ ] Add an offline invented-fixture integration test covering report -> DM edit -> rerun -> Handled -> new signal/sprint -> worse -> resolved -> return. Assert no answer/text loss, unchanged closed-sprint metric outputs, and no running data or person cells on manager output. Use temporary directories only.
- [ ] Run `python3 -m unittest discover -s tests` and `git diff --check`. Review changed text for private data. Record results, do not commit or push.

## Execution and review

Before implementation, create the plan-specific SDD workspace and ledger, recording the no-commit adaptation and each decision above. Record base HEAD and capture the working tree before each task; produce task-scoped diff packages from before/after snapshots, including newly created files. Do not stage files merely to generate a diff. Dispatch one fresh implementer per task and a separate reviewer for spec compliance and quality. Apply the skill's fix loop to blocking findings. Run its whole-branch review after all tasks, then leave the reviewed changes uncommitted in the implementation worktree and report its path and verification results.
