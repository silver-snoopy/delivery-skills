# Idea: first-time pass rate (manager card)

Status: idea, not designed yet. Raised 2026-11-17. Rated more useful than easy notes.

## The idea

A new small card per team on the manager_ page: of the items closed in the sprint, the share that never went backwards. Shown next to the plain count, for example "92% first-time pass, 2 returns". It tells management whether "done" really meant done.

**Name:** First-time pass rate (the agile name for lean "first pass yield"). Avoid "reopen rate" (covers only Closed → Active) and "rework rate" (DORA uses it for deployments).

## Scope decided

- **Team level only, on manager_.** No per-person figure on either page. Per person, the counts are too small to mean anything. Returns also depend on requirements, test environments and testers, not only on the engineer. And measuring individuals makes people avoid moving tickets back.
- Maybe later, on dm_: an item that returns again during the running sprint as an action signal (for example "returned 2×"). This is about the ticket, not a score for a person.

## How a return is detected

The tool already reads every state change, so no new data source is needed. A return is one of:

- leaving a waiting state (`waiting_states` in `config.json`, such as Ready for QA) for an active state, meaning testing failed;
- leaving a closed state for an active state, meaning the ticket was reopened.

Still to decide: count the returns per item, or only whether an item had any return; which counting level applies (stories vs tasks, as per team `count`); and which sprint gets an item that returns after its sprint ended.

## Main risk: board discipline

Testers may not move tickets back. They comment on the ticket, raise a separate bug, or tell the developer directly. Then no backward move shows on the board, and the card shows 100%, which looks good and is false. That is worse than no card.

1. **First step, before any design:** on the real board, count the backward moves per team over the last 6 sprints. A team with none at all is probably not recording returns. For such a team the card should say "not tracked", not 100%.
2. Possible second signal: bugs linked to a story in the same sprint. This depends on how the teams link bugs.

Depends on the first live-board run, which is still pending.
