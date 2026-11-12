# Delivery Ledger

The Delivery Ledger turns Azure DevOps work item history into sprint delivery reports and evidence briefs. It is for a delivery manager (DM) who supplies engineers to a customer's teams. It is a command-line tool in standard-library Python, driven in conversation by three agent skills. It only reads the board and never sends a report anywhere.

## Installation

You need Python 3.9 or later and, for a real board, the `az` CLI with the `azure-devops` extension, signed in with `az login`. On Windows, run everything inside WSL. There are no packages to install.

```sh
git clone <this repository> && cd delivery-skills
python3 -m unittest discover -s tests        # check this machine
scripts/install-skills.sh                    # skills for Claude Code and GitHub Copilot, in this repository
bin/ledger demo --home /tmp/ledger-demo      # optional: invented data and example reports
```

`scripts/install-skills.sh --user` installs the skills for every project, and `--project DIR` into another one. Agents without skill support can follow `AGENTS.md`.

## Available skills

| Skill | Use it when | What you get |
|---|---|---|
| `delivery-setup` | You start on a new machine or board, add a team or engineer, or change how board states are read. | A checked config and roster, and a trace check on tickets you know. |
| `delivery-sprint-close` | A sprint has closed. | The executive report (no names) and the delivery manager report, as drafts and then as a numbered edition. |
| `delivery-evidence` | You want the delivery record of one engineer's contribution, or of a team's, over recent sprints: for a review, a renewal, a staffing decision or a question from the customer. | An evidence brief of what the board recorded over the last sprints. |

Ask your agent in plain words, for example "set up the Delivery Ledger" or "close the sprint". Every command the skills run is also available by hand: `bin/ledger --help`.

## Flow

1. **Once: set up.** The agent runs the tests and `probe`, builds the config and roster with you, and has you compare five to ten tickets you know with the board.
2. **After each sprint: close it.** The agent collects the board history and computes the figures. You answer, for each carried-over or returned ticket, whether it was agreed and why, and you accept or drop the risks it proposes.
3. **Read, then issue.** The agent writes the drafts and checks the executive report for engineers' names. You read it, approve, and the edition is frozen. You send it yourself.
4. **Any time: delivery evidence.** Ask for a brief on one engineer's contribution (for you only) or on a team's.

All real data stays in a private data home outside this repository (`~/delivery-ledger-data/` by default).

Details are in [docs/reference.md](docs/reference.md): first run by hand, correcting an issued report, the data home, what an agent may read, what the executive report guarantees, and known limits.

Per-person reporting on employees may be subject to local law, data protection rules and works-council agreements; the operator is responsible for checking what is allowed. A licence has not been chosen yet; until one is added, this project must not be redistributed.
