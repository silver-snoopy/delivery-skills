# Delivery report v3: design

Status: draft for approval, 2026-11-13. Replaces the v2 Delivery Ledger design (still in git history). Research behind it: `research/Sprint delivery reporting for ADO.md` (git-ignored).

## 1. Why

A delivery manager (DM) supplies engineers to a customer's Scrum teams ("squads") on Azure DevOps. The customer says two things: **"you are slow"** and **"you are not delivering"**. The DM is not in every daily and does not micromanage 20+ people. After each sprint the DM wants:

1. A one-page report for executives that shows, per squad, what the team delivered and why anything did not land. No engineer names.
2. A private DM report that says where to intervene, with per-person facts.

Anyone in the DM role must be able to reuse it on any project with Azure DevOps Services, with nothing installed in the customer's organisation.

## 2. Goals, non-goals, anti-goals

Goals
- Answer five questions from board data:
  - Did we deliver what was planned?
  - How much do we deliver per sprint?
  - Are we slow, and if so is it our work or waiting on the customer?
  - What is carried over, for how long, and why?
  - Who needs a conversation?
- The same board history always gives the same numbers. Run it as often as you like.
- Industry-standard definitions (Azure DevOps velocity, Scrum Guide, flow metrics), stated on the report.
- Set up a new project in about 15 minutes.

Non-goals
- No editions, locking, freezing, approval steps, risk register or names-check machinery.
- No story points, no DORA, no forecasting.
- No dashboards hosted anywhere. The reports are files the DM sends themselves.

Anti-goals: this fails even if it works when
- it ranks people or puts per-person figures in the executive report,
- a number changes between two runs on the same data,
- setup asks the operator more than each person's team and join date,
- the tool grows past about 2,000 lines of code.

## 3. Setup

The operator provides three things. The tool does the rest and asks only what it cannot find.

| Input | Form |
|---|---|
| The query | A saved query URL or id, a WIQL text, or a root area path (`Project\Claims`). It should cover the work of the operator's people. |
| `staff.txt` | The operator's people, as copied from a mail distribution list: `Name <email>;` (one or many per line). |
| `vacations.xlsx` | The team calendar: days off per person, public holidays included; one row per employee, one column per day, months side by side. |

```
ledger init --query "<query>" --staff staff.txt --vacations vacations.xlsx
```

What `init` does:
1. Checks access to Azure DevOps with the operator's `az login`.
2. Reads the query's items over the last 6 sprints, with their revisions. From those it finds each staff member's area paths and item count, the sprints and their dates, and every state in use.
3. Asks, each time with a suggestion. For each person in `staff.txt`:
   - **Team?** Suggested from the person's main area path, or from the team already chosen for a colleague in the same area path. Teams are the operator's own grouping of people, not Azure DevOps teams. One area path can hold several teams.
   - **Joined the team on?** Suggested from the person's first activity on the board.
   People with no tickets at all are asked too. They stay on the team and show up in the report as having capacity but no assigned work.
   Then, once per team: **Count stories or tasks?** Suggested from how the team works on the board: tasks when most of the team's sprint work is tasks that are planned into sprints and closed one by one, stories otherwise. The suggestion comes with the numbers behind it (for example "last 6 sprints: 212 tasks, 31 stories closed").
4. Writes `.delivery-skills/team.csv` and `config.json`, and prints a summary:

```
name,email,team,joined,allocation
John Doe,john.doe@vendor.example,ALFA,2025-03-03,1
Maya R.,maya.r@vendor.example,ALFA,2026-10-12,1
Tom H.,tom.h@vendor.example,ALFA,2024-11-04,0.5
```

- `allocation` (default 1) is optional; `init` asks for it only if the operator wants to set it.
- Running `init` again with a new `staff.txt` asks only about new people and keeps every earlier answer. People who left are marked with a `left` date, not deleted, so earlier sprints still report correctly.
- Two emails for one person: two lines in `staff.txt` with the same name.
- Everyone else who appears on the board (customer staff, other vendors) is never listed. They form the "rest of squad" for the area paths our teams work in.

`config.json` is written by `init`. The operator normally only reviews it.

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
  "not_delivered_reasons": ["Removed", "Won't Fix", "Cut", "Duplicate"]
}
```

- `organization`, `project` and `timezone` come from the `az` defaults and the machine.
- `waiting_states` is proposed from state names (review, QA, test, accept, wait, blocked). `init` prints every state so the operator can correct the list.
- `onboarding_sprints`: delivery is expected from the third sprint after a person's join date. In their first two sprints a person is shown as "onboarding" and never triggers a "nothing delivered" flag.
- Sprint dates and state categories (Proposed, In Progress, Completed, Removed) are read from Azure DevOps on every run, never configured.

`vacations.xlsx` is read again on every `report`, so the operator can just replace the file. Layout, as in the operator's team calendar: one sheet with several months side by side. A title row holds merged cells that name each month ("November 2026") over its day columns. The header row below holds the labels of the person columns (one of them `Name`, optionally `Squad`) followed by day numbers that run on across months (… 30, 31, 1, 2, …). One row per employee. Any non-empty day cell is a day off (letter codes such as V, S, H); public holidays are marked in each person's row the same way, so no separate holiday list exists. Weekend shading is ignored (weekends never count as working days). Codes listed in `half_day_codes` (default none) count as half a day. Rows are matched to `staff.txt` by name, ignoring case, accents and the order of first and last name; `init` asks about any name it cannot match and remembers the answer. Rows for people not in `staff.txt` are ignored. When a `Squad` column exists, `init` uses it as the first team suggestion. The file is read with the standard library (`zipfile` and XML), with no Excel package.

Per-sprint notes, written by the DM or by the agent from the DM's answers: `.delivery-skills/<sprint>/notes.txt`, plain text.

```
#48211 agreed: QA environment down 3-5 Nov, PO moved acceptance to S30
#48277 unknown: dependency on payments API team
Alfa summary: The team finished document upload and validation for claims intake ...
```

## 4. Data source

Azure DevOps **Analytics OData**, entity `WorkItemRevisions`, read with the DM's own `az login` token (`az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798`). This needs the Reader role ("View analytics") and no extension install.

- One query per run: every revision since the start of the trend window (6 sprints) of the items the setup query returns, plus their child tasks, plus anything ever assigned to a person in `team.csv`.
- Fields: `WorkItemId, Revision, ChangedDate, WorkItemType, State, StateCategory, Reason, IterationPath, AreaPath, AssignedTo/UserEmail, ParentWorkItemId, Title, TagNames`.
- Sprint dates come from `Iterations`. The Requirements backlog level comes from the project's process (work item types in the Requirement category).
- Each run writes the raw rows to `.delivery-skills/cache/revisions.json`. `--offline` reports from the cache.

The figures are computed by replaying revisions to fixed points in time (sprint start, sprint end), never from current values. That makes them idempotent: Azure DevOps's own velocity chart re-reads current data, which is why its past sprints can change.

Assumption to verify on the first real board: field names and the `StateCategory` values on `WorkItemRevisions`. `init` verifies this before it writes anything.

## 5. Counting rules

| Rule | Decision |
|---|---|
| What counts | One counting level per team, chosen once at setup (`count` in `config.json`): **stories**, meaning the Requirements backlog level (User Story, PBI, Issue, Requirement, plus any custom type the process puts at that level, such as Spike, and Bug where the team tracks bugs as requirements), or **tasks**, meaning the Task level (Task, plus Bug where bugs are tracked as tasks). The process configuration says which types sit at which level, so custom types need no mapping. Only that level counts for the team. Each item counts once. No roll-up between levels. |
| Ours | A counted item belongs to team T when a person with `team` = T in `team.csv` was its assignee at any point while it was In Progress. At the stories level, being assignee of one of its child tasks also counts, so stories-only and stories-with-tasks teams both work. An item worked on by two of our teams counts for both and is marked. |
| Wording | Reports always say **work items** ("11 of 14 work items delivered"), whatever the team counts. The counting level appears once per team, in small print under its name ("counts tasks"), and in the method footnote. Teams are never added up or compared with each other. The trend uses the team's current level for all 6 sprints, so changing `count` rewrites the trend instead of breaking it. |
| Working | First entry into an In Progress state category. |
| Waiting | Time in a `waiting_states` state, or tagged `Blocked`. |
| Done | Entry into the Completed category, unless the reason is in `not_delivered_reasons`. Removed is never done. |
| Planned (Y) | In the sprint's iteration at the end of sprint day 1 and not already done. |
| Delivered of planned (X) | Planned items done on or before the sprint's last day. Headline: "X of Y". |
| Added / removed | Entered the iteration after day 1 / left it before the end without being done. Counts only; who made the change is not reported. |
| Delivered in total | Every counted item done within the sprint dates, planned or not. **A sprint gets credit for the items closed within its dates.** This is Azure DevOps's own rule ("the completion date determines which sprint gets velocity credit"). Items closed after the sprint end are "completed late" in the sprint they were planned in. |
| Cycle time | Done date minus first In Progress date, plus 1, in calendar days. Median and 85th percentile (P85) with n. When n < 10, also show the 6-sprint figure. |
| Waiting share | Of the delivered items' cycle time, the share spent in waiting states. |
| Carry-over | Planned, not done by the sprint end, still open. For each: age (days since first In Progress), sprints carried (earlier sprints where it was planned and not done), current state, and the reason and "agreed with PO" from notes. |
| Reopened | Done, then reopened: listed, never counted twice. |
| Capacity | Working days in the sprint on or after the join date, minus days off in `vacations.xlsx`, multiplied by allocation. Shown as context, never used to adjust figures. |
| Onboarding | A person is onboarding in the sprint containing their join date and the next `onboarding_sprints − 1` sprints. Their work counts; they are excluded from "nothing delivered" and "no assigned work" flags. |
| Rest of squad | The same figures for counted items in team T's area paths (the members' main area paths) that no one of ours worked on, with the number of other assignees. Not shown when there are none. |

## 6. Outputs

`.delivery-skills/<sprint>/manager_<YYYY-MM-DD>.html` and `dm_<YYYY-MM-DD>.html`, one pair per sprint with every squad in it. A rerun on the same day overwrites the pair. Each file is self-contained HTML (inline CSS, no scripts), so it can be emailed or printed.

**manager_ (executives, no names)**, per squad:
- a status line, GREEN, AMBER or RED, with the reason in words:
  - GREEN: delivered at least 80% of planned, not counting removed items, and no carry-over is both older than the squad's P85 and not agreed with the PO;
  - AMBER: one of the two conditions is missed;
  - RED: both are missed, or an item has waited on the customer for more than 10 working days.
  The rule is printed in the footnote as the operator's own convention, not an industry standard. The operator can override the colour with a reason in `notes.txt` (`ALFA status: AMBER - ...`), and the report shows that it was set by hand.
- what the team worked on, as the parent features with item counts plus the summary from notes;
- delivered X of Y planned, added, removed, delivered in total, completed late;
- cycle time median and P85, and the waiting share;
- capacity line;
- carry-over table: ticket, title, age, sprints carried, now in, reason, agreed with PO;
- 6-sprint trend: X of Y, delivered in total, P85;
- footnote with the method.

**dm_ (private)**: everything in manager_, plus:
- **Needs attention**, first on the page. Each trigger names the item or person and the reason:
  - open item older than the squad's P85 cycle time;
  - carried 2 or more sprints;
  - carry-over with no reason in notes, or not agreed;
  - waiting on the customer for more than 3 working days (raise with the PO);
  - In Progress with no board change for 5 working days;
  - an engineer with available days and **no item assigned** during the sprint;
  - an engineer with available days, past onboarding, and nothing delivered;
  - more than 3 items in progress for one engineer;
  - board hygiene: closed without ever being In Progress, or an assignee email not on the roster.
- **Ours vs rest of squad:** delivered, cycle time median and P85, waiting share.
- **People:** available days, delivered, in progress, oldest open item and its age, median cycle time, items worked on.

Thresholds (P85, 2 sprints, 3 days, 5 days, 3 items) are fixed defaults. The footnote states them as local conventions, not industry standards.

## 7. Commands and skill

```
ledger init --query Q --staff staff.txt --vacations vacations.xlsx   # asks team and join date per person
ledger report [--sprint S] # default: the last closed sprint; writes the two files
ledger person EMAIL        # one engineer over the last 6 sprints -> person_<name>_<date>.html
```

One agent skill, `delivery-report`, runs the flow:
1. `init` on first use, or when `staff.txt` changed.
2. Before the report, ask the operator, for each team they own: "Anything to add for team T this sprint?" (for example, someone lent to another project, a support rotation, an outage). The answers go to `notes.txt` and appear in the dm_ report, and in manager_ only if the operator says so.
3. `report`.
4. Ask the DM about each carry-over without a note and write the answers to `notes.txt`.
5. Write the per-squad summary from the delivered items' titles.
6. `report` again.
7. Give the DM the two paths.

The agent reads ticket titles to write the summary. The wording may differ between runs; the numbers never do.

## 8. Build

- Python 3.9+ standard library only. Modules: `ado.py` (token, OData paging), `replay.py` (item state at a point in time), `metrics.py`, `render.py` (two HTML templates), `cli.py`, `config.py`. Target 1,500 lines plus tests.
- Tests run against an invented fixture of OData rows covering both squad styles, carry-overs, reopen, late close, mid-sprint add and remove, and leave.
- The v2 code is removed from `main` and stays in git history.

## 9. Known limits

- Every figure depends on the board being kept up to date. The hygiene triggers show where it isn't.
- "Ours" depends on assignee history. Pair work without an assignee change is invisible.
- Waiting time is only as good as the waiting states and the `Blocked` tag the squads actually use.
- The rest-of-squad comparison mixes different kinds of work. The DM report says so next to the table.
- Per-person reporting may fall under local labour law and works-council rules. The operator checks.

## 10. Decisions

None. Decided on 2026-11-13: the squad-vs-rest comparison stays in dm_ only; manager_ has a RAG status line; `vacations.xlsx` is the team calendar layout (months side by side under merged title cells, day numbers in one header row, `Name` and `Squad` columns), with letter codes and public holidays marked per person.
