# Executive card on manager_: plan

Spec: `docs/superpowers/specs/2026-11-15-exec-highlights-design.md`.

1. `metrics.team_sprint` returns `delivered_items` (id, title at sprint end, planned) and `worked_items` (id, title, delivered; the union of the people's items, delivered first).
2. `config.parse_notes` joins several `summary:` lines per team with a newline and drops a leading "- ".
3. `render`: add `_figs(t, carried)`, `_fold`, `_summary`, `_delivered`, `_trend_table`; add `_exec_team(t)` for manager_; `_capacity(t, dm)` builds the three-line header.
4. `run_report` prints the worked-on lines instead of the delivered lines and warns for a team with no summary.
5. Update `README.md`, `AGENTS.md`, `skills/delivery-report/SKILL.md` (step 5) and `tests/test_docs.py`.

Tests: `test_manager_card_layout`, `test_manager_card_without_summary_has_no_highlights`, `test_manager_card_exec_note_without_summary_keeps_highlights`, `test_manager_card_multiline_summary_renders_as_bullets`, `test_manager_card_single_line_summary_unchanged`, `test_dm_card_unchanged`, `test_dm_card_multiline_summary_renders_as_bullets`, `test_delivered_and_worked_items`, `test_parse_notes_multiple_summary_lines_join_and_strip_dash`.
