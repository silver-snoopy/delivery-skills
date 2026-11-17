# Delivery Ledger

The Delivery Ledger reads Azure DevOps work item history and writes sprint delivery reports for a delivery manager (DM) who supplies engineers to a customer. It is a command-line tool in standard-library Python, driven in conversation by one agent skill, `delivery-report`. It only reads the board and never sends a report anywhere.

For each sprint it writes two self-contained HTML files, named with the run date: `manager_DATE.html` for executives and `dm_DATE.html` for the DM only. The executive file has one card per team with delivery highlights, stats and foldable delivered, carried-over and trend panels; it has no RAG status, no people tables and no per-person figures. Every `#ID` links to the work item in Azure DevOps. Ticket titles and the DM's own notes appear in it as written, so `report` warns when a roster name or email appears in it; read it before you send it.

## Installation

You need Python 3.9 or later and, for a real board, the `az` CLI signed in with `az login`. The `azure-devops` extension is optional: it supplies the default organisation and project; without it, pass `--org` and `--project` to `init`. There are no packages to install.

```sh
git clone <this repository> && cd delivery-skills
python3 -m unittest discover -s tests
scripts/install-skills.sh
```

`scripts/install-skills.sh --user` installs the skill for every project, and `--project DIR` into another one. Agents without skill support can follow `AGENTS.md`.

## Usage

Ask your agent for "the sprint report", or run the three commands by hand. Working files are in `.delivery-skills/` under the current directory (`--dir` changes it).

```sh
bin/ledger init --query Q --staff staff.txt --vacations vacations.xlsx   # team and join date per person
bin/ledger report [--sprint S]                                           # manager_ and dm_ files
bin/ledger person EMAIL                                                  # one engineer, last 6 sprints
```

`staff.txt` lists your people as `Name <email>;`. `vacations.xlsx` is your team calendar and is read again on every `report`. `init` asks, for each person, the team and the join date, and prints the counting levels, waiting states and closure reasons it chose so you can correct `config.json`. `team.csv` can be edited afterwards; a new team name there is picked up by `report`, which warns until the team has a `count` in `config.json`. A trailing tag such as `(external)` is dropped from staff names; `squad_teams` in `config.json` maps the calendar's squad codes to team names.

Without `--sprint`, `report` takes each team's own last ended sprint. When the teams' sprints have different names, the report is called `Sprints ended by DATE`. A team with no sprint gets a card that says why. The private dm_ file is for the DM and team leads, never the customer. It shows running-sprint actions before the closed Team figures.

Report prints the notes path and action prompts. Ask about each action and put the DM's answer in its Answer cell. For a carry-over, ask whether the PO agreed and why. Ask for optional notes per team. Add an executive note only if the DM asks for it. Propose summary bullets from the printed worked-on titles and have the DM confirm them. Rerun report, show every warning, and give the DM the two HTML paths. The DM reads manager_ before sending it.

## Working with notes.md

`notes.txt` is no longer read. There is no automatic migration. Copy needed text into the matching sections only after the DM confirms it.
Keep `notes.md` and `.notes-state.json` together when copying a report folder to another machine. The hidden sidecar records the last observed answer and its sprint scope. Without it, the tool imports Answered metadata conservatively and answered rows may need review again.

The first line is a tool-owned marker:
`<!-- ledger notes: TEAM=closed-sprint-path; ... -->`.
It records each team's closed sprint. Do not edit it.
Each team has a `# TEAM` heading and these sections, in this order:

```markdown
## Summary
- Delivered upload validation.
- In progress: retry handling.

## Exec note

## Note

## Status

## Actions
| Person | Work item | Problem | Answer | Answered |
|---|---|---|---|---|

## Resolved
| Person | Work item | Problem | Answer | Answered |
|---|---|---|---|---|
```

Summary supplies Delivery highlights. Exec note appears in both reports. Note appears only in dm_. Status is blank for the computed closed-sprint status, or `GREEN|AMBER|RED - reason` for a manual override in dm_. Keep names out of Summary, Exec note and Status. Carry-over answers reach the manager_ file, so no names in them. A reason is shown only from a prefixed answer entered or changed in this folder on a carry-over row for the current closed sprint. Copied answers and running-only answers supply no manager reason. The reason is the text after the prefix and still passes through the manager name warning.

Answer is the only DM-owned cell in either table. Answered is tool-owned, as are Person, Work item and Problem. The tool rewrites those cells. Answered records the date, severity level and every signal kind present when the answer was observed. It uses display labels, for example `2026-11-16 amber Chase, Waiting`.

Actions holds red and amber problems, including answered ones. OK and grey checks are not listed there. When a problem goes away, an answered row moves to Resolved. If it returns, the row returns to Actions with its answer. The tool keeps the DM's Answer text and text sections. Malformed lines and unknown sections are kept with warnings. In table cells, escape a pipe as `\|` and a backslash as `\\`.

Carry-over answers start with `agreed:`, `not agreed:` or `unknown:`, followed by the reason. Without a prefix, the answer counts as unknown for PO agreement. Other answers record what the DM agreed to do.

On rerun, an answered problem shows Handled unless it got worse. A higher level, a new signal kind or a new sprint while the item stays open makes it worse. Its badge then shows `↑`. Editing Answer resets its baseline on the next run. Editing Answered does not reset it; the tool repairs that cell. Answers change presentation, not figures, except that `agreed:` changes the closed-sprint PO agreement rule and status.

A new folder carries notes forward per team. If the closed sprint is unchanged, all sections survive. If it changed, only answers for rows still open carry forward from Actions and Resolved; the other sections start empty. Copied answers do not renew PO agreement for a new closed sprint. They must be changed or re-entered. To re-enter identical text, clear Answer, run report, then enter the answer and run report again. Replacing it with identical text before a run does not renew it. Comparisons and prefix parsing strip and collapse whitespace; changing only whitespace does not renew agreement or reset the baseline. The DM's Answer text in the file is preserved.

`--sprint` must name an ended sprint: its end must be before `--today`. A named past sprint suppresses the running view. Otherwise dm_ also shows each team's running sprint, if any. Its snapshot is the start of `--today` in the configured time zone (end of yesterday). The first-day planned snapshot is clamped to that instant. There is no running-sprint carry-over, forecast, burndown or RAG status. Manager_ contains only closed-sprint results and no per-person cells.

Unanswered red and amber problems print as `TEAM action: PERSON #ID BADGE - PROBLEM`. Person-only actions omit `#ID`. Answered problems that worsen print `TEAM action: PERSON #ID BADGE ↑ - PROBLEM`. Carry-over rows with copied answers also print an action line because they have no agreement for this closed sprint. For each printed action, ask the DM again and write only their answer into the Answer cell.

`TEAM carry-over without note: #ID TITLE` names a carry-over with no agreement answer yet for this closed sprint, including one with a copied answer. The same item also has an `action:` line. Ask about it once and write one answer in that row's Answer cell. Start it with `agreed:`, `not agreed:` or `unknown:`, followed by the reason.

## How figures are counted

- Everything is counted in work items, at the level each team chose: stories, tasks, or an explicit list of work item types. The DM card shows it as "Counts: ...".
- Planned means in the sprint and assigned to one of the team's engineers at the end of its first day. Work that becomes theirs later counts as added, not planned.
- A sprint gets credit for the work items closed while on its iteration, even after its end date; a close after the end is shown as "completed after the sprint ended".
- A sprint figure is "delivered X of Y planned", not counting removed items. An unfinished planned item that is still on this sprint or moved to a later sprint is a carry-over; it is removed only when it went back to the backlog (or another path that is not a sprint) or was closed with a not-delivered reason.
- Cycle time runs from the first In Progress or Resolved state to the first delivered completion, in days; the report shows the median and the P85 (nearest rank) over the last 6 sprints.
- First-time pass is the whole-percent share of delivered work items never sent back from a `test_states` state or reopened, per team. It shows "not tracked" when no scored item moved back in the last 6 sprints, or "–" when nothing was delivered.
- Waiting is time in states you mark as waiting (or with the Blocked tag). More than 3 working days is a DM flag; more than 10 makes the status RED.
- On Hold is the number of working days an item spent in a hold state (`hold_states`) or with the Blocked tag during the sprint. It is shown for context and never changes a figure.
- Board history covers the last `history_days` days (default 200) and is kept in the cache. Every run fetches the cached items again, so an item that left the query keeps its real latest state; an item that is deleted or no longer readable is dropped, with a warning.
- Status (DM file only) is GREEN at 80% delivered or more with no old, unagreed carry-over; AMBER if one condition is missed; RED if both are, or on a long wait. You can override it in the `## Status` section of `notes.md`.
- Thresholds are fixed local conventions, not industry standards, and the footnote says so. Every figure depends on the board being kept up to date.

Per-person reporting on employees may be subject to local law, data protection rules and works-council agreements; the operator is responsible for checking what is allowed. A licence has not been chosen yet; until one is added, this project must not be redistributed.
