# Delivery report v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the v2 Delivery Ledger with a slim tool that reads Azure DevOps work item history and writes, per closed sprint, an executive report (`manager_<date>.html`) and a private delivery manager report (`dm_<date>.html`).

**Architecture:** `init` turns one query, `staff.txt` and `vacations.xlsx` into `team.csv` + `config.json`. `report` fetches full revision histories of the relevant work items from Azure DevOps (WIQL for ids, Analytics OData for revisions and iterations), caches them, replays each item's history to fixed points (sprint start + 1 day, sprint end) and renders two self-contained HTML files. Every figure comes from replayed history, so reruns give the same numbers.

**Tech Stack:** Python 3.9+ standard library only (`urllib`, `json`, `csv`, `zipfile`, `xml.etree`, `zoneinfo` with a fixed-offset fallback, `unittest`). `az` CLI for the access token.

**Spec:** `docs/design-v3.md`

## Global Constraints

- Python 3.9+ standard library only; no third-party packages, including for `.xlsx`.
- Package stays `ledger`, launcher `bin/ledger`; all non-test code under `ledger/` totals at most 2,000 lines (enforced by a test).
- All working files live in `.delivery-skills/` under the current directory (override with `--dir`): `config.json`, `team.csv`, `cache/revisions.json`, `<sprint>/notes.txt`, `<sprint>/manager_<YYYY-MM-DD>.html`, `<sprint>/dm_<YYYY-MM-DD>.html`, `people/person_<name>_<YYYY-MM-DD>.html`.
- Reports say **work items** in every figure, never "stories" or "tasks"; the counting level appears only as "counts stories" / "counts tasks" next to the team name and in the method footnote.
- `manager_` contains no engineer name and no email (test-enforced). Nothing renders who added or removed an item.
- Thresholds are fixed constants in `ledger/metrics.py`: P85; 2 sprints carried; waiting > 3 working days (DM flag); waiting > 10 working days (RED); no board change for 5 working days; more than 3 items in progress; status GREEN threshold 80%; trend window 6 sprints; history window 120 days; `onboarding_sprints` default 2.
- Default `not_delivered_reasons`: `["Removed", "Won't Fix", "Cut", "Duplicate"]`.
- Tests never touch the network or `az`; all Azure DevOps access goes through an injectable `http` callable.
- No company, customer or real person names anywhere in the repository; fixtures use invented names and `vendor.example` / `customer.example` domains.

## Rulings on points the spec leaves open

- **Non-interactive init.** `init` prompts only when stdin is a TTY. `init --yes` accepts every suggestion and prints the summary; the skill uses `--yes`, shows `team.csv` to the DM and edits it from their answers.
- **Active = In Progress or Resolved** state category (Azure DevOps cycle-time rule). "First In Progress" means the first entry into either.
- **Credit uses the first delivered completion.** A reopened item keeps its first credit and first cycle time; reopening is listed in the DM report, never re-credited.
- **Bug level is per item:** a Bug whose parent is at the stories level counts at the tasks level; any other Bug counts at the stories level (unless the process already lists Bug in a backlog).
- **"Squad's P85"** for ages and the status rule is the team's P85 over the 6-sprint window (more stable than one sprint); the footnote says so.
- **DM flags are evaluated at the sprint end**, not at report time, so reruns stay identical. Only "completed late" depends on later history.
- **Roster-like unknown assignee:** "assignee email not on the roster" fires only for emails whose domain matches a roster email domain.
- **Sprint selection:** `report` without `--sprint` picks the most recently ended iteration (end date before today) among all teams' iterations; each team reports its iteration with that name; teams without one are listed as "no sprint named S".
- **Team sprints:** iterations (with dates) that hold at least one of the team's items in the history.
- **Percentiles** use nearest rank (`sorted(v)[ceil(p*n)-1]`), integers in days, for both median (p=0.5) and P85.
- **notes.txt grammar** (one entry per line, `#` lines with ids, case-insensitive keys):
  - `#<id> agreed|not agreed|unknown: <reason>`
  - `<TEAM> summary: <text>` (manager_ and dm_)
  - `<TEAM> note: <text>` (dm_ only); `<TEAM> exec note: <text>` (both)
  - `<TEAM> status: GREEN|AMBER|RED - <reason>` (manual override)
  - Anything else is ignored and reported as a warning.
- **Item scope fetched:** ids from the setup query changed within 120 days, plus their child items, plus items whose revisions were ever assigned to a roster email within 120 days; full revision history of all of those, plus titles of their parents.

## Review Focus

1. An item closed without ever being active: excluded from cycle time, still counted as delivered, flagged as hygiene. Test in Task 5.
2. A sprint with zero planned items: "0 of 0", no division by zero, status condition counts as met. Test in Task 5.
3. Email case and multiple emails per person: matching is case-insensitive and two `staff.txt` lines with the same name are one person. Test in Task 1.
4. Timezone boundary: a close at 00:30 local on the day after the sprint end (23:30 UTC on the last day) is not in the sprint for `Europe/Zurich`. Test in Task 4.
5. `vacations.xlsx` with a sheet that has no month titles or an unmatched name: a clear warning naming the sheet or the name, not a crash. Test in Task 2 (sheet) and Task 7 (name).

---

## File Structure

| File | Responsibility |
|---|---|
| `ledger/__main__.py` | `from .cli import main; main()` |
| `ledger/cli.py` | argparse: `init`, `report`, `person`; global `--dir`; errors as `error: …` exit 1 |
| `ledger/config.py` | `staff.txt`, `team.csv`, `config.json`, `notes.txt` read/write; `Person`, `Config` |
| `ledger/xlsx.py` | read `vacations.xlsx` grids into days off per name |
| `ledger/ado.py` | token, WIQL, OData paging, process levels, `fetch()` → cache dict |
| `ledger/replay.py` | `History` per work item: state at a time, first active, done, waiting time |
| `ledger/metrics.py` | counting rules, per-team sprint figures, people, flags, status, trend |
| `ledger/setup.py` | `init`: suggestions (team, join date, count level, waiting states) and prompts |
| `ledger/render.py` | `manager_`, `dm_` and person HTML (inline CSS, no scripts) |
| `tests/fixture.py` | invented-data builder for revision rows, iterations, levels |
| `skills/delivery-report/SKILL.md` | the agent flow from spec section 7 |
| `README.md`, `AGENTS.md` | rewritten for v3 |

---

### Task 1: Clean slate and configuration files

**Files:**
- Delete: every file under `ledger/`, `tests/`, `skills/`, plus `docs/reference.md`, `docs/team-performance-report/`
- Create: `ledger/__init__.py`, `ledger/__main__.py`, `ledger/config.py`, `tests/__init__.py`, `tests/test_config.py`, `tests/test_size.py`
- Modify: `.gitignore` (add `.delivery-skills/`), `scripts/install-skills.sh` (drop the reference to the deleted `skill-locations.md`; install every directory under `skills/`)

**Interfaces:**
- Produces:
  - `@dataclass Person: name: str; emails: List[str]; team: str; joined: Optional[date]; left: Optional[date]; allocation: float = 1.0`
  - `parse_staff(text: str) -> List[Tuple[str, str]]` (name, lower-cased email), accepts `Name <email>;` many per line, `"Last, First" <email>`, and bare emails (name = local part)
  - `merge_staff(pairs) -> Dict[str, List[str]]` name → emails (same name = one person)
  - `read_team(path) -> List[Person]`, `write_team(path, people)`; columns `name,email,team,joined,left,allocation`, one row per email
  - `@dataclass Config` with fields from spec section 3 plus `staff: str`, `vacations: str`; `load_config(dir) -> Config`, `save_config(dir, cfg)`; `Config.teams: Dict[str, Dict[str, str]]` (`{"count": "stories"|"tasks"}`)
  - `@dataclass Notes: items: Dict[int, Tuple[str, str]]` (id → (agreed, reason)), `summary`, `note`, `exec_note`, `status: Dict[str, Tuple[str, str]]` (each keyed by team), `warnings: List[str]`; `parse_notes(text: str) -> Notes`
  - `email_index(people) -> Dict[str, Person]` (lower-cased)

- [ ] **Step 1:** `git rm -r` the v2 files listed above; keep `bin/ledger`, `scripts/install-skills.sh`, `docs/design-v3.md`, `docs/superpowers/`.
- [ ] **Step 2: Write failing tests** in `tests/test_config.py`:

```python
def test_parse_staff_list_formats(self):
    pairs = parse_staff('Maya R. <Maya.R@vendor.example>; "Doe, John" <john.doe@vendor.example>;\nann@vendor.example')
    self.assertEqual(pairs, [("Maya R.", "maya.r@vendor.example"), ("John Doe", "john.doe@vendor.example"), ("ann", "ann@vendor.example")])

def test_same_name_two_emails_is_one_person(self):
    m = merge_staff([("Tom H.", "tom.h@vendor.example"), ("Tom H.", "tom.h2@vendor.example")])
    self.assertEqual(m, {"Tom H.": ["tom.h@vendor.example", "tom.h2@vendor.example"]})

def test_team_csv_round_trip_and_case_insensitive_index(self):  # write, read, email_index["TOM.H2@VENDOR.EXAMPLE".lower()].name == "Tom H."

def test_parse_notes(self):
    n = parse_notes("#48211 agreed: QA env down\n#48277 Unknown: API dependency\nALFA summary: Done upload.\nALFA status: amber - QA slot lost\nBETA exec note: Pilot live\nnonsense line")
    self.assertEqual(n.items[48211], ("agreed", "QA env down"))
    self.assertEqual(n.items[48277], ("unknown", "API dependency"))
    self.assertEqual(n.summary["ALFA"], "Done upload.")
    self.assertEqual(n.status["ALFA"], ("AMBER", "QA slot lost"))
    self.assertEqual(n.exec_note["BETA"], "Pilot live")
    self.assertEqual(len(n.warnings), 1)

def test_config_round_trip_defaults(self):  # save then load; onboarding_sprints == 2, not_delivered_reasons default list
```

  `tests/test_size.py`: `test_code_stays_small` sums lines of `ledger/**/*.py` and asserts `<= 2000`.
- [ ] **Step 3:** Run `python3 -m unittest discover -s tests`; expect failures (module missing).
- [ ] **Step 4:** Implement `ledger/config.py` and `ledger/__main__.py` (cli comes in Task 7; `__main__` may import lazily).
- [ ] **Step 5:** Run tests; expect PASS.
- [ ] **Step 6:** Commit `refactor: remove v2, add v3 configuration files`.

### Task 2: Vacations reader

**Files:** Create `ledger/xlsx.py`, `tests/test_xlsx.py`

**Interfaces:**
- Produces: `read_vacations(path: str, half_day_codes: Sequence[str] = ()) -> Vacations` with `@dataclass Vacations: days_off: Dict[str, Dict[date, float]]` (name → {date: 1.0 or 0.5}), `squad: Dict[str, str]` (name → value of the `Squad` column, when present), `warnings: List[str]`; `match_name(name: str, candidates: Iterable[str]) -> Optional[str]` (case, accents, word order ignored; `"Doe John"` matches `"John Doe"`).

Parsing rules (layout from the operator's team calendar; spec section 3):
- Read `xl/workbook.xml` + rels for sheets, `xl/sharedStrings.xml`, each sheet's cells (shared, inline and numeric), and `<mergeCells>`.
- **Header row:** the first row with a cell whose text is `Name` (case-insensitive, trimmed) and at least 28 day-number cells (integers 1–31, as numbers or numeric text) to its right. The `Name` column is that cell's column; a `Squad` cell in the same row gives the squad column.
- **Day runs:** walk the day-number cells left to right; a run ends where the number does not increase by 1 (… 30, 31, 1 starts a new run).
- **Months:** title cells are cells in rows above the header whose text is an English month name (full or 3-letter) and a 4-digit year, or `YYYY-MM`. A title belongs to the run whose columns contain the title's column or, for a merged title, any column of its merge range. A run without a title takes the month before the next run's month (the leading "30, 31" of the previous month) or after the previous run's month. A sheet with no title at all is skipped with a warning naming the sheet.
- **Days off:** for each row below the header with a non-empty `Name`, every non-empty day cell is a day off; a value in `half_day_codes` is 0.5. Styles (weekend hatching, today's highlight) are ignored.

- [ ] **Step 1: Write failing tests** that build a workbook in a temp dir with `zipfile` (minimal parts: `[Content_Types].xml`, `xl/workbook.xml`, `xl/_rels/workbook.xml.rels`, `xl/sharedStrings.xml`, `xl/worksheets/sheet1.xml` with `<mergeCells>`). The fixture copies the real layout: row 3 has `CT`, `Active`, `Squad`, `Name` in A–D, then days `30, 31` in E–F, `1..30` in G–AJ under a `November 2026` merge G2:AJ2, `1..31` in AK–BO under `December 2026` AK2:BO2:

```python
def test_reads_team_calendar_layout(self):
    v = read_vacations(path, half_day_codes=["H"])
    self.assertEqual(v.days_off["Maya R."], {date(2026,10,31):1.0, date(2026,11,3):1.0, date(2026,11,4):1.0, date(2026,12,24):0.5})
    self.assertEqual(v.squad["Maya R."], "Alfa")
    self.assertEqual(v.warnings, [])

def test_untitled_leading_run_is_previous_month(self):  # "30, 31" before November -> October 30/31

def test_sheet_without_titles_is_a_warning(self):
    v = read_vacations(path_without_titles)
    self.assertEqual(v.days_off, {}); self.assertIn("Sheet1", v.warnings[0])

def test_match_name_ignores_case_accents_order(self):
    self.assertEqual(match_name("doe JOHN", ["John Doe", "Maya R."]), "John Doe")
    self.assertEqual(match_name("Zoë Brandt", ["Zoe Brandt"]), "Zoe Brandt")
    self.assertIsNone(match_name("Nobody", ["John Doe"]))
```

- [ ] **Step 2:** Run; expect FAIL. **Step 3:** Implement. **Step 4:** Run; expect PASS.
- [ ] **Step 5:** Commit `feat: read vacations.xlsx month grids`.

### Task 3: Azure DevOps access

**Files:** Create `ledger/ado.py`, `tests/test_ado.py`

**Interfaces:**
- Produces:
  - `class AdoError(Exception)` with `.next_step: str`
  - `az_token(run=subprocess.run) -> str` via `az account get-access-token --resource 499b84ac-1321-427f-aa17-267ca6975798 --query accessToken -o tsv`; a non-zero exit raises `AdoError(..., next_step="az login")`
  - `az_defaults(run=subprocess.run) -> Tuple[Optional[str], Optional[str]]` from `az devops configure --list`
  - `class Client(org: str, project: str, http: Callable[[str, str, Optional[dict]], dict])`; `http(method, url, body)` returns parsed JSON; default `http` uses `urllib` with `Authorization: Bearer <az_token()>`
  - `Client.query_ids(query: str, since: date) -> List[int]`: a URL containing `/_queries/query/<guid>` or a bare GUID → `GET {org}/{project}/_apis/wit/wiql/{guid}?api-version=7.1`; text starting with `SELECT` (case-insensitive) → `POST .../_apis/wit/wiql`; anything else is an area path → WIQL `SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND [System.AreaPath] UNDER '<path>' AND [System.ChangedDate] >= '<since>'`. Flat result ids from `workItems`, link-query ids from `workItemRelations[].target`.
  - `Client.odata(entity: str, params: Dict[str, str]) -> List[dict]` on `https://analytics.dev.azure.com/{org_name}/{project}/_odata/v4.0-preview/{entity}`, following `@odata.nextLink`
  - `Client.levels() -> Dict[str, List[str]]` from `GET {org}/{project}/_apis/work/processconfiguration?api-version=7.1`: `{"stories": requirementBacklog.workItemTypes[].name, "tasks": taskBacklog.workItemTypes[].name}`
  - `fetch(client, query: str, emails: List[str], today: date, history_days: int = 120) -> dict` returning the cache dict:
    `{"fetched": iso, "levels": {...}, "iterations": [{"path","name","start","end"}], "revisions": [row], "parents": {id: title}}`
    where `row` = `{"id","rev","changed","type","state","category","reason","iteration","area","assigned","parent","title","tags"}` (`assigned` lower-cased email or None, `tags` list of strings, `changed` ISO UTC).
  - `save_cache(dir, data)`, `load_cache(dir) -> dict`

Fetch steps: ids = `query_ids(...)`; ∪ children via OData `WorkItems?$filter=ParentWorkItemId in (…)&$select=WorkItemId`; ∪ `WorkItems?$filter=Revisions/any(r: r/AssignedTo/UserEmail in (…) and r/ChangedDate ge <since>)&$select=WorkItemId`; then `WorkItemRevisions?$filter=WorkItemId in (…)&$select=<fields from spec section 4>&$expand=AssignedTo($select=UserEmail)` in batches of 200 ids; `Iterations?$select=IterationPath,IterationName,StartDate,EndDate&$filter=StartDate ne null`; parent titles via `WorkItems?$filter=WorkItemId in (…)&$select=WorkItemId,Title`.

- [ ] **Step 1: Write failing tests** with a fake `http` that records calls and serves canned JSON:

```python
def test_query_kinds(self):  # GUID URL -> GET wiql/<guid>; "select ..." -> POST; "Proj\\Claims" -> POST with "UNDER 'Proj\\Claims'"
def test_odata_follows_next_link(self):  # two pages -> rows concatenated, two calls
def test_fetch_flattens_rows_and_lowercases_email(self):
    data = fetch(client, "Proj\\Claims", ["maya.r@vendor.example"], date(2026,11,13))
    r = data["revisions"][0]
    self.assertEqual(set(r), {"id","rev","changed","type","state","category","reason","iteration","area","assigned","parent","title","tags"})
    self.assertEqual(r["assigned"], "maya.r@vendor.example")
def test_ids_are_batched_by_200(self):  # 450 ids -> 3 revision calls
def test_az_not_logged_in(self):
    with self.assertRaises(AdoError) as e: az_token(run=fake_run(returncode=1, stderr="Please run 'az login'"))
    self.assertEqual(e.exception.next_step, "az login")
```

- [ ] **Step 2:** Run; FAIL. **Step 3:** Implement. **Step 4:** Run; PASS.
- [ ] **Step 5:** Commit `feat: read work item history from Azure DevOps`.

### Task 4: History replay and test fixture

**Files:** Create `ledger/replay.py`, `tests/fixture.py`, `tests/test_replay.py`

**Interfaces:**
- Consumes: cache row format (Task 3).
- Produces:
  - `local_tz(name: str) -> tzinfo` (`zoneinfo.ZoneInfo`, falling back to UTC with a warning when tz data is missing)
  - `day_end(d: date, tz) -> datetime` (UTC instant of 24:00 local on `d`), `day_start(d, tz)`
  - `class History(rows: List[dict], not_delivered: Sequence[str])` with:
    `id, type, title, parent` (from the last row), `at(t: datetime) -> Optional[dict]` (last row with `changed <= t`), `first_active -> Optional[datetime]`, `done_at -> Optional[datetime]` (first entry into `Completed` whose reason is not in `not_delivered`), `reopened -> bool` (active again after `done_at`), `assignees_active(until: datetime) -> Set[str]`, `waiting_seconds(start, end, waiting_states: Sequence[str]) -> float` (time in a waiting state or with tag `Blocked`), `last_change(before: datetime) -> Optional[datetime]`, `was_in(path: str, t: datetime) -> bool`
  - `tests/fixture.py`: `class Board` builder: `Board(tz="Europe/Zurich")`, `.sprint(name, start, end, path=None)`, `.item(id, type="User Story", parent=None, title=None)` returning an item with `.at(when: str "YYYY-MM-DD HH:MM" local, state=, category=, iteration=, assigned=, reason=, tags=)` chaining; `.cache() -> dict` in the Task 3 format with levels `{"stories": ["User Story", "Product Backlog Item"], "tasks": ["Task"]}`.

- [ ] **Step 1: Write failing tests:**

```python
def test_first_active_and_done(self):  # New -> Active (3 Nov 09:00) -> Closed (6 Nov 17:00); first_active and done_at match; reopened False
def test_not_delivered_reason_is_not_done(self):  # Closed with reason "Cut" -> done_at None
def test_reopen_keeps_first_done(self):  # Closed 5 Nov, Active 6 Nov, Closed 10 Nov -> done_at 5 Nov, reopened True
def test_waiting_seconds_states_and_blocked_tag(self):  # 1 day "Ready for QA" + 1 day Active tagged Blocked -> 2*86400
def test_timezone_boundary(self):
    tz = local_tz("Europe/Zurich")
    h = History(rows_closed_at_utc("2026-11-06T23:30:00Z"), [])  # 00:30 local on 7 Nov
    self.assertGreater(h.done_at, day_end(date(2026, 11, 6), tz))
def test_at_and_was_in(self):  # iteration changes mid-sprint; was_in(path, t) follows it
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: replay work item history`.

### Task 5: Sprint figures

**Files:** Create `ledger/metrics.py`, `tests/test_metrics.py`

**Interfaces:**
- Consumes: `History`, `Board`, `Person`, `Config`, `Notes`.
- Produces:
  - constants from Global Constraints (`P85 = 0.85`, `CARRIED_FLAG = 2`, `WAIT_FLAG_DAYS = 3`, `WAIT_RED_DAYS = 10`, `STALE_DAYS = 5`, `WIP_FLAG = 3`, `GREEN_SHARE = 0.8`, `TREND = 6`)
  - `percentile(values: List[int], p: float) -> Optional[int]` (nearest rank)
  - `level_of(h: History, levels, histories) -> Optional[str]` (`"stories"`/`"tasks"`, Bug rule from Rulings)
  - metrics works on `data`, the cache dict from Task 3.
  - `team_sprints(data, people, team) -> List[dict]` iterations of the team, ordered by start
  - `team_sprint(data, cfg, people, team, sprint, notes, days_off: Dict[str, Dict[date, float]]) -> dict` (days_off keyed by staff name) returning:

```
{"team", "count", "sprint": {"name","path","start","end"},
 "planned": [ids], "delivered_planned": [ids], "added": [ids], "removed": [ids],
 "delivered": [ids], "late": [ids], "reopened": [ids],
 "cycle": {"n","median","p85"}, "cycle_window": {"n","median","p85"},
 "waiting_share": Optional[float],
 "carry": [{"id","title","age","sprints","state","waiting","agreed","reason"}],
 "worked_on": [[parent_title, count]],
 "trend": [{"sprint","planned","delivered_planned","delivered","p85"}],
 "capacity": {"engineers","onboarding","available","nominal"},
 "notes": {"summary","note","exec_note"}}
```

  Rules: exactly the "Counting rules" table in spec section 5, with planned = in the sprint's path at `day_end(start)` and not done by then; delivered = `done_at` within `[day_start(start), day_end(end))`; ours = Task-1 roster email in `assignees_active` of the item or (stories level) of any child item; age = `(sprint end date - first_active local date).days + 1`; cycle = `(done local date - first_active local date).days + 1`, excluding items without `first_active`; capacity uses Mon–Fri working days on or after `joined` (and before `left`) minus `days_off`, times allocation; `worked_on` groups the team's delivered and carried items by parent title (`"Other"` without parent), descending count then title; `trend` = the last `TREND` team sprints ending with this one.

- [ ] **Step 1: Write failing tests** with a `Board` holding sprints S28 (2026-10-13..10-24) and S29 (2026-10-27..11-07), team ALFA (stories, with child tasks) and BETA (tasks):

```python
def test_planned_delivered_added_removed(self):
    t = team_sprint(...ALFA, S29...)
    self.assertEqual((len(t["planned"]), len(t["delivered_planned"]), len(t["added"]), len(t["removed"])), (5, 3, 1, 1))
def test_child_task_assignee_makes_story_ours(self):  # story assigned to customer PO, child task to maya -> story counts for ALFA
def test_tasks_team_counts_tasks_only(self):  # BETA: parent story not counted, its 3 tasks are
def test_credit_by_completion_date_and_late(self):  # planned in S28, closed 28 Oct -> S28 late, S29 delivered (not planned)
def test_cycle_time_nearest_rank_and_closed_without_active(self):
    self.assertEqual(percentile([1,2,3,4,5,6,7,8,9,10], 0.85), 9)
    # item closed straight from New: in delivered, not in cycle n
def test_zero_planned_sprint(self):  # no items -> planned [], cycle n 0, no exception
def test_carry_over_age_and_sprints(self):  # planned S28 and S29, active since 15 Oct -> age 24, sprints 2, agreed from notes
def test_capacity_join_date_leave_allocation(self):  # 10 working days, joined 29 Oct, 1 day off, allocation 0.5 -> available 3.5, nominal 4.5
def test_reopened_listed_not_credited_twice(self):
def test_bug_level_rule(self):  # bug under a story counts for a tasks team; parentless bug for a stories team
def test_rerun_is_identical(self):  # team_sprint twice on the same data -> equal dicts
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: compute sprint figures per team`.

### Task 6: People, flags, comparison and status

**Files:** Modify `ledger/metrics.py`; Create `tests/test_flags.py`

**Interfaces:**
- Produces (added to the `team_sprint` result):
  - `"people": [{"name","available","nominal","delivered","in_progress","oldest": [id, age] | None,"median","items":[ids],"onboarding": bool,"no_assignment": bool}]` ordered by name
  - `"rest": {"assignees","delivered","median","p85","waiting_share"} | None` (items at the team's level under members' main area paths that no roster person of this team worked on; None when there are none)
  - `"attention": [{"level": "red"|"amber"|"grey", "text", "ask"}]`
  - `"status": {"colour": "GREEN"|"AMBER"|"RED", "reason": str, "manual": bool}`
- Flag rules (all at sprint end), levels:
  - red: carry-over older than window P85 **and** carried ≥ 2; engineer not onboarding with available > 0 and no item assigned at any time during the sprint
  - amber: open item older than P85; carried ≥ 2; carry-over with no note or agreed ≠ `agreed`; in a waiting state > 3 working days; active with no change for 5 working days; not onboarding, available > 0, nothing delivered (and had an assignment); more than 3 items active for one engineer
  - grey: done without ever being active; reopened; assignee email in a roster domain but not on the roster
  - A person is onboarding in the sprint containing `joined` and the next `onboarding_sprints - 1` team sprints.
- Status: share = `len(delivered_planned) / (len(planned) - len(removed))`, condition met when the denominator is 0. A = share ≥ 0.8; B = no carry-over with age > P85 and agreed ≠ `agreed`. GREEN = A and B; AMBER = one missed; RED = both missed or any ours item waiting > 10 working days at sprint end. A `notes.status[team]` entry replaces colour and reason and sets `manual`. `reason` is one sentence naming the share and the carry-over, e.g. `"85% of planned work items delivered, not counting 1 removed item; the one old carry-over (#48211) was agreed with the PO."`

- [ ] **Step 1: Write failing tests:**

```python
def test_no_assignment_is_red_unless_onboarding(self):
def test_nothing_delivered_amber_after_onboarding(self):  # joined 2 sprints ago -> flag; joined in this sprint -> no flag, people[].onboarding True
def test_waiting_more_than_three_working_days(self):  # Ready for QA from Mon to next Fri -> amber "raise with the PO" ask
def test_stale_active_item(self):
def test_wip_over_three(self):
def test_unknown_roster_domain_assignee_is_grey(self):  # tom.h2@vendor.example flagged, jane@customer.example not
def test_rest_of_squad(self):  # customer engineer's items in same area -> rest.delivered, rest.assignees == 1
def test_status_rules(self):  # GREEN case, AMBER (share 0.6), RED (both), RED (waiting 11 working days), manual override from notes
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: DM flags, people, comparison and status`.

### Task 7: init

**Files:** Create `ledger/setup.py`, `ledger/cli.py`, `tests/test_setup.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `parse_staff`, `merge_staff`, `read_team`, `write_team`, `Config`, `read_vacations` (`Vacations`), `match_name`, `Client`, `fetch`, `save_cache`, `History`, `level_of`.
- Produces:
  - `suggest(data, staff: Dict[str, List[str]], known: List[Person], squads: Dict[str, str] = {}) -> dict` (`squads` keyed by staff name, already matched with `match_name`):
    `{"people": [{"name","emails","team","joined","items","area"}], "teams": {team: {"count","stories","tasks"}}, "states": [{"name","category"}], "waiting": [names]}` — team = known value, else the person's `Squad` value from `vacations.xlsx`, else a colleague's team with the same main area, else last segment of the main area upper-cased, else `""`; joined = known value, else date of the first revision assigned to the person, else `None`; count = `"tasks"` when tasks done by the team's members in the window > 5 × stories done by them, else `"stories"`; waiting = state names in categories InProgress/Resolved containing `review`, `qa`, `test`, `accept`, `wait`, `blocked`, `verify` or `uat` (case-insensitive).
  - `run_init(args, client, ask: Callable[[str, str], str], out) -> int`: fetch → check that revisions carry every field in the row format and that `category` values are among `Proposed, InProgress, Resolved, Completed, Removed` (otherwise `AdoError` naming the missing field or value, before any file is written) → `suggest` → for each new person ask `Team?` and `Joined the team on?` (and once per team `Count stories or tasks?` with the counts in the prompt); `ask` returns the default when `--yes`; keep earlier answers; mark people missing from the new staff file with `left` = today; write `config.json` (org/project from `az_defaults()` or `--org/--project`, timezone from `--timezone` or the machine's `TZ`/`/etc/localtime` name, default `UTC`), `team.csv`, cache; print a summary including unmatched vacation names and all states.
  - `cli.main(argv=None)`: `ledger [--dir DIR] init --query Q --staff F --vacations F [--yes] [--org URL --project P --timezone TZ]`; `ledger [--dir DIR] report [--sprint NAME] [--offline] [--today YYYY-MM-DD]`; `ledger [--dir DIR] person EMAIL [--offline] [--today YYYY-MM-DD]`. `AdoError` and `ValueError` print `error: <msg>` and `next: <step>` and exit 1.

- [ ] **Step 1: Write failing tests** with the fixture cache and a fake client:

```python
def test_squad_column_is_first_suggestion(self):  # squads={"Maya R.": "Alfa"} -> team "ALFA"
def test_unmatched_vacation_name_is_reported(self):  # calendar row "Nobody X" -> listed in the init summary, no crash
def test_suggestions(self):  # team from area "Proj\\Claims\\Intake" -> "INTAKE"; colleague in same area gets the team already chosen
def test_count_suggestion(self):  # 30 tasks vs 4 stories -> "tasks"; 10 vs 8 -> "stories"
def test_waiting_state_suggestion(self):  # "Ready for QA", "In Review" yes; "Active" no
def test_init_yes_writes_files_and_keeps_answers(self):  # second run with an extra person asks only about them; removed person gets left date
def test_person_without_tickets_is_kept(self):
def test_init_stops_on_missing_fields(self):  # cache without "category" -> AdoError, no files written
def test_cli_error_lines(self):  # AdoError -> stdout has "error:" and "next: az login", exit code 1
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: automatic setup with init`.

### Task 8: Reports

**Files:** Create `ledger/render.py`, `tests/test_render.py`; Modify `ledger/cli.py` (report, person)

**Interfaces:**
- Consumes: `team_sprint` results (Tasks 5–6), `Config`, `Notes`.
- Produces:
  - `render_manager(sprint_name, teams: List[dict], generated: datetime) -> str`
  - `render_dm(sprint_name, teams, generated) -> str`
  - `render_person(person: Person, rows: List[dict], generated) -> str` (rows: one per team sprint in the trend window: sprint, available, delivered, in progress at end, median cycle, items)
  - `run_report(args, client) -> int`: load config, team, vacations, notes (`<sprint>/notes.txt`, created empty if missing), fetch unless `--offline`, compute every team, write `<sprint>/manager_<today>.html` and `dm_<today>.html`, print both paths and the notes warnings.
  - `run_person(args, client) -> int`: writes `people/person_<name with spaces as _>_<today>.html`.

Layout follows the approved mockup (https://claude.ai/artifact/VzgbPKNYVWhGgtF1ARJyK7): per team the status line, counting level and capacity next to the name, "The team worked on", summary, figure row, carry-over table, 6-sprint trend; footnote with the method, the status rule and the thresholds as local conventions. `dm_` starts with a "Private: delivery manager only" marker, the DM notes, "Needs attention", then per team "ours vs rest of squad" (with the caveat sentence) and "People", then everything in `manager_`. All HTML escaped with `html.escape`; inline CSS; no `<script>`; light and dark via `prefers-color-scheme`.

- [ ] **Step 1: Write failing tests:**

```python
def test_manager_has_no_names_or_emails(self):
    html = render_manager("Sprint 29", teams, gen)
    for p in people: self.assertNotIn(p.name, html); [self.assertNotIn(e, html) for e in p.emails]
def test_wording_is_work_items(self):  # "11 of 14" with "work items"; no "stories delivered"/"tasks delivered"; "counts tasks" present for BETA
def test_dm_starts_with_attention_and_private_marker(self):
def test_titles_are_escaped(self):  # title "<b>x</b>" appears as "&lt;b&gt;"
def test_no_script_tags(self):
def test_report_offline_writes_two_files_and_rerun_is_identical(self):
    # run_report twice with --offline --today 2026-11-13 on the fixture cache -> same two paths; file contents equal
def test_person_page(self):
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat: manager and dm sprint reports`.

### Task 9: Skill and docs

**Files:** Create `skills/delivery-report/SKILL.md`, `tests/test_docs.py`; Rewrite `README.md`, `AGENTS.md`

**Interfaces:** none new.

- [ ] **Step 1: Write failing tests** in `tests/test_docs.py`: README has sections `## Installation`, `## Usage`, `## How figures are counted`; SKILL.md front matter has `name: delivery-report` and a `description` under 300 characters; SKILL.md mentions `init --yes`, "Anything to add for team", `notes.txt`, and never tells the agent to open the HTML files; no file in the repo (excluding `.git`, `research/`) contains two forbidden company names (case-insensitive; built by concatenation in the test).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Write the docs.
  - SKILL.md follows spec section 7 steps 1–7. The agent runs `bin/ledger init ... --yes`, shows the DM `team.csv` and asks them to correct teams and join dates, then edits `team.csv`. It asks "Anything to add for team T this sprint?" per team, writes answers as `T note:` or `T exec note:` lines, asks about each carry-over without a note (`#id agreed|not agreed|unknown: reason`), and writes `T summary:` from delivered titles. It never computes figures and never sends reports.
  - README: what it is, install (`git clone`, run tests, `scripts/install-skills.sh`), usage (the three commands), the counting rules in short, and the notice on per-person reporting and local law. Licence line stays as in v2.
  - AGENTS.md: the same flow for agents without skills.
- [ ] **Step 4:** Run the whole suite: `python3 -m unittest discover -s tests` and `/usr/bin/python3 -m unittest discover -s tests` (3.9); expect PASS on both.
- [ ] **Step 5:** Commit `docs: v3 skill, README and AGENTS`.
