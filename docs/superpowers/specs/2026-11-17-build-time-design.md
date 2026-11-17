# Build time and a simpler manager card: design

Status: agreed with the DM on 2026-11-17/18 in chat. The DM approved local mockup v2 ("the form that I imagined, with the narrative I was looking for"). The mockup is local only and holds invented data. Calls the DM did not make explicitly are marked **(builder's call)**.

## 1. Intent

The DM must show a flow-time figure as the vendor's AI practice matures. The vendor's 1–3 engineers work inside a larger customer team. Full cycle time mostly measures the customer's process: QA queues, PO acceptance, release trains and holds. A new team figure, **build time**, measures only the part the vendor's engineers control. **First-time pass** stays next to it as the quality check, so speed bought with rework does not show up as a gain.

The manager card tells one story: are we delivering, how fast, how well, and are we getting better? It has these parts:
- five figures: planned delivered; total delivered; **work items per 10 person-days** (throughput relative to capacity); **build time**; **first-time pass**;
- the last three of those figures carry an **arrow** against the team's own past (green better, red worse);
- a one-line **Where the time goes** breakdown, which answers "why are you slow?" by showing how much of start-to-done time was spent waiting in named customer states.

## 2. Goals, non-goals, anti-goals

- Goals:
  - build time on the manager card, with a per-sprint trend;
  - first-time pass pooled over the same window as build time;
  - throughput relative to capacity;
  - arrows that compare each flow figure with the team's own previous window;
  - the Where the time goes line;
  - late completions shown as a badge in Delivered items instead of a separate figure.
- Non-goals:
  - effort or hours per person;
  - repository data such as pull request times;
  - any change to the DM signals, the status rule or the definition of cycle time.
- Anti-goals:
  - any per-person build time;
  - a manager figure that executives cannot read without the footnote;
  - medians that pretend to add up;
  - comparing teams with each other: a team is compared only with its own past.

## 3. Definitions

- **Ours at t:** the existing rule. The item, or one of its child tasks, is assigned at time t to an engineer of the team (`_Team._ours_at`).
- **Build start:** the first revision time, of the item or of one of its children, at which the item is in category InProgress or Resolved and is ours. Assigned to ours while still Proposed does not start the clock. The clock also does not start while someone outside the team works on it.
- **Handoff:** the first entry into a state in `test_states` after build start, or the delivered close (`done_at`) if that comes first. With no `test_states` configured, the handoff is the delivered close. In that case `report` already prints the first-time pass warning. **(builder's call)** No extra warning.
- **Build time of an item:** the time between build start and handoff, minus time spent in a waiting state, in a hold state or with the Blocked tag. It is measured in days to one decimal place (seconds / 86 400). **(builder's call)** One decimal place, because many items build in under a day.
- **Measured items:** the team's delivered items at its counting level (`figures()["delivered"]`) that have a build start before handoff. Items with no build start (closed without ever being active while ours) are left out. They are already flagged as never active on dm_.
- **Now window and before window:** the now window is the last 3 sprints, including the closed one. The before window is the 3 sprints before them. Together they make up the existing 6-sprint trend window. Chosen by the DM on 2026-11-18 over a fixed baseline date.
- **Build time figure:** the median build time of the measured items delivered in the now window, pooled across sprints. The trend row shows each sprint's own median.
- **Throughput figure:** work items delivered in the now window, divided by the available person-days in the now window, times 10, to one decimal place. Available person-days are the team's existing capacity after leave (`capacity["available"]`), summed per sprint. The trend row shows each sprint's own value.
- **First-time pass figure:** pooled over the now window, (Σ scored − Σ returned) / Σ scored. This is not a median of the sprint shares. The 99% cap and the "–" rule stay as they are, applied to the pooled figure. The "not tracked" rule still looks at all 6 sprints. The trend row keeps the per-sprint shares.
- **Arrow:** the same figure computed over the before window, shown as "▲ from X vs sprints A–C" or "▼ from X vs sprints A–C". It is green when better (throughput up, build time down, first-time pass up) and red when worse. There is no arrow when the before window has no value, when the figure is "–" or "not tracked", or when the value is unchanged at the displayed precision.
- **Where the time goes:** over the delivered items of the window that have a cycle time:
  - **typical start to done:** the window median cycle time (the existing `cycle_window["median"]`, unchanged);
  - **building:** the summed build time of those items;
  - **waiting:** the summed time between first active and done in waiting states or with the Blocked tag, outside build time;
  - **other:** the rest of first active to done, for example hold, time before ours picked it up, and rework after a return.

  The three parts are pooled shares of that summed time, multiplied by the typical start-to-done time and rounded to whole days. Rounding gives any leftover day to "other", so the three parts always add up to the total. **(builder's call)** The waiting part lists the configured waiting states that actually occurred in the window, in config order.

## 4. What the reader sees

**manager_ card**

- **Delivery stats figures**, in this order:
  - planned work items delivered (closed sprint, as today);
  - work items delivered in total (closed sprint, as today);
  - **work items per 10 person-days**, for example `3.4` (label "work items per 10 person-days (last 3 sprints)") with an arrow;
  - **build time**, for example `3.5 days` (label "build time, median (last 3 sprints)") with an arrow, or `–` with no measured items;
  - **first-time pass**, for example `96%` (label "first-time pass (last 3 sprints, 1 of 25 returned)") with an arrow. The not-tracked and "–" forms keep their current labels.
- **Scope line** under the figures, in plain text: "Scope: +2 added / −1 removed mid-sprint · 1 work item carried over". With no changes it reads "Scope: no changes mid-sprint · nothing carried over".
- **Removed:**
  - the "added / removed" and "carried over" figures, which became the scope line;
  - both cycle time figures;
  - the waiting share figure;
  - the "planned work items completed after the sprint ended" figure.
- **Where the time goes (last 6 sprints)** sits under the figures. It is one sentence, "12 days typical from start to done = 4 days building · 6 days waiting (Ready for QA, PO Review) · 2 days other", plus a thin three-part bar: accent for building, warn for waiting, grey for other. The bar is decoration. The sentence carries the numbers, so the line still reads correctly in print. With no item that has a cycle time, the section is left out.
- **Delivered items:** the summary reads "Delivered items (9, 2 late)". Late rows get an amber `Late` chip in a new last column. With no late items the summary stays "Delivered items (9)" and has no extra column. **(builder's call)** No empty column.
- **Trend table:**
  - "Cycle time P85 (days)" is replaced by "Work items per 10 PD" and "Build time median (days)";
  - the first-time pass row is unchanged;
  - the planned and total rows are unchanged.
- **Footnote** adds plain definitions of the arrows, work items per 10 person-days, build time, the pooled first-time pass, Where the time goes and Late, and says to compare a team only with its own past. It drops the cycle time sentence from the manager footnote only.

**dm_**

No change. The ours-vs-rest table keeps cycle time median / P85 and waiting share, the signals keep using the window P85, and the DM footnote keeps the cycle time definition. dm_ has no figure row since PR #6.

## 5. Data shape

- `team_sprint` adds `t["build"] = {"median": float|None, "n": int, "before": float|None}`, `t["throughput"] = {"per10": float|None, "before": float|None}`, `t["windows"] = {"now": [names], "before": [names]}`, and `trend[i]["build"]` and `trend[i]["per10"]`.
- `t["first_pass"]` becomes the pooled now-window figure, with the same keys plus `"before": float|None`: `scored`, `returned`, `share`, `tracked`.
- `t["flow"] = {"total": int, "building": int, "waiting": int, "other": int, "states": [str]}`, or None when nothing qualifies.
- `delivered_items[i]["late"]` is a bool.
- `History` gets the helpers. Rules stay in `metrics.py`.

## 6. Edge cases

- Item handed to test, returned, then built again: build time ends at the **first** handoff, and the rework counts in "other". The return already lowers first-time pass, so the rework is not counted twice in build time. **(builder's call)**
- Item that is active while ours, then reassigned outside the team: build time still runs to the handoff, minus waiting, hold and blocked time. **(builder's call)** Tracking ownership per segment would add rules for little gain.
- Child tasks: a story counts as ours from the moment one of its tasks is ours and the story is active. Teams that count tasks measure each task on its own.
- Build start after the handoff, for example closed straight from Proposed and then reopened: not measured.
- Fewer than 6 sprints of history: the now window takes up to 3 sprints and the before window takes what is left, so there is no arrow when nothing is left. Labels say "last 3 sprints" even with fewer, as the trend already does (parked ruling from first-time pass).
- Zero available person-days in the now window: throughput is "–".
- Small samples: with 1 engineer the now window may hold only 6–9 items, so one odd item can flip an arrow. The footnote says arrows are a direction, not a verdict. **(builder's call)** No minimum sample size.

## 7. Tests

The tests use invented histories only, with no real names or ticket data:
- build start ignores a Proposed-state assignment;
- a non-ours active period before the handoff to ours is excluded;
- the handoff is the first test-state entry;
- the handoff falls back to done with no test states;
- waiting, hold and Blocked time is excluded;
- the window median is pooled;
- per-sprint trend values;
- first-time pass pooled versus a median of sprint shares;
- the three Where the time goes parts add up to the total;
- the now and before windows split the 6 sprints, and the arrow direction and colour per figure (lower build time is green);
- no arrow with an empty before window, an unchanged value or "not tracked";
- throughput per 10 person-days, and "–" with zero capacity;
- the scope line;
- the Late chip and summary count;
- the removed figures are absent from manager_;
- dm_ is unchanged.

The size cap in `tests/test_size.py` may rise if needed.

## 8. Decided

On 2026-11-18 the DM chose rolling windows (last 3 sprints against the 3 before) over a fixed baseline date.
