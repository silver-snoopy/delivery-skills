# Delivery Ledger: design

Date: 2026-11-11
Version: 2 (revised after a builder check and an adversarial review)
Status: draft for the delivery manager's review
Mockup of the main output: `handout-mockup.html` in this folder (invented data; open it in a browser)

The mockup is layout input only. Where its numbers, labels or markup differ from this document, this document wins (see section 15.4).

Section 22 lists the decisions made by the designer rather than by the delivery manager.

---

## 1. Intent

A delivery manager (DM) supplies engineers to a customer under staff augmentation. The engineers work on the customer's shared Azure DevOps (ADO) boards in mixed squads of vendor engineers, engineers from other vendors and customer employees. The DM does not own the backlog, the breakdown or the QA queue. They own the people.

The DM's job is to anticipate risks and issues that could affect delivery or timeline. After sprints close they want to send the customer a delivery health report covering the vendor's contribution to the total. They also need to answer two kinds of accusation with evidence:

- "Person X is not delivering."
- "Your people are slow" or "your people's quality is poor."

This is hard today because:

- Tickets are not estimated. There are no story points.
- Ticket hygiene is poor. Fields people are supposed to fill in are often empty or wrong.
- Standard ADO views cannot separate the vendor's people from the rest of the squad, and they miss work an engineer finished and handed to customer QA.

The Delivery Ledger is a deterministic tool plus a set of agent skills. It builds a sprint-by-sprint record from the board's own change history and turns it into an executive report with no names, a delivery manager report with per-person observations, and on-demand evidence briefs.

## 2. What the tool can and cannot know

This section governs the wording of every output.

The board records **what was set, by whom and when**: state, board column, assignee, sprint, and text edits. It does not record effort, who actually did the work, or why something happened. A scrum master may update five tickets on Friday for work finished on Wednesday.

Therefore:

- The tool reports **observations** ("in progress with a vendor engineer for 9 working days") and **which rules they triggered**. It does not state verdicts about a person. The DM states the conclusions.
- Durations are recorded durations, not labour time.
- Reasons (why a ticket came back, whether a carryover was agreed) are the DM's assertions, recorded as such and kept separate from board observations.
- Known limits are printed in a short footnote on every output: tickets are not sized; splitting or cloning a ticket cannot be detected; durations reflect when the board was updated.

## 3. Users and moments of use

One user: the DM. Three moments:

1. **Once, at setup.** Describe the roster and teams, check access, confirm how board states map to phases, check the tool's reading of a few tickets the DM knows well.
2. **After sprints close.** Run one skill. Answer a few questions about carried-over and returned tickets. Review the drafts. Send the executive report themselves.
3. **When challenged.** Ask for an evidence brief on a person or a team over the last N sprints.

## 4. Expected scale and shape

The design must suit a portfolio like this (illustrative, from the DM's current engagement):

- About 8 teams and about 16 vendor engineers. Teams have 1 to 4 vendor engineers; several have exactly one.
- All teams run two-week sprints. Most share the same sprint dates but number their sprints differently. At least one team is offset by a week.
- People join, leave and move between teams often, sometimes several changes in a month.

Consequences: figures are small counts, so they are shown as "2 of 3" rather than percentages; a one-person team's figures are that person's figures; roster assignments must be dated.

## 5. Goals

- G1. An executive report, with no names in it, showing per team the vendor team's contribution against the squad total, what was delivered, what is finished and awaiting acceptance, what was carried over, and risks with asks.
- G2. A delivery manager report: the same, plus per-person observations for each engineer.
- G3. Per person: what they closed and handed off over time; whether they are carrying an item for more than 2 sprints; whether their tickets move backward repeatedly.
- G4. Separate what the vendor's people control from what they do not: time with the vendor engineer versus time waiting on others, the state of work when it reached them, days with nothing in progress.
- G5. A ticket-level trace behind every figure, so any number can be explained on the spot.
- G6. An immutable record of each report as issued.
- G7. Rule-based risk candidates with ticket evidence for the DM to confirm.
- G8. Runs the same way under GitHub Copilot in VS Code, Copilot CLI and Claude Code, and by hand with no agent.
- G9. Publishable as open source with no customer or vendor specifics in the repository.

## 6. Non-goals

- Story points or any estimate-based metric.
- Writing to the ADO board. The tool is read-only.
- Sending the report.
- Measuring customer or other-vendor individuals.
- A hosted service, database or web app.
- Reproducing any externally agreed KPI framework. The DM chose to use this design's own figures.
- Detecting ticket splits and clones.
- Source-control evidence (commits and builds). It is a later phase with its own design (section 20).

## 7. Anti-goals

- **A league table.** No ranking of people or teams against each other. Each is compared only with its own history.
- **A composite score.** No 0–100 health number and no ratio of vendor output to headcount.
- **Verdicts about people.** See section 2.
- **Customer staff being profiled.** Non-vendor people are pseudonymised at collection (section 9.7).
- **Figures the DM cannot trace** to tickets and board changes.
- **The tool sending data anywhere** other than reading from the configured ADO organisation, or real data appearing in the repository.

## 8. Constraints

- **Two machines.** Development happens where there is no ADO access. The tool runs on a customer machine: Windows with WSL; code, `az` CLI (with the `azure-devops` extension, logged in), Python 3 and Node all inside WSL. Nothing else can be assumed installable.
- **Python 3.9+, standard library only.** No `pip install`. Config files are JSON.
- **Blind development.** Every ADO behaviour relied on is an assumption until checked on the customer machine (sections 10 and 21).
- **Offline outputs.** Each HTML output is one self-contained file: no external fonts, scripts or images.
- **Open source.** No company, customer or team names and no real ticket data in the repo. The research handoff in the DM's other repository contains real names and must not be copied in.

## 9. How it works

### 9.1 Stages

```
probe ─> setup ─> trace check
                      │
collect ─> analyze ─> annotate ─> narrate ─> report ─> issue
(network)  (pure)     (DM)        (agent,    (render)  (freeze)
                                   optional)
```

`probe` and `collect` are the only stages that use the network. `analyze` is a pure function of the collected snapshot, the config and the roster. `annotate` and `narrate` add DM and agent input that is stored separately and never alters what `analyze` computed.

### 9.2 Team sprints and reports

These are two different things.

- A **team sprint** is one team's own iteration with its own name and dates. Each team is always measured on its own sprints. A team sprint is collected and analysed once it has ended.
- A **report** is a handout issued on a date. It includes, for each team, that team's most recently ended sprint. Teams are grouped by identical sprint dates. Each team's row prints its own sprint name and dates.

Totals are shown per date group, never across groups with different dates. The summary paragraph and the risks list cover all teams.

If a team's latest sprint was already included in an earlier report, its row is marked "unchanged since the last report".

Trends are per team, over that team's own sprints.

### 9.3 Data home

All real data lives outside the repository in a data home, default `~/delivery-ledger-data/`, overridable with `--home` or `DELIVERY_LEDGER_HOME`. It must be on the Linux filesystem, not under `/mnt/`, because Windows-mounted paths do not enforce Linux permissions; the tool refuses otherwise. Directories are `0700`, files `0600`.

```
<home>/
  config.json
  roster.json
  secret.key                      # pseudonym key (9.7)
  probe/                          # full local report + export
  inventory.json                  # known unfinished tickets (9.5)
  teams/<team>/sprints/<sprint-id>/
    snapshot/                     # collected history
    metrics.json                  # what analyze computed
    ledger.json                   # per-figure ticket lists (11)
    annotations.json
    narrative.json
  reports/<date>/
    risks.json
    draft/                        # handouts being reviewed
    editions/001/, 002/, …        # issued, immutable (16)
  evidence/<date>-<subject>.html
```

### 9.4 Command line

One launcher, `bin/ledger`, which works from any directory, plus `python3 -m ledger`. Commands: `probe`, `setup-check`, `trace`, `collect`, `analyze`, `explain`, `annotate`, `narrative-validate`, `risks`, `report`, `names-check`, `issue`, `evidence`, `status`.

Every command prints a short human summary and, with `--json`, a machine-readable result. On failure it exits non-zero with a one-line cause and a suggested next step. The order of steps and all validation are enforced by the CLI, not by the skills.

### 9.5 Config and roster

`config.json`:

- `ado`: organisation URL, project.
- `timezone`: IANA name.
- `teams[]`: `name`, `ado_team` (for the sprint calendar and board), `area_paths[]`. Area paths of different teams must not overlap; the tool rejects a config where they do.
- `unit_level`: `stories` or `tasks` (section 12.3), and `unit_types`: the work item types counted at that level. Default level `stories`, types User Story, Product Backlog Item, Bug.
- `phases`: per team board, an ordered list of rules mapping a state, and optionally a board column and its done/doing half, to `todo`, `doing`, `handoff`, `done` or `removed`, with an optional `wait_label` for handoff ("QA", "review", "sign-off"). First match wins.
- `not_delivered`: state and reason values that mean a ticket was closed without being delivered (duplicate, won't fix, cut, obsolete and similar).
- `service_identities[]`: automation accounts whose changes are not treated as a person's action.
- `holidays[]`.
- `thresholds`: every number used by flags, status and risks, with defaults.
- `labels`: display name for the vendor team (default "Vendor team").
- `agent_text_access`: `full`, `titles` or `none` (section 14.2).

`roster.json`, one entry per vendor person:

- `id`, `display_name`
- `identity_ids[]`: the ADO account's permanent identifiers, resolved at setup
- `aliases[]`: every login name and display name the person has had, for querying and as a fallback match
- `assignments[]`: `team`, `from`, `to` (open-ended allowed), `allocation` (share of full time, default 1.0). Allocations on overlapping dates must not total more than 1.0.
- `absences[]`: `from`, `to`, optional note

A person counts as vendor on a team only on dates inside an assignment to that team.

Optional role labels for non-vendor people ("Product owner", "Customer QA") are stored as a map from pseudonym to label, never from a real identity (section 9.7).

### 9.6 Collection

**Access.** The collector obtains a token with `az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798` and calls the ADO REST API with `urllib`. It records the authentication mode and the signed-in account for each collection, re-acquires the token before it expires, and never changes account mid-collection. A PAT in `AZURE_DEVOPS_EXT_PAT` is an alternative authentication mode chosen at setup, not an automatic fallback. If direct HTTPS does not work through the customer's proxy and certificates, the transport falls back to `az devops invoke`; that is a transport choice and does not change the account. Certificate validation is never disabled. Credentials are only sent to the configured organisation's host.

**Which tickets.** The coverage rule: every ticket that was a vendor ticket (12.2) at any time in the window must be collected, however long ago it last changed. Three sources are combined and de-duplicated:

1. Unfinished: tickets ever assigned to a roster identity that are not in a `done` or `removed` state now, with no date filter.
2. Recent: tickets ever assigned to a roster identity that changed since the window start.
3. The inventory of tickets seen unfinished in earlier collections.

"Ever assigned" uses WIQL `EVER` on the assignee, queried for each of a person's aliases.

**Complete results.** WIQL has a result limit and a time limit. Queries are partitioned by ticket ID range; a partition that returns a full batch, errors or times out is split further. Partition completion is recorded so collection can resume. A result is never assumed complete because it came back smaller than a requested size.

**History.** For each collected ticket, the full revision history. Kept per revision: revision number, changed date, changed-by, state, reason, board column and its done/doing half, iteration path, area path, assignee, work item type, project, parent, tags, and a hash and length of the normalised text of description and acceptance criteria. Full title and text are kept for the revision in effect at the sprint's end.

**Squad ledger.** For each team's area paths, a minimal history of every ticket at the counting level that was unfinished or changed in the window: ticket ID and, per revision, date, state, reason, board column, area, iteration, type. No identities and no text. Squad figures are derived from this with the same functions used for vendor tickets, so the vendor figure is by construction a subset of the squad figure.

**Board and state metadata.** Each team's board columns (including split columns) and each work item type's states and categories, used to propose the phase mapping.

**Protocol.** Bounded concurrency; pacing; retry with backoff and jitter; obeying the server's `Retry-After` on both failures and throttled successes; checkpoints per ticket and per page so an interrupted ticket is never marked complete; a single cut-off timestamp for the whole collection so vendor and squad data describe the same moment. The builder benchmarks the per-ticket revisions endpoint against the reporting revisions feed during phase 1 and chooses.

**Access gaps.** Tickets that are missing, deleted or not readable are recorded as such and counted in a coverage note shown on the delivery manager report. A report states the scope the signed-in account could read.

### 9.7 Pseudonymising non-vendor people

Before anything is written to disk, every identity not in the roster is replaced by `other-<32 hex>`: an HMAC-SHA256 of the account's permanent identifier with a random key generated once and kept in the data home, truncated to 128 bits and checked for collisions. The same person keeps the same pseudonym across sprints. No list of real identities is stored. This is pseudonymisation, not anonymity: someone who knows a ticket's history can work out who a pseudonym is. Losing the key breaks continuity with earlier data; the tool says so and offers no rotation.

## 10. Checking assumptions on the customer machine

### 10.1 Probe

`probe` makes a small number of read-only calls and records, for each assumption in section 21, what it observed and on how large a sample: `observed`, `failed` or `not checked`. One easy ticket does not confirm paging, so the probe forces small page sizes, and looks for tickets with long histories, column changes, type changes and parent changes.

It also checks the environment: Python version; that the configured timezone loads; `az` login and token expiry information; both transports through the real proxy and certificate setup; that the data home is on a Linux filesystem with effective permissions; rate-limit headers under a short realistic burst; and a timing benchmark of both history endpoints.

The probe writes a full local report and a separate **export**. The export is built from a strict allowlist: assumption results, counts, timings, ADO categories, field reference names of standard fields, and response shapes. Customer-defined values (state names, custom field names, team and area names, URLs, error text, identity values) are replaced by consistent placeholders. Only the export leaves the machine, after the DM has read it.

### 10.2 Trace check

`trace <ticket-id>` prints the tool's reading of one ticket: its timeline of phases, holders and sprints, and how it was classified in each sprint. At setup the DM picks five to ten tickets they know well, covering a handoff to QA, a long-carried ticket, a returned ticket and a reopened one where available, and compares each trace with the board. Mismatches are fixed in the phase mapping or reported back as defects. This is the main protection against assumptions that synthetic tests would simply repeat.

## 11. Explaining every figure

`analyze` writes, beside `metrics.json`, a `ledger.json` that lists for every figure the ticket IDs and board events behind it. `explain <team> <sprint> <figure>` prints that list. The delivery manager report links each figure to its list. No figure may appear in any output without a ledger entry.

## 12. The derived model

### 12.1 Time

- Timestamps are kept at full precision. Intervals are half-open: start included, end excluded.
- Sprint and day boundaries are evaluated in the configured timezone. A sprint runs from the start of its first day to the end of its last day.
- A **working day** is a Monday to Friday that is not in `holidays`. Durations count only time that falls on working days, in fractions of a day, and are displayed to the nearest half day.
- Events on non-working days are kept and shown as events; their time is not counted. Outputs never imply that no work happened on a non-working day.

### 12.2 Phases, timeline, vendor tickets

**Phases** have a rank: `todo` 0, `doing` 1, `handoff` 2, `done` 3. `handoff` means the engineer's part is finished and someone else must act. A phase change is detected whenever the mapped phase changes, whether the state or only the board column changed.

**Timeline.** Each ticket's revisions are folded into intervals. An interval has a phase, a holder (a vendor person, a pseudonym, or none), the sprint it was in, and the team its area path belonged to. A new interval starts when any of these changes, or when the holder's roster status changes.

**Administrative changes.** A change made by a service identity, a change of project or work item type, and a bulk edit (one account changing many tickets within a minute) are marked administrative. They still move the timeline, but they are not counted as backward moves or returns, and they are labelled in traces.

**Vendor ticket.** A ticket is a vendor ticket for a team sprint if, on dates inside the person's assignment to that team:

- a vendor person held it in `doing` at any time up to the sprint's end and it was not `done` before the sprint started; or
- a vendor person was its assignee during the sprint while it was in `todo` (reported only as "assigned, not started"; such a ticket never counts as vendor-delivered).

Vendor tickets under no configured area path are listed in the delivery manager report under "Outside configured teams".

### 12.3 Counting level

Boards differ: on some, engineers are assigned stories; on others, stories sit with the product owner and engineers hold child tasks. `unit_level` picks one level for all counting and timing, chosen at setup from what the probe shows. At `tasks` level, each task's parent story at the time is recorded so narrative can group by story. Mixing levels, or crediting a story through its tasks, is not supported in this version.

### 12.4 Where ticket time went

For each vendor ticket, time from first vendor involvement is classified, exhaustively and exclusively:

| Phase | Holder | Bucket |
|---|---|---|
| `doing` | vendor person | In progress with a vendor engineer |
| `doing` | non-vendor | In progress with another party |
| `doing` | none | Unassigned |
| `handoff` | any | Waiting, by `wait_label` |
| `todo` after having started | any | Parked |
| `done` | any | Closed (excluded from the split) |

A team sprint's split uses only time inside that sprint's dates. Whole-life durations are computed separately and used only on evidence cards. The implementation asserts that bucket durations add up to the classified time.

### 12.5 Sprint membership and planning

A ticket is a member of a sprint if its iteration path was that sprint's path at any time during the sprint.

- **Planned:** a member by the end of the first working day (configurable).
- **Added mid-sprint:** became a member later.
- **Active outside a sprint:** a vendor ticket in `doing` that was not a member of any sprint during the period. Reported as its own line.

Planned does not mean the vendor chose it. A vendor engineer working in a customer's team has no say over which tickets enter a sprint, so every report states that sprint scope is set by the customer and shows who decided it, from the `ChangedBy` of the board's own record:

- **Placed by:** for each vendor ticket that was a member, the party behind the revision that put it on the sprint's path (the start of the first run on that path in force during the sprint).
- **Taken out by:** for each ticket that left the sprint (12.6), the party behind the last revision before the sprint's end that moved it off the path.
- **Party:** another party (any identity not on the roster), vendor engineers (a roster person), automation (a configured service identity) or not recorded. Never a person.
- **Scope figures:** tickets with vendor engineers at the start (members by the planned cut, worked on or only assigned), added after the start, and taken out before the end, each split by party.
- **Load at the start:** tickets with vendor engineers at the start against the median delivered in the team's previous sprints (up to the six-sprint window; at least 3 needed). Above usual when at least 1.5 times that median and at least 2 more.

### 12.6 Outcome at sprint end

Each vendor ticket has exactly one outcome per team sprint, from its phase at the sprint's end:

| Outcome | Rule |
|---|---|
| **Delivered** | first sprint at whose end the ticket is `done` and not a `not_delivered` closure |
| **Closed, not delivered** | `done` with a `not_delivered` state or reason |
| **Finished, awaiting acceptance** | `handoff` |
| **Carried over, in progress** | `doing` |
| **Carried over, not started** | `todo`, was a sprint member |
| **Re-closed** | `done` again after having been delivered in an earlier sprint and reopened; not counted as delivered again |

**Planned work, one line per team sprint:** of the planned vendor tickets, how many were delivered, how many are finished and awaiting acceptance, how many carried over, and how many left the sprint. This line shows unfinished commitments honestly, including work that reached QA but was not accepted.

### 12.7 Carryover and waiting age

- `sprints_carried`: the number of ended sprints in which the ticket's outcome was carried over, counted from the first sprint in which a vendor person held it. Sprints before any vendor involvement are not counted.
- `sprints_waiting` and working days waiting: the same for the "finished, awaiting acceptance" outcome. These never raise a flag on a person.
- `carried_since` and the sprint trail.

### 12.8 Who worked on a ticket

There is no single owner or "primary credit". For each ticket the tool records every vendor person who held it in `doing` and for how long. At team level a delivered ticket counts once. At person level, "delivered, worked on" counts every delivered ticket the person held in `doing`; two people can both count the same ticket, and the table says so.

### 12.9 Backward moves and returns

- **Backward move:** a non-administrative phase change to a lower rank.
- **Return:** a backward move out of `handoff` or `done`.
- **Reopen:** a return out of `done`.

Two figures are kept apart: the number of return events in a sprint, and the number of distinct tickets returned. The ratio shown is for a matched group: of the tickets handed off in sprint k, how many were returned by the end of sprint k+1. For the latest sprint it is marked "so far". Returns are shown split by the DM's recorded reason: defect, requirement changed, other, not recorded.

### 12.10 State of work when received

Measured from the moment a vendor person first held the ticket, not from when anyone first started it:

- **No criteria at receipt:** acceptance criteria empty and description shorter than a threshold. Which fields count is configurable. Text is normalised (markup stripped, whitespace collapsed) before measuring.
- **Text changed after receipt:** number of later revisions where the normalised description or acceptance criteria changed, excluding service identities. This is "text changed", not "requirement changed"; the latter needs the DM's annotation.
- **Added mid-sprint.**
- **Assigned late:** first assigned to a vendor person with a threshold or fewer working days left in the sprint (default 3).

### 12.11 Person observations

Per person per team sprint, all limited to collected tickets and labelled as such:

- **Available days:** working days in the sprint inside their assignment, times allocation, minus absences.
- **Days with nothing in progress:** available days on which they held no ticket in `doing`. Shown with how many tickets they had assigned in `todo` at the time.
- **In progress at once:** average and maximum.
- **Pickup time:** working days from assignment to first entry to `doing`.
- **Recorded board changes:** distinct days on which they changed a collected ticket, and the last date.
- **Joiner:** for someone whose first assignment starts in the window, working days to their first delivered ticket.

A change in a person's allocation or team starts a new comparison period; trends are not drawn across it.

## 13. Figures, flags and status

### 13.1 Per team sprint

- Delivered by the vendor team, and the squad's total delivered.
- Planned work line (12.6).
- Sprint scope by party and load at the start (12.5).
- Finished and awaiting acceptance, with the longest wait.
- Carried over, split by the DM's answer: agreed in advance, not agreed, not yet answered.
- Returns (12.9).
- Median working days in progress with a vendor engineer per delivered ticket, and median elapsed working days from first `doing` to `done`.
- Where ticket time went (12.4).
- State of work when received (12.10).
- Available person-days against nominal (working days times allocation).
- Six-sprint trend of delivered.

### 13.2 Per person per team sprint (delivery manager report only)

Available days; delivered, worked on, with trend; handed off; in progress now with the age of the oldest; carried over with the longest `sprints_carried`; backward moves; returns; median days in progress; days with nothing in progress; last recorded board change; flags.

### 13.3 Person flags (delivery manager report only)

Every default below is configurable.

| Flag | Default rule |
|---|---|
| Carried over more than 2 sprints | a ticket carried over in progress with `sprints_carried` ≥ 3 |
| Ticket returned repeatedly | a ticket with ≥ 2 returns |
| Ticket moving backward | a ticket with ≥ 2 backward moves |
| Fewer delivered than usual | delivered below own trailing 3-sprint average for 3 sprints running, within one comparison period |
| Nothing delivered or handed off | for 2 sprints running, with ≥ 50% availability |
| No recorded board changes | for ≥ 5 available days |

Context is always printed beside flags: leave, joiner, days with nothing in progress, share of time waiting on others, work added mid-sprint, the DM's annotations. A person with no flags is shown as "No flags raised". There is no positive or negative verdict label and no score.

### 13.4 Team status

Three rules are evaluated per team sprint:

| Rule | Default |
|---|---|
| Chronic carryover | at least 1 ticket carried over in progress with `sprints_carried` ≥ 3 |
| Returns | at least 2 distinct tickets returned in the sprint |
| Fewer delivered than usual | delivered below the team's own trailing 3-sprint average for 3 sprints running |

None triggered: "On track". One: "Needs attention". Two or more: "At risk". The label is a summary of triggered rules and the rules are always printed with it. The DM may override it with a written reason; the override and its reason are shown in the report.

## 14. DM input, narrative and risks

### 14.1 Annotations

`annotate` records the DM's assertions. Each is attached to a specific event (a ticket's carryover at a given sprint end, or a specific return), with the time it was entered.

- Carryover: agreed with the PO and communicated in advance (`yes`, `no`, `unknown`); a reason from a fixed list (dependency, waiting on customer, requirement changed, unplanned work took priority, planned leave, larger than expected, other).
- Return: `defect`, `requirement changed`, `other`.
- A free-text note, shown with the ticket in both reports. The DM should not put names in it; `names-check` catches roster names if they do.

Annotations never change a figure, flag or status. They are displayed beside the ticket, flag or risk they concern. Corrections are kept as new versions. `annotate --interactive` lets the DM enter answers in a terminal with no agent involved.

### 14.2 What an agent may read

Using an agent means its model provider may see whatever the agent reads. `agent_text_access` controls what the CLI gives the agent: `full` (titles and truncated descriptions), `titles`, or `none` (ticket IDs, types and counts only). The default is `none` until the DM sets it.

This setting limits what the tool prints and what it writes into agent-facing files. It is **not** a technical barrier: an agent with file access could open files in the data home. Skills instruct the agent not to open the data home directly. Where the customer forbids a model provider seeing ticket text or people's names, the DM runs the affected steps by hand: `annotate --interactive`, the fallback narrative, and opening the delivery manager report themselves.

### 14.3 Narrative

With `full` or `titles` access, `analyze` writes an agent-facing input per team: the delivered, awaiting-acceptance and not-delivered tickets with ID, type, title, parent title and, at `full`, a truncated description as it stood at the sprint's end.

The agent writes, per team and bucket, entries with a heading, one or two plain sentences and the ticket IDs covered, plus a summary paragraph for the report.

`narrative-validate` rejects the file if a cited ID is not in that bucket, a ticket in a bucket is not covered, a length limit is exceeded, or the text claims deployment, acceptance, a cause or a business result ("live", "as planned", "because") without the DM having confirmed that sentence. Sprints backfilled from history get no written narrative.

Fallback with no agent, and for `none`: tickets listed by title grouped by parent, and a template summary sentence built from the figures.

### 14.4 Risk candidates

`risks` runs rule-based detectors and writes candidates with a kind, team, evidence ticket IDs, the figures behind it and a suggested ask:

- Chronic carryover: `sprints_carried` ≥ 3.
- Repeated returns on a ticket (≥ 2), noting whether text changed between returns.
- Long wait: finished and awaiting acceptance for more than 5 working days, or across 2 sprint ends.
- Work arriving without criteria: at least 3 tickets and at least a third of those received in the sprint.
- Many mid-sprint additions.
- Sprint started with more work than usual: load at the start above usual (12.5). The ask is to agree the next sprint's size with the product owner.
- One recorded vendor contributor on a parent feature across the window, on at least 2 tickets.
- Fewer delivered than usual (same rule as 13.4).
- Days with nothing in progress: a person with ≥ 3 in a sprint.
- Available person-days well below nominal.

The DM accepts, edits or drops each. An accepted risk has a stable ID, stays open in later reports until the DM closes it, and is re-checked each report: if its evidence no longer holds, the DM is told.

## 15. Outputs

### 15.1 The report

Two self-contained HTML files with the same layout as the mockup:

- **Executive report:** summary and key figures per date group; team table; risks and asks; per team: delivered, finished and awaiting acceptance, planned work line, where ticket time went, state of work when received, carried-over tickets without owners. A Markdown version is written alongside for pasting into a message.
- **Delivery manager report:** everything in the executive report, plus an owner column, a people table per team, links from each figure to its ticket list, the coverage note, and an evidence card for each flagged ticket showing who held it for how long and the dated list of changes. It keeps the mockup's switch so the DM can see the executive view in place.

Both are drafts until the DM issues them. The tool never sends anything.

### 15.2 No names in the executive report

The executive report must contain no person's name. This is enforced in two ways.

1. Its renderer is given a dataset from which every person-level field has been removed, so the names are not in the file at all, hidden or otherwise.
2. `names-check` scans every piece of text in the executive report (ticket titles, DM notes, narrative, risk text, status overrides, file metadata) for roster display names and aliases. Any hit is listed and blocks `issue` until the DM edits the text or approves that occurrence.

The second check exists because a ticket title or a note can mention an engineer by name. It covers the vendor's roster only; it cannot recognise names of customer staff that appear inside the customer's own ticket titles.

The aim is that names are not printed, not that people cannot be identified: on a team with one vendor engineer, that team's figures are that engineer's figures, and a reader with board access can open any listed ticket.

The delivery manager report carries a "Restricted: delivery manager" mark in its header. All text from tickets and from the DM is escaped before it is placed in HTML or Markdown.

### 15.3 Evidence brief

`evidence --person <id>` or `--team <name>`, over the last N team sprints (default 6):

- Subject, period, teams and assignment dates.
- Per-sprint table of the section 13 figures.
- Flags and when they were raised.
- Everything delivered and handed off, with ticket links.
- For each flagged ticket: the holder timeline, the dated change list and the DM's annotations.
- Context: leave, days with nothing in progress, time waiting on others, state of work when received.

A person brief is restricted to the DM. A team brief has an executive form with no names, produced through the same dataset and `names-check` as the executive report.

### 15.4 Using the mockup

The mockup fixes the layout the DM approved: summary, team table with trend bars and status, risks and asks, a block per team, and the people-view additions. The view switch is kept in the delivery manager report only. The builder must not copy from it: private rows hidden inside a file meant for executives, the web font link, the "safe to send" caption, QA-stage tickets shown in the carryover table, or its wording "hands-on" and "Delivering". Tests check issued files for external requests and, in the executive report, for hidden person data.

## 16. Issuing and history

`issue` creates an immutable **edition** of a report: the handouts as issued, plus a manifest with content hashes of every input (snapshots, config, roster, phase mapping, thresholds, annotations, narrative, risks), the tool version and a calculation-rules version. Editions are never overwritten. Re-issuing creates the next edition with a note of what changed.

Team trends for past sprints are read from the metrics as last issued. If the config, mapping or calculation rules have changed since, the report says that earlier sprints were computed under earlier rules, or the DM re-analyses them and issues a new edition.

On first use, earlier sprints in the window are backfilled from history and marked as such. They have figures and no annotations or narrative. Sprint dates are taken as the board shows them now; if they were edited after the fact the tool cannot tell, and the footnote says so.

## 17. Skills and packaging

Three skills in the open Agent Skills format, each a short procedure calling the CLI:

| Skill | Procedure |
|---|---|
| `delivery-setup` | Run `probe`. Build config and roster with the DM. Propose the phase mapping and counting level; DM confirms. Run the trace check on tickets the DM chooses. Run `setup-check`. |
| `delivery-sprint-close` | For each team whose sprint has ended: `collect`, `analyze`. Ask the annotation questions. Write and validate narrative if permitted. `risks`, confirm with the DM. `report`, `names-check`. Tell the DM where the drafts are. `issue` on approval. |
| `delivery-evidence` | Resolve the subject, run `evidence`, tell the DM where the brief is. |

Skills live in one canonical directory. An install command places them where each agent looks. The builder verifies current discovery locations for Copilot in VS Code (opened in WSL), Copilot CLI and Claude Code, and tests in each both that the skill is found and that it can run the launcher. The repo also has an `AGENTS.md` with the same procedures.

Skills contain no metric logic.

## 18. Privacy and open-source hygiene

- The repository holds code, skills, docs and synthetic fixtures only.
- Real data lives in the data home on the Linux filesystem with restrictive permissions.
- Non-vendor identities are pseudonymised at collection.
- The delivery manager report and person briefs carry a "Restricted: delivery manager" mark in their header.
- The README states that per-person reporting on employees may be subject to local law and works-council rules, and that the operator is responsible for checking; it also covers how long to keep the data home and how to delete it.
- Network use: the configured ADO organisation, and Microsoft's sign-in endpoints through `az` when a token is acquired. Nothing else.

## 19. Testing

- **Fixture builder** for writing synthetic ticket histories readably.
- **Scenario tests**, at least: handoff to QA; ticket carried 4 sprints; ticket returned 3 times; reopen in a later sprint and re-close; ticket moved to the next sprint early; added mid-sprint; no criteria at receipt versus added before receipt; two vendor people on one ticket; `tasks` counting level; person on leave; part-time person; person moving teams mid-sprint; joiner; days with nothing in progress; removed ticket; closed as duplicate; QA expressed only as a board column; split column; ticket never in a sprint; ticket inherited from a non-vendor person with earlier sprint history; ticket untouched for months; bulk edit; service account closing tickets; project move resetting state; changes on a weekend; timestamps near midnight and across a clock change; two teams with offset sprint dates; one-person team.
- **Invariants:** bucket durations add up; each vendor ticket has exactly one outcome per sprint; vendor delivered is a subset of squad delivered; every figure has a ledger entry.
- **Golden portfolio** modelled on the mockup's story lines, following this document's rules, asserted through `metrics.json` and the rendered files.
- **Output tests:** no external requests in any output; ticket and note text is escaped; the executive report contains no person-level field and no roster name or alias anywhere in the file, including in ticket titles and notes planted for the purpose; `names-check` blocks `issue` on a hit.
- **Probe export tests:** adversarial values planted in every string field must not appear in the export.
- **Collector tests** against recorded response shapes with a fake HTTP layer, including throttling, expiry mid-run, interrupted pages and full-batch partitions. Shapes are corrected from the probe export.
- No test needs network access.

## 20. Phasing

1. **Probe and trace.** `probe`, minimal config and roster, collection of chosen tickets, `trace`. Run on the customer machine; the probe export and trace check results come back.
2. **Core, all teams.** Full collection with coverage and squad ledger, `analyze`, `explain`, `annotate`, `report` with fallback text, `names-check`, `issue`, backfill, and the `delivery-setup` and `delivery-sprint-close` skills. All configured teams are collected from the first run; the trace check is done on one team in parallel.
3. **Evidence and risks.** `evidence`, `risks`, agent narrative with validation, `delivery-evidence`.
4. **Source-control corroboration.** Separate design. Sketch: with a personal access token, read commit history and branch builds, attribute them to roster people, link commits to tickets by ID, and show them in the delivery manager report as corroboration only.

## 21. Unverified assumptions

| # | Assumption |
|---|---|
| A1 | The `az`-issued token is accepted by the REST API and reports its expiry |
| A2 | Direct HTTPS from WSL works through the customer's proxy and certificates; if not, `az devops invoke` does |
| A3 | WIQL `EVER` on the assignee finds history for each alias, including renamed and deactivated accounts |
| A4 | ID-range partitioning returns complete results within WIQL limits |
| A5 | Revision history returns full field values per revision, including board column, and pages predictably |
| A6 | Identity values in revisions carry a permanent account identifier |
| A7 | Team iterations expose start and finish dates for past sprints |
| A8 | Board column history is complete enough to detect a QA column on boards that use one |
| A9 | Description and acceptance criteria are under their standard reference names |
| A10 | A six-sprint collection for 8 teams, including the squad ledger, completes within rate limits in acceptable time |
| A11 | Work item type states, categories and team board columns can be read |
| A12 | The parent of a ticket at a past date can be recovered |
| A13 | The reason values that mean "not delivered" can be listed for the customer's process |
| A14 | Python in WSL can load the configured timezone without installing anything |

## 22. Calls made by the designer

For the DM to confirm or overturn. Items 1–6 were put to the DM during the review; the rest were not individually discussed.

1. Per person: flags and observations only; "No flags raised" in place of a verdict label.
2. Team status label kept, as a summary of three printed rules, with any DM override shown in the report.
3. A minimal identity-free history of non-vendor tickets is stored so the squad total can be checked.
4. No comparison of vendor output with headcount share.
5. All teams from the first run, with the trace check on one team in parallel.
6. One handout for all teams, grouped by sprint dates, with totals per date group only.
7. Python standard library only; JSON config.
8. Two report files: an executive report with no names in it, and a delivery manager report that adds the per-person view. The DM asked that the executive version never show names.
9. Roster names are scanned for in the executive report's text and block issuing; no folding of small teams.
10. Work finished and awaiting acceptance is not carryover and never raises a person flag; the planned-work line shows it openly.
11. No primary credit; everyone who held a ticket in progress is shown.
12. One counting level (stories or tasks) for the whole configuration.
13. Carryover age counts only from the first sprint a vendor person held the ticket.
14. "Delivered" excludes duplicate, won't-fix and similar closures, and counts a ticket once.
15. DM answers add context only and never change a figure, flag or status.
16. Agent text access defaults to none, and is described as a limit on the tool, not a barrier.
17. Data home on the Linux filesystem at `~/delivery-ledger-data/`.
18. All threshold defaults in sections 13 and 14.4; six-sprint window.
19. The KPI framework agreed elsewhere is not reproduced, at the DM's instruction.
20. Source-control corroboration deferred to its own design.
21. Working name "Delivery Ledger", CLI `ledger`.
22. Sprint scope is the customer's: who placed, added or took out each ticket is reported by party, and asks about scope are addressed to the product owner, never phrased as the vendor's commitment.

## 23. Left to the builder

- Module layout.
- HTML and CSS of outputs, following the mockup's layout within section 15.4.
- JSON schemas of all stored files.
- Which history endpoint to use, after benchmarking.
- Concurrency and retry parameters.
- How skills are installed per agent.
- Wording of fallback narrative, risk templates and the footnote.
- Behaviour when the timezone cannot be loaded, within the rule that nothing may be installed.
- Licence: ask the DM before publishing.
