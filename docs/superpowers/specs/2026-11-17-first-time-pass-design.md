# First-time pass: design

Status: agreed with the DM on 2026-11-17 (grilling, two rounds). Source idea: `docs/improvements/2026-11-17-first-time-pass-rate.md`.

## 1. Intent

Management sees how much the team delivers, but not whether "done" really meant done. A new team figure, **first-time pass**, shows the share of delivered work items that never went backwards: never sent back from testing and never reopened. It is a team-level quality signal. It is never shown per person.

## 2. Goals, non-goals, anti-goals

- Goal: one team figure per closed sprint, on the manager_ card and in its trend, from the board history the tool already reads.
- Non-goal (version 1): bugs linked to a story as a second return signal; a running-sprint "returned 2×" signal on dm_; any per-person figure.
- Anti-goal: a false 100%. A team that never records returns on the board must show "not tracked", never a perfect score.

## 3. Definitions

- **Scored items:** the work items the team delivered in the sprint, at the team's own counting level. These are the same items as "work items delivered in total" (`figures()["delivered"]`), so credit goes to the iteration at first close. Open items are not scored; they are scored in the sprint where they close. Teams that count tasks are measured on tasks too, not on parent stories.
- **Test states:** a new `test_states` list in `config.json`, global like `waiting_states`.
- **Test return:** a revision where the previous state is in `test_states` and the new state is not in `test_states` and is not in category Completed or Removed. This includes a move back to a Proposed state. Moving between two test states, or forward to Completed, is not a return. Tags (for example "blocked") never count.
- **Reopen:** a revision from a delivered close (previous category Completed, with no not-delivered reason) to category Proposed, InProgress or Resolved.
- **Returned item:** a scored item with at least one test return or reopen anywhere in its history up to the report time. A reopen always comes after the first close, so a later reopen counts against the sprint that got the credit. Rerunning an old sprint's report can therefore lower its figure. This is intended.
- **First-time pass:** (scored − returned) / scored, shown as a whole percent. Each item counts once, however often it bounced.
- **Tracked:** a team is tracked if at least one of its scored items in the trend window (the last 6 sprints, as in the existing trend) is returned. Otherwise every figure for the team shows "not tracked".

## 4. What the reader sees

- **Delivery stats** (manager_ and dm_, because they share the figure row) get one more figure:
  - tracked: `92%` with the label `first-time pass (2 returned)`; with no returned item just `first-time pass`;
  - not tracked: `not tracked` with the label `first-time pass: no item moved back on the board in the last 6 sprints`;
  - nothing delivered in the sprint: `–` with the label `first-time pass`.
- **Trend table** gets one more row, `First-time pass`, with one cell per sprint: `–` when nothing was delivered comes first; otherwise the percent for tracked teams, or `not tracked`.
- **Footnote** (both pages) gets one sentence: First-time pass = share of delivered work items never sent back from a test state and never reopened, counted per team; "not tracked" means no item moved back in the last 6 sprints.
- No figure per person anywhere.

## 5. Setup and config

- `init` suggests `test_states`: states in category InProgress or Resolved whose name contains one of qa, test, review, verify, uat, accept, resolved (any case). It prints them as `Proposed test states: ...` next to the waiting and hold states. AGENTS.md and SKILL.md step 1 ask the DM to confirm them as well.
- An existing `config.json` without `test_states` still loads. `report` prints one line, `warning: no test states in config.json; first-time pass counts reopens only`, and counts reopens only. The tracked rule still applies.

## 6. Constraints

- Python 3.9+, standard library only. No names, customer data or real ticket data in the repo, including tests.
- The code cap in `tests/test_size.py` goes from 3,200 to 3,300 lines.
- Docs to update: `docs/design-v3.md` (metric definition), `AGENTS.md` and `skills/delivery-report/SKILL.md` (init confirmation), `README.md` if it lists the figures.

## 7. Left to the builder

The data shape in `team_sprint` output, function names, and whether return detection lives on `History` (next to `reopened`) or in metrics.

## 8. Calls made without the DM

- The figure also shows on dm_, because dm_ shares the figure row. It is team-level only.
- `–` when nothing was delivered. Whole-percent rounding.
- `test_states` is global, not per team, like `waiting_states`.
