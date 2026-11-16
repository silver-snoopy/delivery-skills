# Trial findings, 2026-11-15

A trial of v3 against a real Azure DevOps board (7 teams, 16 engineers) found the problems below. Names of the customer, teams, people and work items are left out on purpose.

| # | Problem in v3 | Required behaviour |
|---|---|---|
| 1 | `init` stalled for more than 5 minutes on Analytics OData (`WorkItemRevisions`, `Revisions/any`). | Fetch through the work item REST APIs, with the same cache rows. In the trial, `init` then took about 90 seconds. |
| 2 | Delivery was credited by close date: items on one sprint closed on the next sprint's first day counted in the next sprint. | Credit the sprint whose iteration the item was on when it closed. Flag it "late" if it closed after the sprint ended. |
| 3 | `report` used one sprint name; teams with differently named sprints printed "No sprint named ...". | Without `--sprint`, each team gets its own last ended sprint. |
| 4 | Staff names such as `Name Surname (external)` never matched the calendar. | Drop a trailing `(tag)` from names. |
| 5 | Calendar squads used short codes, not team names. | Add a `squad_teams` mapping. |
| 6 | `count` was only `stories` or `tasks`; Bug sat on neither backlog level. | `count` may also be an explicit list of work item types. |
| 7 | One dropped connection (`RemoteDisconnected`) killed the run. | Retry connection errors twice, then raise `AdoError`. |
| 8 | A team with no sprint read "No sprint named ". | Give that team its own card that says why. |
| 9 | "Planned" counted unassigned work on the sprint path, which inflated carry-over. | Planned = on the sprint and assigned to one of the team at the end of day 1. If the item becomes theirs later, it counts as added. |
| 10 | The manager page duplicated the DM card. | A dedicated executive card: highlights, stats, foldable panels, no RAG status. |
| 11 | There was no visibility of blocked time. | Add On Hold days (context only, never affects figures). |
| 12 | `#ID`s were plain text. | Link `#ID`s to Azure DevOps. |
| 13 | The roster-hygiene flag was noisy. | Remove the grey hygiene attention flag. |

## Worked example

An item on a sprint first appeared on the sprint, unassigned, a few days before its end. It was assigned to one of our engineers the next day and moved to the next sprint the day after. It was not planned (not ours at the end of day 1), so it counts as **added**, not carry-over. This is intended, and `test_reassigned_to_us_mid_sprint_moved_on_is_added_not_planned_or_carry` covers it.

## Open questions about the data

The trial's own list of open data questions was not carried over to this repository. Still to confirm on each new board:

- whether staff sign-in emails match the assignee `uniqueName` on the board (`init` warns about emails it never sees);
- which states mean "on hold" (`init` proposes `hold_states`; the operator confirms);
- whether a team's work fits `stories` or `tasks`, or needs a type list;
- whether calendar month titles are stored as text (date serials are skipped).
