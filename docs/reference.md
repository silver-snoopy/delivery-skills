# Delivery Ledger: reference

The detail behind the [README](../README.md): first run, corrections, where data lives, what an agent may read, and known limits.

The Delivery Ledger turns Azure DevOps work item history into sprint delivery reports. It is for a delivery manager (DM) who supplies engineers to a customer's teams and needs to show, each sprint, what was delivered, what was carried over or returned, and where ticket time went, and who needs to answer with the board's record when the customer challenges a person or a team.

It is a command-line tool written in standard-library Python. Three agent skills (for Claude Code and GitHub Copilot) drive it in conversation with the DM; every figure, rule and check lives in the tool, not in the skills.

## What it does not do

- It does not measure effort or judge people. The board records what was set, by whom and when; the tool reports those observations and which rules they triggered. The DM draws conclusions.
- It never writes to Azure DevOps. It only reads.
- It never sends a report anywhere. The DM reads the drafts and sends them.
- It does not size tickets, detect split or cloned tickets, or know when work was really done, only when the board was updated.

## Requirements

- Python 3.9 or later. Nothing to install: no packages, no virtual environment.
- For real use: the `az` CLI with the `azure-devops` extension, signed in with `az login`, with read access to the board.
- On Windows: run the code, `az` and Python inside WSL.

## Quick look

```sh
bin/ledger demo --home /tmp/ledger-demo
```

This writes an invented portfolio of three teams and its report drafts, and prints their paths. Open the executive and delivery manager reports in a browser, then try commands against the demo home:

```sh
bin/ledger --home /tmp/ledger-demo status
bin/ledger --home /tmp/ledger-demo trace 40655
bin/ledger --home /tmp/ledger-demo risks --as-of 2026-11-07 --candidates
bin/ledger --home /tmp/ledger-demo evidence --team "Pricing Models" --as-of 2026-11-07
```

The demo's config sets `agent_text_access` to `none`, so commands print `[text hidden: agent_text_access is none]` in place of ticket titles. In your own terminal add `--show-text` before the command to see them (`bin/ledger --home /tmp/ledger-demo --show-text trace 40655`).

Every command prints a short summary; add `--json` before the command (`bin/ledger --json status`) for a machine-readable result. `bin/ledger COMMAND --help` lists the options.

## First run on a real board

With an agent: install the skills (below), open the repository in your agent and ask it to set up the Delivery Ledger. The `delivery-setup` skill runs the tests, runs `probe`, builds `config.json` and `roster.json` with you, runs `setup-check` and walks you through a trace check of tickets you know well.

By hand:

1. `python3 -m unittest discover -s tests`
2. `bin/ledger probe --org https://dev.azure.com/ORG --project PROJECT --team "TEAM" --alias SIGNIN`, then read `probe/probe-report.md` in the data home.
3. Write `config.json` and `roster.json` in the data home (see `ledger/config.py` for the accepted shape; `bin/ledger demo` writes an example).
4. `bin/ledger setup-check` until it reports no problems.
5. `bin/ledger collect --team "TEAM"`, `bin/ledger analyze --team "TEAM"`, then `bin/ledger trace ID` for five to ten tickets you know, compared with the board. Fix the phase mapping where they differ.

What the first run may tell you:

- `probe` runs the discovery queries that collection will send (without fetching any history) and reports their counts and time, and compares the iteration path format in ticket history with the team's iterations. Pass `--area "PROJECT\\AREA"` before `config.json` exists so that the squad query is sized too.
- `collect` refuses when two of the team's iterations overlap by two days or more, naming them. Two sprints that share one day (one finishes on the day the next starts) are treated as consecutive, with a warning: the shared day counts in the later sprint. An `exclude_iterations` entry that matches no iteration is reported as a warning. List the ones that are not the team's sprints under `"exclude_iterations"` for that team in `config.json` (names or full paths). An iteration that contains two or more others, such as a release or planning increment, is left out with a warning.
- A ticket whose history cannot be used (revisions missing or unparsable) is listed under coverage as malformed with a warning, and collection goes on. When more than a fifth of a team's tickets (and at least five) are missing, unreadable or malformed, `collect` prints a coverage alarm, and `report` repeats it.
- `analyze` stops when ticket history holds board states that have no phase rule, and lists all of them at once per team as lines to paste into that team's `"phases"`. `setup-check` lists the states the probe recorded that have no rule.

### Installing the skills

```sh
scripts/install-skills.sh                    # into this repository: .claude/skills and .github/skills
scripts/install-skills.sh --project ../other # into another project
scripts/install-skills.sh --user             # into ~/.claude/skills and ~/.copilot/skills
scripts/install-skills.sh --agents claude    # one agent only
```

The installer writes the launcher's absolute path into each installed skill, prints each file it writes, and refuses to overwrite a changed file unless you pass `--force`. The locations and when they were checked are in `docs/team-performance-report/skill-locations.md`. Agents without skill support can follow `AGENTS.md`.

## After sprints close

Ask your agent to close the sprint (the `delivery-sprint-close` skill), or run:

1. `bin/ledger collect --all` and `bin/ledger analyze --all`
2. `bin/ledger --show-text annotate --team "TEAM" --sprint SPRINT --interactive` to answer, for each carried-over or returned ticket, whether it was agreed and why
3. `bin/ledger risks --candidates`, then `--accept ID` or `--drop ID` for each candidate
4. `bin/ledger report`
5. `bin/ledger names-check`, fixing or approving each hit
6. Read the drafts, then `bin/ledger issue` to freeze them as a numbered edition. A note of what changed is required after edition 001 for that date: `bin/ledger issue --note "WHAT CHANGED"`.

## Correcting an issued report

An issued sprint is frozen: `collect` skips it, `analyze` keeps its figures, and `annotate --set`, `annotate --interactive` and `narrative-validate` refuse to change it (`annotate --list` still works). If an issued figure turns out wrong (a wrong phase mapping, a missing roster alias), or an answer arrives late, reopen the sprint on purpose:

`bin/ledger reopen --team "TEAM" --sprint SPRINT --note "WHAT AND WHY"`

- The issued edition under `reports/` is never touched.
- The sprint's issued data (metrics, ledger, annotations, narrative and the whole snapshot) is kept, read-only, under `superseded/NNN/` in that sprint's folder, and the reopen is logged in `reopened.json` there.
- Only the team's latest sprint can be reopened, and only if it was collected when it ended. Once a newer sprint of the team has been collected or analysed, `reopen` refuses: corrections to older sprints are not supported in this version, and their issued editions stay as they are.
- Fix the config or roster or record the late answers, then `bin/ledger analyze --team "TEAM"` recomputes the sprint and lists the figures that moved. `bin/ledger collect --team "TEAM" --refresh` can also read the board again. The sprint keeps its narrative as long as the narrative still validates.
- `report` marks the team "corrected since edition NNN" with your note, and the next `issue` needs `--note`. Its manifest records which edition it supersedes.
- The marker is part of that sprint's record: every later report that still includes the sprint shows it, and it goes only when a newer sprint of the team replaces it in the reports.

## Evidence briefs

When the customer challenges an engineer or a team, ask your agent for an evidence brief (the `delivery-evidence` skill), or run `bin/ledger evidence --person ID` or `bin/ledger evidence --team "TEAM"`. A person brief is restricted to the DM; it lists what the board recorded, not an assessment. `--executive` writes a team brief without person data.

## The data home

All real data lives outside the repository, in `~/delivery-ledger-data/` by default (override with `--home` or `DELIVERY_LEDGER_HOME`).

- Under WSL it must be on the Linux filesystem. The tool refuses a home under `/mnt/`, because Windows-mounted drives do not enforce Linux permissions.
- Directories are created `0700` and files `0600`.
- Issued editions are made read-only. To delete them, make them writable first.
- Keep it only as long as you need the history. To delete everything:

```sh
chmod -R u+w ~/delivery-ledger-data && rm -rf ~/delivery-ledger-data
```

## What leaves the machine

Nothing is sent anywhere. The tool talks only to your Azure DevOps organisation (and Microsoft sign-in through `az`). The probe export (`probe/probe-export.json` and `.md`) is the only file designed to be shared, for example with whoever supports the tool; customer-defined values in it are replaced by placeholders. Read it before you share it.

## What the executive report does and does not guarantee

- The executive files are built from a dataset with every person field removed, so engineers' names do not come from the tool's own person data.
- Names can still arrive through text: a ticket title, a DM note, the narrative or a risk. `names-check` scans every piece of text in the executive draft for each roster engineer's display name, their aliases, and the parts of three or more letters of the display name and of each sign-in alias (the part before `@`), also in the form without accents. Parts of one or two letters, such as an initial or a two-letter name, are not scanned for on their own. `issue` refuses while any hit is unapproved.
- The DM can approve a specific occurrence, for example a common word that is also someone's name. That occurrence is then printed.
- Names that are not in the roster are not detected: a nickname, or a customer employee named in a ticket title.
- This is not anonymity. On a team with one vendor engineer, that team's figures are that engineer's figures, and anyone with board access can open a listed ticket.

## What an agent may read

`agent_text_access` in `config.json` controls what every command prints, in its summary and in its `--json` result. Whatever the agent reads, its model provider may see.

| Setting | What commands print |
|---|---|
| `none` (default) | Ticket ids, types, states, counts and dates. Ticket titles, feature titles, descriptions, the DM's notes and the text around a `names-check` hit are replaced by `[text hidden: agent_text_access is none]`. |
| `titles` | Also ticket and feature titles. Descriptions, notes, risk texts the DM edited and `names-check` contexts stay hidden. |
| `full` | Everything, with descriptions shortened. |

This applies to `annotate`, `explain`, `trace`, `risks`, `names-check`, `narrative-input` and to error messages that would quote report text.

`--show-text` (a global option, before the command: `bin/ledger --show-text names-check`) prints the text whatever the setting says. It is for your own terminal. The skills forbid agents to pass it. At `none` or `titles` you review `names-check` hits, and at `none` the setup trace check, in your own terminal with it.

What the setting does not cover:

- The report and brief files on disk always contain ticket titles, and the delivery manager report contains engineers' names. They are for you. An agent must not open them, and the skills tell it not to.
- `trace` prints the display names of your own engineers at every setting, because the check compares who held a ticket with the board.
- `names-check` prints the name it found (the term) and the file, so that the hit can be acted on.
- It is not a technical barrier: an agent with file access could open the data home. When a model provider must not see ticket text or people's names at all, run those steps by hand: `bin/ledger --show-text annotate ... --interactive`, the report's own ticket lists instead of a written narrative, `bin/ledger --show-text names-check`, and opening the reports yourself.

## Legal notice

Per-person reporting on employees may be subject to local law, data protection rules and works-council agreements. The operator is responsible for checking what is allowed before collecting or reporting on any person, and for how long data is kept.

## Known limits

- Figures in the delivery manager report are not linked to their ticket lists. `bin/ledger explain --team "TEAM" --sprint SPRINT FIGURE` lists the tickets behind any figure.
- Engineers are matched by the sign-in names and display names in the roster (`aliases`). Identity ids are used when the roster lists them, but no command looks them up for you at setup.
- "Worked on" needs about half an hour of recorded in-progress time (0.02 of a working day); configurable as `worked_min_days`. It only leaves out a momentary hold, such as a ticket assigned and re-assigned within minutes. Returns and backward moves count only from the first time a vendor engineer held the ticket; earlier ones are shown as context on evidence cards.
- A report keeps the figures of sprints as they were computed. When a sprint in it, or in a team's trend, was computed under earlier calculation rules, the report says so under the footnote.

Every report carries this footnote: figures count tickets and working days taken from the board's change history. Tickets are not sized, so counts are compared only with the same team's earlier sprints. Durations reflect when the board was updated, not hours worked. Splitting or cloning a ticket cannot be detected. Sprint dates are as the board shows them today.

## Tests

```sh
python3 -m unittest discover -s tests
```

Run them with every Python you have, for example `/usr/bin/python3 -m unittest discover -s tests` as well, since the floor is Python 3.9. Tests use no network and no `az`.

## Licence

A licence has not been chosen yet. Until one is added, this project must not be redistributed.
