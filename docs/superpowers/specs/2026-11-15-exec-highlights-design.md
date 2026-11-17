# Executive card on manager_: design

Status: built 2026-11-15. Spec of record: `docs/design-v3.md`, section 6.

## Why

In the trial the manager page repeated the DM card: status line, worked-on list, people-adjacent detail. Executives want a short card per team that says what was delivered and what moved, and they do not need the RAG status, which is the DM's working judgement.

## Card layout (manager_ only)

1. Header: team name; at the right, in small print, "Sprint (dates)" and "N engineers[, K onboarding] (X PD capacity)".
2. **Delivery highlights**: the team's `summary:` lines (one as a paragraph, several as bullets) and the `exec note:`. Left out when both are empty.
3. **Delivery stats**: the stats tiles, including "work items carried over".
4. **Delivered items (N)**: folded; work item, title, "Planned" or "Added".
5. **Carried over items (N)**: folded; no age column; On Hold days.
6. The trend, folded.

No RAG status, no worked-on parent list, no per-person data. The DM card keeps its layout and gains "Counts: ..." in its header.

## Agent flow

`report` prints `TEAM worked on: #ID TITLE (delivered|in progress)`. The agent writes 2-4 short `T summary:` lines from those titles, for example `Alfa summary: Delivered ...` and `Alfa summary: In progress: ...`, and the DM confirms them. `report` warns for a team without a summary, because its card then has no highlights.
