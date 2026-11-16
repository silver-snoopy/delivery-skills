# DM action view: design

Status: draft 2026-11-16, agreed in conversation; not yet planned. Builds on `docs/design-v3.md` (as amended by PR #2, branch `v3.1-trial-fixes`).

## 1. Why

The DM page today ends in a long "Needs attention" list plus a People table. In the trial it read as a huge list of tickets. The DM wants a working view that answers, per team and per person: **who is stuck, who has no work, what to chase and resolve**, plus any other signal. It must cover the **sprint that is running now**, not only the last closed one, because that is when problems can still be fixed.

The DM goes through this page with **our own team leads** (never the customer's), records what was agreed for each problem, and wants the next run to show which problems are handled and which got worse.

## 2. Goals, non-goals, anti-goals

Goals:
- One action table per team: one row per person per work item, with signal, problem, action and age, worst first.
- The running sprint is covered with its own rules (nothing is "carried over" before the sprint ends).
- The DM's answers live in `notes.md` per row; the next run shows answered rows as Handled, and brings them back when they get worse.
- `notes.md` replaces `notes.txt` completely (summary, exec note, note, status and the actions table).

Non-goals:
- No change to the manager_ report's content or layout (it reads summary and exec note from `notes.md` instead of `notes.txt`). Management sees only results of closed sprints.
- No forecasting ("will finish 8 of 10"), no burndown chart, no RAG status for a running sprint.
- No support for the old `notes.txt` format (clean switch; the same version goes to the DM's other machine).
- The `person` command is unchanged.

Anti-goals (a failure even if it works):
- The page stays red after the DM has dealt with everything (answers must step rows down).
- A stalled item hides forever behind an old answer (rows must come back when they get worse).
- The tool overwrites or loses anything the DM typed into `notes.md`.
- Clutter: the handled state is shown by a badge only, with no extra lines of text.
- Any name reaches manager_ (unchanged rule and warning).

## 3. Audience and marking

dm_ is for the DM and our team leads, never the customer. The private marker at the top changes to: "Internal: delivery manager and team leads only. Not for the customer." Per-person content stays out of manager_.

## 4. Which sprints

Per team (each team has its own sprints, as in PR #2):
- **Closed sprint:** the team's last sprint that ended before `--today` (or the sprint named by `--sprint`). Everything about it is as today: figures, status, carry-overs, trend, ours vs rest.
- **Running sprint:** the team's sprint with start ≤ today ≤ end, if any. Not shown with `--sprint` (a named past sprint has no "now").
- **As-of time** for the running sprint: the start of `--today` in the configured time zone (end of yesterday), so reruns on the same day give the same rows. Working day N of M counts Mon-Fri from the sprint start up to and including yesterday.

The report folder and file names stay as today (named after the closed sprint, or "Sprints ended by DATE").

## 5. The dm_ page

Order:
1. Header and marker (section 3).
2. "Your notes for this sprint" block (the `## Note` sections), as today.
3. Per team, one card:
   1. **Ours vs rest of squad** table for the closed sprint (as today).
   2. **Current sprint line** (only when a sprint is running): `<sprint> · day N of M working days · planned done X of Y · +A added / −R removed · H On Hold · W waiting`. Planned, added and removed use the running-sprint rules in section 6.
   3. **Action table** (section 7).
4. **Team figures** for the closed sprint: the existing DM team cards (status, worked on, summary, stats, carry-over table, trend), unchanged.
5. Footnote, extended with the running-sprint rules and the badge meanings.

Removed from dm_: the "Needs attention" list per team and the People table. Their content moves into the action table. The flag texts and asks in `metrics._attention` stay as the source of problem and action wording.

## 6. Running-sprint rules

Counted, ours items are decided as today (`count`, ours, levels). Times: t0 = end of the sprint's first day; now = the as-of time.

- **Planned / added / removed / done** as today, with now in place of t1. **No carry-over** before the sprint ends: an open planned item is just open.
- Item signals (only for items ours at now and open at now, plus items closed in the sprint for the grey checks):

| Badge | Level | When |
|---|---|---|
| Stuck | red | waiting or On Hold for more than `WAIT_RED_DAYS` (10) working days, or open longer than the team's P85 **and** carried in from an earlier sprint |
| Waiting | amber | in a waiting state or tagged Blocked for more than `WAIT_FLAG_DAYS` (3) working days |
| On Hold | amber | in a hold state now (any duration) |
| Chase | amber | open longer than the team's P85 (window P85, as today) |
| No movement | amber | active with no board change (including children at the stories level) for `STALE_DAYS` (5) working days |
| Not started | amber | planned, still in the Proposed category after working day `NOT_STARTED_DAY` (new constant, 3) |
| Check | grey | closed without ever being active; reopened |
| Carry-over | amber (red when older than P85 and carried 2 or more sprints) | closed-sprint rows only: a carry-over not answered `agreed:` |
| OK | green | open, no signal |

- Person signals:

| Badge | Level | When |
|---|---|---|
| No work | red | not onboarding, has available days left in the sprint, and has no open item assigned at now (neither active nor proposed) |
| Too much WIP | amber | more than `WIP_FLAG` (3) items in progress at now |

- One row per item; when several signals apply, the row shows the most severe badge and joins the problem texts with "; ".
- **Owner of a row:** each item has one row, under one person: its assignee at now when that is one of the team; otherwise (stories level) the team member assigned to one of its open children, the most recently changed child first. An item ours only through history (no current ours assignee anywhere) goes under the last ours assignee. Answers are keyed by item id, so a reassignment moves the row and keeps its answer.

## 7. Action table

Columns: **Person · Work item · Sprint · Signal · Problem · Action · Age**.

- Person cell spans the person's rows: name, `available / nominal d available` (running sprint if any, else closed sprint), and an "onboarding" chip when it applies. Every member with working days in the sprint appears, even with no rows ("nothing open").
- Rows come from:
  - the running sprint (Sprint = "Now"): every open ours item (signal or OK), person signals, grey checks;
  - the closed sprint (Sprint = its name): only its carry-overs that are not answered `agreed:`, and carry-overs older than P85. When the same item also has a "Now" row, the two merge into one row (Sprint = "Now", problems joined).
  - With no running sprint, the closed sprint's own signals fill the table instead, with the closed-sprint rules of today mapped to badges: old → Chase (red: Stuck), carried 2+ sprints or no PO agreement → Carry-over, waiting → Waiting, no board change → No movement, no item assigned → No work, nothing delivered → "Nothing delivered" (amber person badge), WIP → Too much WIP, never active / reopened / shared → Check.
- Work item cell: `#ID` (linked as everywhere) and title.
- Action: the tool's suggested action (`ASK` texts), or the DM's answer when there is one (answer only, no extra line).
- Age: calendar days since first active, for open items; "–" otherwise.
- Ordering: people by their worst **open** signal (red, amber, grey, then only handled/OK), then name. Within a person: open rows by severity, then handled rows, then OK rows; ties by id.
- Badges: as in section 6, plus **Handled** (grey) for an answered row that has not got worse. A row that got worse since its answer keeps its own badge with " ↑" appended and the tooltip "worse since your answer". No other text marks these states.

## 8. Answers and re-escalation

- An answer is the Answer cell of the row's line in the team's `## Actions` table in `notes.md` (section 9). A row is matched by work item id; a person row (no work item) by person name and badge.
- Answered and not worse → Handled. The figures and the status never change because of an answer, except the existing rule: a carry-over answered `agreed:` counts as agreed with the PO.
- **Worse** means any of these since the answer was recorded:
  1. the row's level went up (amber → red);
  2. a new kind of signal appeared on it (for example Not started → On Hold);
  3. a new sprint started for that team and the item is still open.
- To judge "worse", the tool stores, per answered row, the date it first saw the answer, the level, and **every** signal kind present then, in an `Answered` cell it fills itself (for example `2026-11-23 amber Waiting, Chase`). Rule 2 compares against that full set, so a signal that was already there never counts as new. The DM never edits that cell; if they do, the tool rewrites it.
- The Person, Work item and Problem cells are tool-owned too and rewritten on every run. The DM's text lives only in Answer (and in the Summary, Exec note, Note and Status sections), and the tool never changes it.

## 9. notes.md

Location: `.delivery-skills/<report folder>/notes.md` (replaces `notes.txt`). UTF-8, a BOM is accepted. Written by the tool on every `report` run and edited by the DM or the agent in between.

```markdown
# ALFA

## Summary
- Delivered pricing engine fixes and two broker portal items.
- In progress: FX rate refresh.

## Exec note
Velocity dipped while the FX feed dependency was open.

## Note
One engineer covered production support for three days.

## Status

## Actions
| Person | Work item | Problem | Answer | Answered |
|---|---|---|---|---|
| John Doe | #7301 FX rate refresh | On Hold 6 working days | chased: fix promised Thu, PO aware | 2026-11-23 On Hold |
| John Doe | #7302 Notification templates | carried over, no PO answer | agreed: split in refinement | 2026-11-23 Carry-over |
| Anna Smith | – | No work, 9 days available | lent to pricing spike until Fri | 2026-11-23 No work |

## Resolved
| Person | Work item | Problem | Answer | Answered |
|---|---|---|---|---|
```

Rules:
- One `# TEAM` section per reported team, in team order. Sections and their order are fixed: Summary, Exec note, Note, Status, Actions, Resolved.
- Summary: one bullet per line (a leading "- " is optional); becomes Delivery highlights on manager_ (as the multi-line summary does today).
- Exec note: both files. Note: dm_ only. Status: empty = computed; otherwise `GREEN|AMBER|RED - reason`, as today.
- Actions: the tool lists every red or amber row of the action table (not OK, not grey). On each run it keeps every existing row's Answer and Answered cells, updates the Problem text, adds new rows, and moves rows whose problem is gone to Resolved (with their answer). It never deletes a row that has an answer; a Resolved row without an answer may be dropped.
- Carry-over answers start with `agreed:`, `not agreed:` or `unknown:`; the status rule and the carry-over table read them as they read the `#ID` lines today. An answer on a carry-over row without one of those prefixes counts as `unknown` for the status rule (the row is still Handled on the page).
- Agreement is per closed sprint, as today: an answer copied forward from an earlier folder never counts as agreement for the new closed sprint. Copied answers show on the page, but the row is not Handled (rule 8.3) and the status treats the carry-over as unknown until the DM answers again in the new file.
- Cells escape `|` as `\|`. A line the tool cannot parse is kept verbatim at the end of its section and reported with a `warning:`.
- The first line of `notes.md` is a tool-owned marker: `<!-- ledger notes: TEAM=closed-sprint-path; ... -->`, listing each team's closed sprint. A new report folder finds its predecessor as the existing `notes.md` (under `.delivery-skills/*/`) whose marker has the latest closed-sprint end dates, and copies forward **per team**:
  - a team whose closed sprint is the same as in the predecessor: everything (Summary, Exec note, Note, Status, Actions with answers, Resolved); answers keep their Handled state;
  - a team whose closed sprint changed: only the Actions answers for rows still open (rule 8.3 makes them not-Handled); its other sections start empty.
- A Resolved row whose problem comes back moves back to Actions with its answer and `Answered` cell, and is judged by section 8 (Handled unless worse).
- Warnings, as today: a team section with no team in the report, a missing summary for a team with a sprint, roster names in text that reaches manager_.

## 10. CLI and agent

- `report` behaviour and output lines stay, plus one console line per open, unanswered red or amber row: `TEAM action: PERSON #ID BADGE - PROBLEM` (person rows without `#ID`), for the agent to ask about.
- AGENTS.md and the skill: replace the carry-over question loop with a loop over the printed action lines: ask the DM (or DM and team lead) about each one, write the answer into the Answer cell. Write summary bullets into `## Summary`. Never write names into Summary, Exec note or Status.
- `tests/test_docs.py` follows the new wording.

## 11. Build constraints

- Python 3.9+ standard library only; the code cap rises to fit (target: about 2,900 lines under `ledger/`; the plan sets the exact figure).
- The running-sprint rules live in `metrics.py`, beside the closed-sprint rules, reusing the replay helpers; rendering in `render.py`; `notes.md` reading and merging in `config.py`.
- Tests use invented fixtures only. Cover at least: no carry-over before the sprint ends; each badge's trigger; person rows; ordering; merge of a "Now" row with a closed-sprint carry-over; Handled; each of the three "worse" triggers; notes.md round trip that keeps every DM edit; unparseable lines kept; carry-forward to a new folder; the manager_ page unchanged apart from its notes source.

## 12. Calls made without the DM (check these)

- One row per item, under one owner (section 6); answers keyed by item id.
- The `Answered` cell stores every signal kind at answer time, so pre-existing signals never re-escalate a row.
- Carry-over agreement stays per closed sprint; copied answers never count as agreement; an unprefixed answer on a carry-over is `unknown` for the status.
- `notes.md` carries a tool-owned marker line; carry-forward is per team, and a team whose closed sprint did not change keeps everything.
- A Resolved row that comes back returns with its answer.
- Person, Work item and Problem cells are tool-owned; only Answer and the text sections belong to the DM.

- As-of time = start of `--today` (end of yesterday).
- `NOT_STARTED_DAY` = 3 working days; On Hold flags at any duration; Stuck = waiting/On Hold over 10 working days or old and carried in.
- The running sprint has no RAG status; the closed sprint's status stays in Team figures.
- Resolved rows are kept in a `## Resolved` section rather than deleted.
- The `Answered` cell is tool-owned state inside `notes.md`.
- The People table is removed; the person cell carries available days and onboarding.
- The dm_ marker text in section 3.
