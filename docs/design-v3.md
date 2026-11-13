# Delivery report v3: design

Status: approved 2026-11-13, as built. Replaces the v2 Delivery Ledger design (still in git history). Research behind it: `research/Sprint delivery reporting for ADO.md` (git-ignored).

## 1. Why

A delivery manager (DM) supplies engineers to a customer's Scrum teams ("squads") on Azure DevOps. The customer says two things: **"you are slow"** and **"you are not delivering"**. The DM is not in every daily and does not micromanage 20+ people. After each sprint the DM wants:

1. A one-page report for executives that shows, per squad, what the team delivered and why anything did not land. No people tables and no per-person figures.
2. A private DM report that says where to intervene, with per-person facts.

Anyone in the DM role must be able to reuse it on any project with Azure DevOps Services, with nothing installed in the customer's organisation.

## 2. Goals, non-goals, anti-goals

Goals
- Answer five questions from board data:
  - Did we deliver what was planned?
  - How much do we deliver per sprint?
  - Are we slow, and if so is it our work or waiting time?
  - What is carried over, for how long, and why?
  - Who needs a conversation?
- The same board history always gives the same numbers. Run it as often as you like.
- Industry-standard definitions (Azure DevOps velocity, Scrum Guide, flow metrics), stated on the report.
- Set up a new project in about 15 minutes.

Non-goals
- No editions, locking, freezing, approval steps, risk register or names-check machinery. (A one-line warning when a roster name appears in the executive file is not machinery.)
- No story points, no DORA, no forecasting.
- No dashboards hosted anywhere. The reports are files the DM sends themselves.

Anti-goals: this fails even if it works when
- it ranks people or puts per-person figures in the executive report,
- a number changes between two runs on the same data,
- setup asks the operator more than each person's team and join date,
- the tool grows past about 2,400 lines of code (enforced by a test).

## 3. Setup

The operator provides three things. The tool does the rest and asks only what it cannot find.

| Input | Form |
|---|---|
| The query | A saved query URL (`.../_queries/query/<id>` or `query-edit/<id>`) or id, a WIQL text starting with `SELECT`, or a root area path (`Project\Claims`). Any other `http(s)://` text is an error that names these forms. It should cover the work of the operator's people. |
| `staff.txt` | The operator's people, as copied from a mail distribution list: `Name <email>;` (one or many per line). An entry without an email address is skipped with a warning. |
| `vacations.xlsx` | The team calendar: days off per person, public holidays included; one row per employee, one column per day, months side by side. |

```
ledger init --query "<query>" --staff staff.txt --vacations vacations.xlsx [--yes]
```

What `init` does:
1. Checks access to Azure DevOps with the operator's `az login`.
2. Reads the board history of the last `history_days` days (default 200; see section 4). From it, it finds each staff member's area paths and item count, the sprints and their dates, every state in use and every closure reason.
3. Asks, each time with a suggestion. It asks only when stdin is a terminal; `--yes` (or no terminal) accepts every suggestion. For each person in `staff.txt`:
   - **Team?** Suggested from the calendar's `Squad` column, else from the team already chosen for a colleague in the same main area path, else from the last segment of the person's main area path. Team names are upper-cased. Teams are the operator's own grouping of people, not Azure DevOps teams. One area path can hold several teams.
   - **Joined the team on?** Suggested from the person's first activity on the board. When that first activity is within 30 days of the start of the history window, the suggestion is blank (unknown, so never onboarding): the person was probably there before the window.
   People with no tickets at all are asked too. They stay on the team and show up in the report as having capacity but no assigned work.
   Then, once per team: **Count stories or tasks?** Suggested from the team's delivered items in the window: tasks when it closed more than five times as many tasks as stories, stories otherwise. The suggestion comes with the numbers behind it.
4. Writes `.delivery-skills/team.csv`, `config.json` and the cache, and prints a summary: people, counting level per team, every state (waiting states marked `*`), the proposed waiting states, the not-delivered reasons, the closure reasons seen on the board (not-delivered ones marked `*`), calendar names it could not match, and warnings. Warnings include each staff email never assigned on the board ("check the sign-in email") and a time zone it could not detect.

```
name,email,team,joined,left,allocation
John Doe,john.doe@vendor.example,ALFA,2025-03-03,,1
Maya R.,maya.r@vendor.example,ALFA,2026-10-12,,1
Tom H.,tom.h@vendor.example,ALFA,2024-11-04,,0.5
```

- One line per email; two emails for one person are two lines with the same name (two lines in `staff.txt` with the same name).
- `joined` is the first working day counted; `left` is the first day no longer counted (exclusive). Both may be blank.
- `allocation` (default 1) multiplies capacity.
- Team names come from `team.csv` (stripped and upper-cased on read). A team typed into `team.csv` is reported automatically; when `config.json` has no `count` for it, it counts stories and `report` warns. A team in `config.json` with no one in `team.csv` is not reported, with a warning.
- Running `init` again with a new `staff.txt` asks only about new people and keeps every earlier answer. People who left are marked with a `left` date, not deleted, so earlier sprints still report correctly.
- Everyone else who appears on the board (customer staff, other vendors) is never listed. They form the "rest of squad" for the area paths our teams work in.

`config.json` is written by `init`. The operator reviews it and corrects it from the printed summary.

```json
{
  "organization": "https://dev.azure.com/ORG",
  "project": "PROJECT",
  "query": "<query>",
  "timezone": "Europe/Zurich",
  "teams": {"ALFA": {"count": "stories"}, "BETA": {"count": "tasks"}},
  "onboarding_sprints": 2,
  "half_day_codes": [],
  "waiting_states": ["Ready for QA", "In Review", "PO Acceptance"],
  "not_delivered_reasons": ["Removed", "Won't Fix", "Won't Do", "Cut", "Duplicate", "Obsolete", "Deferred", "Abandoned"],
  "staff": "/abs/path/staff.txt",
  "vacations": "/abs/path/vacations.xlsx",
  "name_aliases": {"Brandt Zoe (Ext)": "Zoë Brandt"},
  "history_days": 200
}
```

- `organization` and `project` come from `--org`/`--project`, else the `az devops` defaults. `timezone` comes from `--timezone`, else the machine (`TZ`, then `/etc/localtime`). If the machine zone cannot be found, `init` uses UTC and warns the operator to set `timezone`. An unknown zone name is an error; it never falls back silently.
- `count` must be `stories` or `tasks`; anything else is an error naming the team.
- `waiting_states` is proposed from active-category state names (review, QA, test, accept, wait, blocked, verify, UAT). `init` prints every state so the operator can correct the list.
- `not_delivered_reasons` match the closure reason in any case.
- `staff` and `vacations` are the input files; `report` reads the calendar again on every run.
- `name_aliases` maps a calendar name to a `team.csv` name; `init` fills it from the operator's answers.
- `history_days` sets the history window (section 4).
- `onboarding_sprints`: delivery is expected from the third sprint after a person's join date. In their first two sprints a person is shown as "onboarding" and never triggers a "nothing delivered" or "no item assigned" flag.
- Unknown keys are ignored with a warning that names them.
- Sprint dates and state categories (Proposed, InProgress, Resolved, Completed, Removed) are read from Azure DevOps on every run, never configured.

`vacations.xlsx` is read again on every `report`, so the operator can just replace the file. Layout, as in the operator's team calendar: one sheet with several months side by side. A title row holds merged cells that name each month ("November 2026") as text over its day columns. The header row below holds the labels of the person columns (one of them `Name`, optionally `Squad`) followed by at least 28 day numbers that run on across months (… 30, 31, 1, 2, …). One row per employee. Any non-empty day cell is a day off (letter codes such as V, S, H); public holidays are marked in each person's row the same way, so no separate holiday list exists. Weekend shading is ignored (weekends never count as working days). Codes listed in `half_day_codes` (default none) count as half a day. Rows are matched to `team.csv` by `name_aliases`, then by name, ignoring case, accents and the order of first and last name; `init` asks about any name it cannot match and remembers the answer. Two calendar rows that resolve to one person are merged (the larger value per day). `report` warns about calendar names that match no one, about team members with no calendar row, and when no calendar day falls inside the reported sprint ("the calendar does not cover <sprint>"). When a `Squad` column exists, `init` uses it as the first team suggestion. The file is read with the standard library (`zipfile` and XML), with no Excel package.

Per-sprint notes, written by the DM or by the agent from the DM's answers: `.delivery-skills/<sprint>/notes.txt`, plain text, UTF-8 (a byte-order mark is accepted), one entry per line, keys in any case.

```
#48211 agreed: QA environment down 3-5 Nov, PO moved acceptance to S30
#48277 unknown: dependency on payments API team
Alfa summary: The team finished document upload and validation for claims intake ...
Alfa note: private remark, DM file only
Alfa exec note: remark for both files
Alfa status: AMBER - reason for a manual colour
```

Lines that match no form are ignored with a warning. So are lines for a team that is not in the report and `#id` lines for an id that is not a carry-over of any reported team.

## 4. Data source

Azure DevOps **Analytics OData** (`WorkItems`, `WorkItemRevisions`, `Iterations`) and the WIQL and process-configuration REST APIs, read with the DM's own `az login` token (`az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798`, fetched once per run, with a 60-second timeout). This needs the Reader role ("View analytics"). Nothing is installed in the organisation; on the DM's machine the `az` CLI is needed, and its `azure-devops` extension only for the default organisation and project.

- Scope of one run, with `since` = today minus `history_days`:
  - the ids the setup query returns (for an area path: items under it changed since `since`);
  - their child items;
  - items with a revision assigned to a `team.csv` email changed since `since`;
  - the full revision history of all of those, plus the titles of their parents.
- Fields: `WorkItemId, Revision, ChangedDate, WorkItemType, State, StateCategory, Reason, ParentWorkItemId, Title, TagNames`, with `$expand=AssignedTo($select=UserEmail),Area($select=AreaPath),Iteration($select=IterationPath)` (area and iteration paths are navigation properties). Date literals in filters are written `YYYY-MM-DDT00:00:00Z`.
- Sprint dates come from `Iterations` (the calendar date of `StartDate`/`EndDate`). The stories and tasks levels come from the project's process configuration (requirement and task backlogs).
- Each run also fetches again every item already in `.delivery-skills/cache/revisions.json`, then merges: an item the run asked for takes its full history from the new fetch, so an item that left the query scope gets its real latest state, and an item that returns no rows (deleted or no longer readable) is dropped with one warning (count and first ids). Cached iterations that no revision uses any more (renamed or moved) are dropped; parent titles are merged by id; levels and the fetch time are replaced. A later run therefore still has the history of older sprints. An unreadable cache is replaced with a warning. `--offline` reports from the cache.
- Errors: a fetch that returns no revisions is an error ("the query returned no work items") and leaves the cache untouched. Query ids that return no revisions are a warning with their count (possible area-path permission gaps). HTTP errors show the first 300 characters of the response body. A response that is not JSON (often a sign-in page), a missing, unreadable or invalid cache (missing keys or fields, unknown state category) are errors with a next step.

The figures are computed by replaying revisions to fixed points in time (sprint start, end of day 1, sprint end), never from current values. That makes them idempotent: Azure DevOps's own velocity chart re-reads current data, which is why its past sprints can change.

Assumption to verify on the first real board: the field and navigation-property names and the `StateCategory` values on `WorkItemRevisions`. `init` checks every row before it writes anything.

## 5. Counting rules

Times: t0 = 24:00 local on the sprint's first day; t1 = 24:00 local on its last day, in the configured time zone.

| Rule | Decision |
|---|---|
| Sprint | A dated iteration no longer than 42 days (longer ones are releases or PIs). A team's sprints are those that held one of its counted, ours items in the history up to the sprint end. |
| What counts | One counting level per team (`count`): **stories**, the requirement backlog level (User Story, PBI, Issue, Requirement, plus any custom type the process puts there), or **tasks**, the task backlog level. The process configuration says which types sit at which level, so custom types need no mapping. A Bug the process does not list counts at the tasks level when its parent is a stories-level item, otherwise at the stories level. The level is taken at the sprint end, so later re-typing or re-parenting does not change an old sprint. Only that level counts for the team. Each item counts once. No roll-up between levels. |
| Ours | A counted item belongs to team T when a person with `team` = T in `team.csv` was its assignee while it was active (In Progress or Resolved) up to the sprint end. An item not yet active by the sprint end counts by its assignee at t0 or t1, so planned but not started work is in Y. At the stories level, the same test on one of its child items also counts. An item that is ours for two of our teams (at the same level) counts for both, and each team's DM list has a grey entry "#id is counted for teams A and B." |
| Wording | Reports always say **work items** ("11 of 14 work items delivered"), whatever the team counts. The counting level appears once per team, in small print beside its name ("counts tasks"), and in the method footnote. Teams are never added up or compared with each other. The trend uses the team's current level for all 6 sprints, so changing `count` rewrites the trend instead of breaking it. |
| Working | First entry into the InProgress or Resolved category. |
| Waiting | Time in a `waiting_states` state, or tagged `Blocked`. Reports call it "waiting (in a waiting state or blocked)" and never say who was waited on. |
| Done | First entry into the Completed category, unless the reason is in `not_delivered_reasons` (any case). Removed is never done. A reopened item keeps its first completion. |
| Planned (Y) | On the sprint's iteration at t0, not closed and not already done. |
| Delivered of planned (X) | Planned items done before t1. Headline: "X of Y". |
| Added | Entered the iteration after t0 and before t1, not planned. Counts only; who made the change is not reported. |
| Removed | Planned, not done by t1, and at t1 either on a path that is not a later sprint (backlog, area root, release) or closed with a not-delivered reason or in category Removed. Removed items leave the status denominator. |
| Carry-over | Planned, not done by t1, open, and at t1 still on the sprint's iteration or on a later sprint. For each: age (calendar days from first active to the sprint's last day), sprints carried (sprints up to and including this one where it was planned and not done), current state, and the reason and "agreed with PO" from notes. An item closed after the sprint end stays a carry-over of that sprint and is also listed as completed late. |
| Delivered in total | Every counted, ours item done within the sprint dates, planned or not. **A sprint gets credit for the items closed within its dates.** This is Azure DevOps's own rule ("the completion date determines which sprint gets velocity credit"). Planned items closed after the sprint end are "completed late" in the sprint they were planned in. |
| Cycle time | Done date minus first active date, plus 1, in calendar days. Median and 85th percentile (P85), nearest rank (`sorted(v)[ceil(p*n)-1]`), with n. When n < 10, also show the figure over the last 6 sprints. Ages and the status rule use the team's P85 over the last 6 sprints. |
| Waiting share | Of the delivered items' cycle time, the share spent waiting. |
| Reopened | Done, then active again within the sprint: listed, never counted twice. |
| Capacity | Working days (Mon-Fri) in the sprint from `joined` to before `left`, minus days off in `vacations.xlsx`, multiplied by allocation. Shown as context, never used to adjust figures. |
| Onboarding | A person is onboarding in the sprint containing their join date (or the next one) and the next `onboarding_sprints − 1` sprints. Their work counts; they are excluded from "nothing delivered" and "no item assigned" flags. |
| Rest of squad | The same figures for counted items under team T's members' main area paths (exact path) that are not ours, with the number of other engineers (on the children at the stories level). Not shown when there are none. |

## 6. Outputs

`.delivery-skills/<sprint>/manager_<YYYY-MM-DD>.html` and `dm_<YYYY-MM-DD>.html` (the run date), one pair per sprint with every squad in it. A rerun on the same day overwrites the pair. Each file is self-contained HTML (inline CSS, no scripts), so it can be emailed or printed. The header shows the sprint dates, the number of teams, "Board data as of <fetch time, in the configured zone>" and the generation time. `report --sprint S` for a sprint that has not ended by `--today` marks the title "(sprint still running)" and prints a warning.

**manager_ (executives, no people tables, no per-person figures)**, per squad:
- a status line, GREEN, AMBER or RED, with the reason in words:
  - GREEN: delivered at least 80% of planned (floored), not counting removed items, and no carry-over is both older than the squad's P85 and not agreed with the PO;
  - AMBER: one of the two conditions is missed;
  - RED: both are missed, or a work item has been waiting (in a waiting state or blocked) for more than 10 working days.
  When the window P85 is unknown (no finished items in the last 6 sprints), the second condition checks agreement only, for every carry-over, and the reason says "too few finished work items in the last 6 sprints to judge age".
  The rule is printed in the footnote as the operator's own convention, not an industry standard. The operator can override the colour with a reason in `notes.txt` (`ALFA status: AMBER - ...`), and the report shows that it was set by hand.
- what the team worked on, as the parent items with item counts, plus the summary and exec note from notes;
- delivered X of Y planned, delivered in total, added / removed, completed late;
- cycle time median and P85, and the waiting share;
- capacity line;
- carry-over table: ticket, title, age, sprints carried, now in, reason, agreed with PO;
- 6-sprint trend: X of Y, delivered in total, P85;
- footnote with the method.

Ticket titles and the DM's own text appear as written. After writing manager_, `report` scans it (ignoring case) for every roster full name as a whole word and every email and prints `warning: manager file mentions <name>` for each one found; the DM decides.

**dm_ (private)**: everything in manager_, plus:
- **Needs attention**, first on the page, after the DM's notes. Evaluated at the sprint end. Each entry names the item or person, the reason and the next question:
  - open item older than the squad's P85 cycle time (red when also carried 2 or more sprints);
  - carried 2 or more sprints;
  - carry-over with no reason in notes, or not agreed;
  - waiting for more than 3 working days ("Raise it with the PO.");
  - active with no board change for 5 working days (counting changes to children at the stories level);
  - an engineer with available days and **no item assigned** during the sprint (red);
  - an engineer with available days, past onboarding, and nothing delivered;
  - more than 3 items in progress for one engineer;
  - grey: closed without ever being active, reopened, an assignee email in a roster domain but not on the roster, an item counted for two of our teams.
- **Ours vs rest of squad:** engineers, delivered, cycle time median and P85, waiting share.
- **People:** available days, delivered, in progress, oldest open item and its age, median cycle time, items worked on.

Thresholds (P85, 2 sprints, 3 days, 10 days, 5 days, 3 items, 80%, 6 sprints, 42-day sprints) are fixed constants in `metrics.py`. The footnote states them as local conventions, not industry standards.

`report` prints every warning (config, calendar, notes, cache, names, running sprint), the written paths, `sprint: NAME`, `notes: PATH`, and per team the carry-overs without a note and the delivered items with their titles, for the agent.

## 7. Commands and skill

```
ledger init --query Q --staff staff.txt --vacations vacations.xlsx [--yes] [--org URL --project NAME --timezone ZONE]
ledger report [--sprint S] [--offline] [--today YYYY-MM-DD]   # default: the last ended sprint
ledger person EMAIL [--offline] [--today YYYY-MM-DD]          # one engineer over the team's last 6 sprints
```

One agent skill, `delivery-report`, runs the flow:
1. `init --yes` on first use, or when `staff.txt` changed. Show the DM the printed counting levels, waiting states, not-delivered and closure reasons, unmatched calendar names and warnings (such as emails never seen on the board), and correct `config.json` from their answers. Show `team.csv` and correct teams and join dates.
2. `report`. Show every warning.
3. Ask, for each team: "Anything to add for team T this sprint?" The answers go to `notes.txt` and appear in the dm_ report, and in manager_ only if the operator says so.
4. Ask the DM about each carry-over without a note and write the answers to `notes.txt`.
5. Write the per-squad summary from the delivered items' titles and have the DM confirm it.
6. `report` again. It reads the board again, so figures can change if the board changed; the DM's answers change the status and the flags.
7. Give the DM the paths.

The agent reads ticket titles to write the summary. The wording may differ between runs; on the same board history the numbers do not.

## 8. Build

- Python 3.9+ standard library only. Modules: `ado.py` (token, WIQL, OData paging, cache), `replay.py` (item state at a point in time), `metrics.py` (counting rules, flags, status), `render.py` (HTML pages and the `report` and `person` commands), `setup.py` (`init`), `xlsx.py` (team calendar), `config.py` (config.json, team.csv, staff.txt, notes.txt), `cli.py`. At most 2,400 lines under `ledger/`, enforced by a test.
- Tests run against an invented fixture of revision rows covering both squad styles, carry-overs, moves to the next sprint, reopen, late close, mid-sprint add and remove, and leave. No test touches the network or `az`.
- The v2 code is removed from `main` and stays in git history.

## 9. Known limits

- Every figure depends on the board being kept up to date. The hygiene triggers show where it isn't.
- "Ours" depends on assignee history. Pair work without an assignee change is invisible.
- Waiting time is only as good as the waiting states and the `Blocked` tag the squads actually use.
- The rest-of-squad comparison mixes different kinds of work. The DM report says so next to the table.
- On the first run, items not changed within `history_days` (and not assigned to our people in that time) are not fetched; once fetched, an item stays in the cache until it is deleted or no longer readable.
- Per-person reporting may fall under local labour law and works-council rules. The operator checks.

## 10. Decisions

Decided on 2026-11-13: the squad-vs-rest comparison stays in dm_ only; manager_ has a RAG status line; `vacations.xlsx` is the team calendar layout (months side by side under merged title cells, day numbers in one header row, `Name` and `Squad` columns), with letter codes and public holidays marked per person. The final review added: carry-over versus removed by where the item is at the sprint end; teams from `team.csv`; a cache that keeps older items and refetches them; a names warning for manager_; a code cap of about 2,400 lines.
