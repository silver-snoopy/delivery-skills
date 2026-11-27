# PO satisfaction: design

Status: implemented (2026-11-27).

## 1. Intent

The manager card shows objective delivery figures from Azure DevOps (planned delivered, total delivered, throughput, build time, first-time pass), but none of them capture how the customer's product owner (PO) actually feels about the sprint. A team can hit 90% of planned items and still leave the PO frustrated about communication or priorities — or carry over work with full PO trust.

Adding **PO satisfaction** gives executives a lightweight, human-reported customer pulse right alongside the delivery stats, on a simple **1–5 scale** with anchored labels so the number is immediately self-explanatory.

## 2. Goals, non-goals, anti-goals

- **Goals:**
  - A 1–5 PO satisfaction score per team per closed sprint, recorded by the DM in `notes.md` (with an optional short comment).
  - Anchored satisfaction labels and colour bands so executives know what each score means without reading a footnote.
  - A **PO satisfaction** tile in the `manager_` Delivery stats row, with a delta arrow against the team's most recent prior rated sprint.
  - A **PO satisfaction** row in the 6-sprint trend table, sourced from existing `notes.md` files in past report folders (no new database or sidecar file).
  - Opt-in via `po_satisfaction` in `config.json` (`manager_metrics` / `teams.<TEAM>.metrics`), keeping default-config reports byte-for-byte unchanged.
- **Non-goals:**
  - Automated PO surveys, forms, or external integrations.
  - Changing `dm_.html` layout, styles, or the DM's computed RAG status rule.
  - Any per-person PO satisfaction rating.
  - A separate standalone visibility mode (both specs ship together so configured membership is the single source of truth).
- **Anti-goals:**
  - A bare number (`4/5`) with no meaning attached.
  - Silently carrying or re-tagging a previous sprint's PO rating onto a new sprint.
  - Comparing PO satisfaction across teams (a team is compared only with its own past).
  - Engineer names leaking into `manager_` via PO comments.

## 3. Definitions

### Scale and anchored bands **(builder's call on exact wording)**

| Score | Label | Colour |
|---|---|---|
| `5` | Very satisfied | Green (`green`) |
| `4` | Satisfied | Green (`green`) |
| `3` | Mixed | Amber (`amber`) |
| `2` | Dissatisfied | Red (`red`) |
| `1` | Very dissatisfied | Red (`red`) |

All five anchors describe satisfaction (no RAG/verdict wording such as "At risk"). 4–5 map to green, 3 to amber, and 1–2 to red. Half-points (e.g. `3.5`) are not supported; scores are whole integers 1–5. Score-to-label/colour mapping lives in `render.py` only.

### Input in `notes.md`

Each team gets a new optional section `## PO satisfaction` in `notes.md`, placed right after `## Exec note` (grouping the three manager-facing sections at the top: `Summary`, `Exec note`, `PO satisfaction`, followed by DM-only `Note`, `Status`, `Actions`, `Resolved`). The section is always written for every team even when `po_satisfaction` is disabled in `config.json`, so toggling the metric off and back on never loses notes.

Tool-owned guide line:
```markdown
## PO satisfaction
<!-- guide: Manager report. Optional, no names. 1-5 scale (5 Very satisfied, 4 Satisfied, 3 Mixed, 2 Dissatisfied, 1 Very dissatisfied). Example: "4/5 - Smooth release, minor UAT delay" -->
```

Accepted non-empty values (whitespace stripped; single line):
- `<1-5>` — e.g. `4`
- `<1-5>/5` — e.g. `4/5` (optional spaces around `/`)
- `<1-5> - <comment>` or `<1-5>/5 - <comment>` — score plus optional 1-line comment (dash `-`, en-dash `–`, or em-dash `—` separator).

Blank means not collected for this sprint.

Invalid input (out-of-range numbers like `0` or `6`, decimals like `4.5`, free text without a leading 1–5 score, or multiple non-empty lines):
- Ignored for rendering (treated as unrated `None` for that sprint).
- Preserved verbatim in `notes.md`.
- Emits a warning: `notes.md <TEAM> has invalid PO satisfaction: <text>` (matching the existing `## Status` behaviour).

### Carry-forward and reused-folder protection

A PO rating belongs strictly to one closed sprint and must never be silently re-tagged to another sprint:
- **Missing-sprint team (`selected[team] is None`):** Preserve stored `PO satisfaction` text without modifying it, displaying a rating, or assigning a new sprint marker.
- **New report folder, closed sprint unchanged:** `PO satisfaction` survives (same as `Summary` and `Exec note`).
- **New report folder, closed sprint changed:** `PO satisfaction` starts **empty**.
- **Existing (reused) report folder, `sprint is not None`, closed sprint changed or marker missing/unresolved:** Unlike `Summary` and `Exec note` (which are intentionally preserved on reused folders), `load_report_notes()` must **clear** `sec["PO satisfaction"]` *before* replacing `notes.markers[team]` whenever `prior != sprint["path"]` (including missing or unknown markers) and `sec["PO satisfaction"]` is non-empty, emitting a warning (`notes.md <TEAM> PO satisfaction cleared because the closed sprint changed`). Because the field is cleared before write, the protection holds across all subsequent reruns.

### Historical trend (6-sprint window)

PO satisfaction history is read from existing report folders (`<dir>/reports/*/notes.md` and legacy `<dir>/*/notes.md`) using the existing `<!-- ledger notes: TEAM=sprint-path; ... -->` marker — no new storage file is introduced.

- **Shared Markdown discovery pass:** `load_report_notes()` always discovers existing `notes.md` files with lightweight `parse_notes()` (on both first run and subsequent `--offline` reruns) to build `po_history` and identify candidate predecessors. When `not exists`, `.notes-state.json` sidecar loading (`_read_notes()`) runs **only** for the selected predecessor folder(s), preserving existing answer baselines without loading sidecars for unrelated folders. Historical folders do not re-emit unrelated warnings; invalid historical PO entries are treated as `None` silently (they already warned when that folder was generated).
- **Authoritative folder selection (deterministic):**
  - For the **current closed sprint**, the current folder's `## PO satisfaction` always wins — **including when blank or invalid** (clearing or invalidating the current folder yields `None` / `–` and never resurrects a score from another folder).
  - For **prior sprints**, if multiple other folders share the same `(team, sprint_path)` marker, select the authoritative folder first by **lexicographically greatest folder path**, then interpret its `## PO satisfaction` value (including blank or invalid). Clearing a rating in the authoritative historical folder therefore clears it cleanly rather than exposing an older copy.
- **Window keying and known limit:** `po_history` is treated as read-only (not mutated by `_values()` or `reconcile_notes()`). `team_sprint()` resolves scores strictly against its existing 6-sprint `window` keyed by `sprint["path"]` (already ordered by `(start, path)`), so future sprint folders are automatically ignored when running `--sprint <older>` and distinct folders with matching display names do not collide. *Known limit:* because `report_folder()` names folders from the report title, two different sprints that produce the exact same report folder name (e.g. two `Sprint 1` iterations) share one `notes.md` on disk and overwrite each other.
- One effective score per `(team, sprint_path)` drives the tile, the current trend cell, and the comparison arrow.

### Comparison arrow

- Compares the current closed sprint's effective score against the **most recent prior sprint in the team's 6-sprint `window` that has a recorded score** (skipping unrated sprints rather than requiring an unbroken streak).
- Higher score → green up arrow: `▲ from 3 vs Sprint 28` (`.d.up`).
- Lower score → red down arrow: `▼ from 5 vs Sprint 28` (`.d.down`).
- Equal score, or no prior rated sprint in the 6-sprint window → no arrow.
- Passes plain score strings (e.g. `"4"`, `"3"`) to `_arrow()` so numeric parsing works cleanly, preserving `_arrow()`'s existing sprint-name prefix convention.

## 4. What the reader sees (`manager_` only)

Configured membership in `t["manager_metrics"]` (see `2026-11-27-configurable-metrics-design.md`) is the **single authoritative gate** for the tile, trend row, PO feedback line, and footnote.

- **Delivery stats tile:**
  - Rendered at its configured position in `.figs` when `"po_satisfaction"` is in `t["manager_metrics"]`; omitted otherwise.
  - Tile content (built without breaking `_fig()`'s HTML-escaping contract on labels — either a dedicated helper or a trusted chip slot in `_fig()`):
    - **Rated:** number `4 <small>/ 5</small>`, label `PO satisfaction`, band chip on its own line (`<span class="chip green">Satisfied</span>` / `amber` / `red`, keeping long labels like `Very dissatisfied` from colliding with the label), and delta arrow when applicable (`▲ from 3 vs Sprint 28`).
    - **Unrated (blank or invalid):** number `–`, label `PO satisfaction`, no chip, no arrow.
  - Reuse existing `.chip` / `.green` / `.amber` / `.red` styles; any extra PO-specific CSS is added conditionally only when PO satisfaction is rendered (and never to shared `CSS`), so default-config HTML and `dm_.html` stay byte-for-byte unchanged.
- **Optional PO feedback line:** **(builder's call)**
  - Rendered below `Scope:` only when `"po_satisfaction"` is in `t["manager_metrics"]` **and** the current sprint has a non-empty PO comment:
    `PO feedback: Smooth release, minor delay on UAT.` (`.note` styling, HTML-escaped).
  - Kept outside the `.figs` tile so tiles stay compact and uniform in height.
- **6-sprint trend table (`_trend_table`):**
  - Includes a `PO satisfaction` row at its configured position in `t["manager_metrics"]` whenever `"po_satisfaction"` is enabled for that team (even if all 6 sprints are `–`).
  - Each cell shows `<score>/5` (e.g. `4/5`), or `–` for unrated sprints.
- **Footnote (`_footnote(False)`):**
  - Controlled by the configurable-metrics footnote rules: when at least one team enables `po_satisfaction`, qualify the arrows sentence (*"Delivery-metric arrows..."*) and recomputation sentence (*"Board figures..."*), and append:
    `PO satisfaction = product owner's 1–5 sprint rating recorded by the delivery manager (5 Very satisfied, 4 Satisfied, 3 Mixed, 2 Dissatisfied, 1 Very dissatisfied); its arrow compares with the most recent prior rated sprint in the last 6 sprints.`
  - When no team enables `po_satisfaction`, the footnote stays byte-for-byte identical to today.
- **Name-leak check:**
  - Runs in `run_report()` after writing `manager_`, scanning rendered HTML for roster full names and emails. Any matching name or email in a rendered PO comment triggers `warning: manager file mentions <name>` (it does not catch arbitrary first names or block writing, consistent with `Summary` and `Exec note`).

### `dm_` card

No change. `dm_` does not repeat `manager_` delivery stats.

## 5. Agent flow and docs

- Update `AGENTS.md`, `skills/delivery-report/SKILL.md`, and `docs/design-v3.md`:
  - Document `## PO satisfaction` in `notes.md` (1–5 scale, optional comment, no engineer names, never carried over to a new sprint).
  - In step 3 of the agent flow, allow asking the DM for **the PO's rating** (1–5 and optional short note) for teams tracking PO satisfaction. Never invent or fill a score on the DM's behalf.
  - Update the Manager card reference, qualify the *"same board history always gives the same numbers"* note (PO ratings also depend on `notes.md` and report-folder history), and update both stale code-cap statements in `docs/design-v3.md` alongside `tests/test_size.py`.

## 6. Data shape

- `Notes` in `ledger/config.py`:
  - `po_satisfaction: Dict[str, Tuple[int, str]]` — current folder's parsed `(score, comment)` per team (rebuilt by `_values()`).
  - `po_history: Dict[str, Dict[str, Optional[int]]]` — read-only historical `{sprint_path: score_or_None}` per team loaded from other report folders.
- `team_sprint()` in `ledger/metrics.py`:
  - `t["po_satisfaction"]`: `None` when unrated in the closed sprint, otherwise:
    ```python
    {
        "score": int,                    # 1..5
        "comment": str,                  # "" if none
        "before": Optional[int],         # most recent prior rated score in window
        "before_sprint": Optional[str],  # name of that prior sprint
    }
    ```
  - `trend[i]["po_satisfaction"]`: `Optional[int]` (`1..5` or `None`).

## 7. Edge cases

- **Older `notes.md` without `## PO satisfaction`:** Parses cleanly with no warning; the section and guide line are written on the next `report` run.
- **Invalid or multi-line `## PO satisfaction`:** Preserved verbatim in `notes.md`, emits a warning, treated as unrated (`None`) for rendering and trend.
- **Missing-sprint team (`selected[team] is None`):** Stored `PO satisfaction` text is preserved without clearing or rendering.
- **Reused folder with changed/missing/unknown sprint marker (when `sprint is not None`):** Clears stale `PO satisfaction` before updating the marker and warns, so the old rating never attaches to the new sprint on reruns.
- **Two-pass reconciliation in `run_report()` & consecutive `--offline` runs:** `po_history` survives the full `run_report()` sequence (empty-notes pass → reconcile → scoped-notes pass → reconcile) and repeated `--offline` runs.
- **Duplicate folders for the same sprint:** Current folder always wins (including blank/invalid); for past sprints, lexicographically greatest folder path is authoritative (including blank/invalid).
- **Running `report --sprint <past>`:** History is resolved strictly against `window` sprint paths; future sprint folders are ignored.
- **Same score as prior sprint:** No arrow shown.
- **Score improves from 2 to 3:** Green up arrow (`▲ from 2 vs Sprint 28`), even though 3 is amber — the arrow indicates direction of change, consistent with the other tiles.
- **Code size cap:** `ledger/*.py` is currently 3,392 / 3,400 lines in `tests/test_size.py`; raise modestly (e.g. to 3,600 for both specs combined) and update both references in `docs/design-v3.md`.

## 8. Tests

All tests use invented histories and notes only (no real names or ticket data):
- Parsing valid `## PO satisfaction` forms: `4`, `4/5`, `4 - comment`, `4/5 - comment`, en-dash/em-dash separators, blank.
- Invalid values (`0`, `6`, `3.5`, `good`, multi-line): emit warning, preserve verbatim, render `–` when enabled.
- Carry-forward:
  - preserved when closed sprint is unchanged;
  - cleared in a new folder when closed sprint changes;
  - cleared with warning in a reused folder when closed sprint changes or marker is missing/unknown (and stays cleared on rerun);
  - preserved untouched when `selected[team] is None` (missing-sprint team).
- Historical lookup across multiple report folders:
  - survives the full `run_report()` two-pass reconciliation and two consecutive `--offline` runs;
  - populates 6-sprint trend row, skips unrated sprints when finding `before`, ignores future sprints on `--sprint <past>`, separates distinct folders with identical display names by `sprint["path"]`;
  - current folder blank/invalid overrides a duplicate folder's valid score;
  - authoritative historical folder (lexicographically greatest path) blank/invalid overrides an older folder's valid score;
  - independent teams in the same folder do not leak ratings into each other;
  - predecessor `.notes-state.json` answer baselines still load when creating a new folder.
- Manager HTML rendering:
  - When enabled + rated: tile renders score, `/ 5`, label, chip on its own line with correct colour (`green`/`amber`/`red`), and delta arrow (up green, down red, omitted when unchanged or no prior).
  - When enabled + unrated: tile renders `–`, trend row renders with `–` for current sprint and historical scores for prior sprints.
  - When disabled: tile, trend row, and PO feedback line are all omitted even if `notes.md` has a rating and comment.
  - Optional comment renders as escaped `PO feedback:` line below `Scope:`, not inside the tile.
  - Missing-sprint team card unaffected.
- CLI integration (`run_report`): name-leak warning fires if a roster full name or email appears in a rendered PO comment.
- Default-config `manager_.html` and all `dm_.html` outputs are byte-for-byte unchanged (with fixed timestamp).
- Update `tests/test_notes.py` section-list expectations for the 7th section.
