# Delivery Ledger

The Delivery Ledger reads Azure DevOps work item history and writes sprint delivery reports for a delivery manager (DM) who supplies engineers to a customer. It is a command-line tool in standard-library Python, driven in conversation by one agent skill, `delivery-report`. It only reads the board and never sends a report anywhere.

For each sprint it writes two self-contained HTML files: `manager_DATE.html` for executives (no engineer names) and `dm_DATE.html` for the DM only.

## Installation

You need Python 3.9 or later and, for a real board, the `az` CLI with the `azure-devops` extension, signed in with `az login`. There are no packages to install.

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

`staff.txt` lists your people as `Name <email>;`. `vacations.xlsx` is your team calendar and is read again on every `report`. `init` asks, for each person, the team and the join date, and `team.csv` can be edited afterwards. Per-sprint notes go in `.delivery-skills/<sprint>/notes.txt`:

```
#48211 agreed: QA environment down 3-5 Nov, PO moved acceptance to S30
Alfa summary: The team finished document upload and validation.
Alfa note: private remark, DM file only
Alfa exec note: remark for both files
Alfa status: AMBER - reason
```

## How figures are counted

- Everything is counted in work items, at the level each team chose (stories or tasks), shown next to the team name and in the footnote.
- A sprint figure is "delivered X of Y planned": items that finished in the sprint against items planned at its start, not counting items removed or cut.
- Cycle time runs from the first In Progress or Resolved state to the first delivered completion, in days; the report shows the median and the P85 (nearest rank) over the last 6 sprints.
- Waiting is time in states you mark as waiting for someone else. More than 3 working days is a DM flag; more than 10 makes the status RED.
- Status is GREEN at 80% delivered or more with no old, unagreed carry-over; AMBER if one condition is missed; RED if both are, or on a long wait. You can override it in `notes.txt`.
- Thresholds are fixed local conventions, not industry standards, and the footnote says so. Every figure depends on the board being kept up to date.

Per-person reporting on employees may be subject to local law, data protection rules and works-council agreements; the operator is responsible for checking what is allowed. A licence has not been chosen yet; until one is added, this project must not be redistributed.
