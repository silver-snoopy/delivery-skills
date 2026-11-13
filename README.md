# Delivery Ledger

The Delivery Ledger reads Azure DevOps work item history and writes sprint delivery reports for a delivery manager (DM) who supplies engineers to a customer. It is a command-line tool in standard-library Python, driven in conversation by one agent skill, `delivery-report`. It only reads the board and never sends a report anywhere.

For each sprint it writes two self-contained HTML files, named with the run date: `manager_DATE.html` for executives and `dm_DATE.html` for the DM only. The executive file has no people tables and no per-person figures. Ticket titles and the DM's own notes appear in it as written, so `report` warns when a roster name or email appears in it; read it before you send it.

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

`staff.txt` lists your people as `Name <email>;`. `vacations.xlsx` is your team calendar and is read again on every `report`. `init` asks, for each person, the team and the join date, and prints the counting levels, waiting states and closure reasons it chose so you can correct `config.json`. `team.csv` can be edited afterwards; a new team name there is picked up by `report`, which warns until the team has a `count` in `config.json`. Per-sprint notes go in `.delivery-skills/<sprint>/notes.txt`:

```
#48211 agreed: QA environment down 3-5 Nov, PO moved acceptance to S30
Alfa summary: The team finished document upload and validation.
Alfa note: private remark, DM file only
Alfa exec note: remark for both files
Alfa status: AMBER - reason
```

## How figures are counted

- Everything is counted in work items, at the level each team chose (stories or tasks), shown next to the team name and in the footnote.
- A sprint figure is "delivered X of Y planned": items that finished in the sprint against items in the sprint at the end of its first day, not counting removed items. An unfinished planned item that is still on this sprint or moved to a later sprint is a carry-over; it is removed only when it went back to the backlog (or another path that is not a sprint) or was closed with a not-delivered reason.
- Cycle time runs from the first In Progress or Resolved state to the first delivered completion, in days; the report shows the median and the P85 (nearest rank) over the last 6 sprints.
- Waiting is time in states you mark as waiting (or with the Blocked tag). More than 3 working days is a DM flag; more than 10 makes the status RED.
- Board history covers the last `history_days` days (default 200) and is kept in the cache, which only grows.
- Status is GREEN at 80% delivered or more with no old, unagreed carry-over; AMBER if one condition is missed; RED if both are, or on a long wait. You can override it in `notes.txt`.
- Thresholds are fixed local conventions, not industry standards, and the footnote says so. Every figure depends on the board being kept up to date.

Per-person reporting on employees may be subject to local law, data protection rules and works-council agreements; the operator is responsible for checking what is allowed. A licence has not been chosen yet; until one is added, this project must not be redistributed.
