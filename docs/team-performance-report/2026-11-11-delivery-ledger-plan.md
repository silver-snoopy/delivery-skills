# Delivery Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Delivery Ledger: a stdlib-only Python CLI plus three agent skills that turn Azure DevOps work item history into an executive sprint report with no names, a delivery manager report with per-person observations, and evidence briefs.

**Architecture:** A pipeline of file-to-file stages over a data home outside the repo. `collect` (network) writes a pseudonymised snapshot of ticket revision histories; `analyze` is a pure function from snapshot + config + roster to `metrics.json` and `ledger.json`; `report` renders HTML and Markdown; `issue` freezes an edition. All analysis is developed and tested against synthetic snapshots produced by a fixture builder; the collector is tested against a fake HTTP transport.

**Tech Stack:** Python 3.9+ standard library only (`argparse`, `dataclasses`, `zoneinfo`, `urllib`, `hmac`, `html`, `unittest`). Bash launcher. No third-party packages.

**Spec:** `docs/team-performance-report/2026-11-11-delivery-ledger-design.md` (version 2). Read it before starting any task. Section numbers below refer to it. Layout reference: `docs/team-performance-report/handout-mockup.html`, subject to spec section 15.4.

## Global Constraints

- Python 3.9+, standard library only. No `pip install`, no `pytest`. Tests are `unittest`, run with `python3 -m unittest discover -s tests -v` from the repo root.
- No test may use the network or the real `az` CLI.
- No real company, customer, team or person names and no real ticket data anywhere in the repo. Fixtures use the invented names from the mockup.
- The tool is read-only against ADO: only `GET`, and `POST` to the WIQL query endpoint.
- Credentials are sent only to the configured organisation's host. TLS verification is never disabled.
- Data home default `~/delivery-ledger-data/`; override `--home` then `DELIVERY_LEDGER_HOME`. Refuse a home whose real path is under `/mnt/`. Directories `0700`, files `0600`.
- Every HTML output is one self-contained file: no external fonts, scripts, images or links to stylesheets. Fonts are a system stack.
- All ticket and DM text is escaped with `html.escape` before going into HTML, and Markdown-escaped before going into Markdown.
- All datetimes are timezone-aware UTC internally. Intervals are half-open `[start, end)`. Durations are fractional working days, displayed to the nearest 0.5.
- Ratios with a denominator below 5 are displayed as "n of m", never as a percentage.
- Exact display labels (do not paraphrase):
  - Outcomes: `Delivered`, `Closed, not delivered`, `Finished, awaiting acceptance`, `Carried over, in progress`, `Carried over, not started`, `Re-closed`, `Active outside a sprint`, `Assigned, not started`.
  - Time buckets: `In progress with a vendor engineer`, `In progress with another party`, `Unassigned`, `Waiting: <label>`, `Parked`.
  - Person flags: `Carried over more than 2 sprints`, `Ticket returned repeatedly`, `Ticket moving backward`, `Fewer delivered than usual`, `Nothing delivered or handed off`, `No recorded board changes`. No flags: `No flags raised`.
  - Team status: `On track`, `Needs attention`, `At risk`.
  - DM report header mark: `Restricted: delivery manager`.
  - Forbidden words in tool-generated text: "hands-on", "Delivering", "safe to send", "starvation", "sole engineer".
- Every figure in `metrics.json` has an entry in `ledger.json` under the same dotted key.
- Commit after every task with the message given. Never push.

## Review Focus

Failure modes the spec implies but does not spell out, most likely first. Each has a pinning test in the task named.

1. **A board state that no phase rule maps** (a new custom state appears). Expected: `analyze` stops with an error naming the team, state and column and telling the DM to add a rule; it never silently treats it as `todo`. Tasks 4 and 11.
2. **A team sprint with no vendor tickets, or with no delivered tickets.** Expected: metrics with zeros, medians `null`, report row renders with "–", no division by zero. Tasks 10 and 19.
3. **A ticket whose iteration path is not one of the team's sprints, or a sprint with no dates.** Expected: the ticket is treated as in no sprint; a dateless sprint is skipped with a warning in `collect`, never a crash. Tasks 7 and 15.
4. **Hostile or awkward ticket text** (HTML, `<script>`, pipes and backticks, emoji, a 400-character title). Expected: escaped in HTML and Markdown, truncated with an ellipsis in table cells, never executed or layout-breaking. Task 19.
5. **Running too early or resuming after an interruption.** Expected: `collect` refuses a sprint that has not ended; a collection interrupted mid-ticket resumes without marking that ticket complete and without mixing two cut-off times. Task 15.

## File Structure

```
bin/ledger                      bash launcher, works from any cwd
ledger/__main__.py              calls cli.main
ledger/cli.py                   argparse wiring, --home/--json, error exit
ledger/errors.py                LedgerError(message, hint)
ledger/home.py                  data home paths, permissions, atomic JSON
ledger/config.py                Config, Team, PhaseRule, Roster, Person; load + validate
ledger/workcal.py               WorkCalendar: working-day arithmetic
ledger/model.py                 Phase, Revision, Ticket, Sprint, Snapshot (load/save)
ledger/phases.py                PhaseMapper
ledger/timeline.py              Interval, Event, Timeline, build_timeline, find_bulk_edits
ledger/classify.py              involvement, membership, outcome, carry age, workers
ledger/buckets.py               time_split, first_vendor_hold
ledger/received.py              received_state
ledger/people.py                person_observations
ledger/metrics.py               analyze_team_sprint, squad figures, ledger entries
ledger/history.py               trends, person flags, team status over earlier sprints
ledger/analyze.py               analyze/explain/trace commands, backfill ordering
ledger/annotations.py           DM assertions
ledger/narrative.py             agent input, fallback text, validation
ledger/risks.py                 detectors and risk register
ledger/report.py                report assembly, date groups, exec projection
ledger/render/html.py           both HTML reports
ledger/render/markdown.py       executive Markdown
ledger/render/style.py          embedded CSS and the DM switch script
ledger/names_check.py           roster-name scan of executive output
ledger/issue.py                 editions and manifest
ledger/evidence.py              person and team briefs
ledger/ado/auth.py              AzTokenAuth, PatAuth
ledger/ado/transport.py         Transport protocol, UrllibTransport, AzRestTransport, retry/pacing
ledger/ado/client.py            AdoClient: wiql, revisions, iterations, states, boards, connection data
ledger/ado/pseudonym.py         Pseudonymizer, text normalisation
ledger/ado/collect.py           discovery, checkpoints, squad ledger, snapshot writing
ledger/ado/probe.py             probe checks and allowlisted export
ledger/testing/builder.py       PortfolioBuilder fixture DSL
ledger/testing/fake_ado.py      FakeTransport serving a PortfolioBuilder as ADO responses
ledger/testing/portfolio.py     golden demo portfolio
skills/delivery-setup/SKILL.md, skills/delivery-sprint-close/SKILL.md, skills/delivery-evidence/SKILL.md
scripts/install-skills.sh
AGENTS.md, README.md, .gitignore
tests/test_*.py                 one file per module
```

Actor strings used everywhere after collection: `p:<roster id>` for a roster person, `o:<32 hex>` for any other person, `s:<32 hex>` for a configured service identity, `None` for unassigned.

---

## Phase A: analysis core on synthetic data

### Task 1: Scaffold, data home, CLI shell

**Files:** Create `bin/ledger`, `ledger/__init__.py`, `ledger/__main__.py`, `ledger/cli.py`, `ledger/errors.py`, `ledger/home.py`, `.gitignore`, `README.md` (one paragraph), `tests/__init__.py`, `tests/test_home.py`, `tests/test_cli.py`.

**Interfaces — Produces:**
- `class LedgerError(Exception)` with attributes `message: str`, `hint: str`.
- `home.slug(text: str) -> str` (lowercase, non-alphanumerics to `-`, collapsed).
- `class Home`: `Home(root: Path)`; `Home.resolve(arg: Optional[str], env: Mapping[str, str]) -> Home`; `ensure() -> None`; `path(*parts: str) -> Path`; `read_json(*parts, default=None) -> Any`; `write_json(obj, *parts) -> None` (atomic: temp file + `os.replace`, mode `0600`, parent dirs `0700`); `team_sprint_dir(team: str, sprint_id: str) -> Path` = `teams/<slug(team)>/sprints/<sprint_id>`.
- `cli.main(argv: Optional[List[str]] = None) -> int`. Each command module exposes `add_parser(subparsers) -> None` and sets `func=run` where `run(args, home: Home) -> dict` returns at least `{"summary": str}`. `main` prints `summary`, or the whole dict as JSON with `--json`. A `LedgerError` prints `error: <message>` and `next: <hint>` to stderr and returns 2.
- Command `status`: summary lists whether `config.json` and `roster.json` exist and the team names.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_home.py
class HomeTest(unittest.TestCase):
    def test_resolve_precedence(self):
        self.assertEqual(Home.resolve("/tmp/a", {"DELIVERY_LEDGER_HOME": "/tmp/b"}).root, Path("/tmp/a"))
        self.assertEqual(Home.resolve(None, {"DELIVERY_LEDGER_HOME": "/tmp/b"}).root, Path("/tmp/b"))
        self.assertEqual(Home.resolve(None, {}).root, Path.home() / "delivery-ledger-data")
    def test_refuses_windows_mount(self):
        with self.assertRaises(LedgerError) as c: Home.resolve("/mnt/c/Users/x/data", {})
        self.assertIn("Linux filesystem", c.exception.hint)
    def test_write_json_is_private_and_atomic(self):
        h = Home(Path(tempfile.mkdtemp()) / "home"); h.ensure(); h.write_json({"a": 1}, "teams", "x", "m.json")
        p = h.path("teams", "x", "m.json")
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(p.parent.stat().st_mode), 0o700)
        self.assertEqual(h.read_json("teams", "x", "m.json"), {"a": 1})
        self.assertEqual(h.read_json("nope.json", default=[]), [])
    def test_slug(self): self.assertEqual(slug("Proj\\Release 1\\Sprint 37"), "proj-release-1-sprint-37")
# tests/test_cli.py
    def test_status_on_empty_home(self):   # main(["--home", tmp, "status"]) returns 0, stdout mentions "config.json: missing"
    def test_ledger_error_exit_code(self): # main(["--home", "/mnt/c/x", "status"]) returns 2, stderr has "error:" and "next:"
    def test_launcher_runs_from_other_cwd(self): # subprocess: cwd=/tmp, [repo/bin/ledger, "--home", tmp, "status"] exits 0
```

- [ ] **Step 2:** Run `python3 -m unittest discover -s tests -v`. Expected: import errors.
- [ ] **Step 3:** Implement the files. `bin/ledger` resolves its own directory through symlinks, sets `PYTHONPATH` to the repo root and `exec python3 -m ledger "$@"`; `chmod +x`. `.gitignore`: `__pycache__/`, `*.pyc`, `delivery-ledger-data/`, `.DS_Store`.
- [ ] **Step 4:** Run the tests. Expected: all pass.
- [ ] **Step 5:** `git init && git add -A && git commit -m "feat: scaffold, data home and CLI shell"`

### Task 2: Config and roster

**Files:** Create `ledger/config.py`, `tests/test_config.py`.

**Interfaces — Consumes:** `Home`, `LedgerError`. **Produces:**
- `DEFAULT_THRESHOLDS: Dict[str, float]` exactly: `window_sprints 6, planned_grace_days 1, no_criteria_desc_len 40, assigned_late_days 3, carry_flag_sprints 3, returns_flag 2, backward_flag 2, trend_window 3, trend_runs 3, nothing_sprints 2, nothing_min_availability 0.5, quiet_days 5, status_returned_tickets 2, long_wait_days 5, long_wait_sprint_ends 2, unready_min 3, unready_share 0.3334, midsprint_min 3, nothing_in_progress_days 3, capacity_share 0.7, bulk_edit_count 10, bulk_edit_seconds 60`.
- Frozen dataclasses: `PhaseRule(state: str, phase: str, column: Optional[str]=None, column_done: Optional[bool]=None, wait_label: Optional[str]=None)`; `Team(name, ado_team, area_paths: Tuple[str,...], phases: Tuple[PhaseRule,...], column_field: Optional[str]=None, done_field: Optional[str]=None)`; `Config(org_url, project, timezone, teams, unit_level, unit_types, not_delivered_states: FrozenSet[str], not_delivered_reasons: FrozenSet[str], service_identities, holidays: FrozenSet[date], thresholds: Dict[str,float], vendor_label: str, agent_text_access: str)`.
- `Config.team(name) -> Team`; `Config.team_for_area(area: str) -> Optional[str]` (longest configured prefix on `\` boundaries, case-insensitive).
- `Assignment(team, start: date, end: Optional[date], allocation: float)`; `Absence(start: date, end: date, note: str)`; `Person(id, display_name, identity_ids, aliases, assignments, absences)`.
- `Roster(people: List[Person])`: `person(pid) -> Person`; `match_identity(identity_id: Optional[str], names: Iterable[str]) -> Optional[str]` (id first, then case-insensitive alias); `vendor_on(actor: Optional[str], team: str, at: datetime, tz) -> bool`; `assignment_on(pid, team, d: date) -> Optional[Assignment]`; `people_on(team, start: date, end: date) -> List[Person]`.
- `load_config(home) -> Config`, `load_roster(home) -> Roster`, `validate(cfg, roster) -> None` raising `LedgerError`.

- [ ] **Step 1: Write failing tests** (build JSON dicts in the test, write with `Home.write_json`)

```python
def test_defaults_applied(self):       # missing thresholds/unit_level -> DEFAULT_THRESHOLDS, "stories", ("User Story","Product Backlog Item","Bug"), agent_text_access "none", vendor_label "Vendor team"
def test_overlapping_area_paths_rejected(self):  # teams with "P\\A" and "P\\A\\B" -> LedgerError naming both teams
def test_team_for_area_longest_prefix(self):     # "P\\Claims\\Intake" -> "Claims"; "P\\ClaimsX" -> None
def test_allocation_over_one_rejected(self):     # two overlapping assignments 0.6 + 0.6 -> LedgerError naming the person id
def test_vendor_on_respects_assignment_dates(self):
    # anna assigned to Claims 2026-10-01..2026-10-31
    self.assertTrue(r.vendor_on("p:anna", "Claims", utc(2026,10,15,12), tz))
    self.assertFalse(r.vendor_on("p:anna", "Claims", utc(2026,11,2,12), tz))
    self.assertFalse(r.vendor_on("p:anna", "Pricing", utc(2026,10,15,12), tz))
    self.assertFalse(r.vendor_on("o:abc", "Claims", utc(2026,10,15,12), tz)); self.assertFalse(r.vendor_on(None, "Claims", utc(2026,10,15,12), tz))
def test_match_identity_id_then_alias(self)
def test_bad_timezone_and_unknown_team_rejected(self)
def test_invalid_phase_or_unit_level_rejected(self)   # phase not in todo/doing/handoff/done/removed
```

- [ ] **Step 2:** Run; expect failures. **Step 3:** Implement. **Step 4:** Run; expect pass.
- [ ] **Step 5:** `git commit -am "feat: config and roster loading with validation"` (add new files)

### Task 3: Working calendar

**Files:** Create `ledger/workcal.py`, `tests/test_workcal.py`.

**Interfaces — Produces:**
- `class WorkCalendar(tz_name: str, holidays: Iterable[date])`: `tz` attribute; `is_working(d: date) -> bool`; `day_start(d: date) -> datetime` (UTC instant of local midnight); `local_date(ts: datetime) -> date`; `working_days(start: datetime, end: datetime) -> float`; `working_dates(first: date, last: date) -> List[date]` (inclusive); `add_working_days(d: date, n: int) -> date`; `sprint_bounds(start_date: date, finish_date: date) -> Tuple[datetime, datetime]` (local midnight of start, local midnight after finish).
- `display_days(x: float) -> float` rounds to the nearest 0.5.

`working_days`: for each local working date overlapping the interval, add (overlap seconds) / (that local day's length in seconds), so a 23- or 25-hour day still counts as 1.0.

- [ ] **Step 1: Write failing tests** (tz `Europe/Zurich`)

```python
def test_full_week_is_five(self):  self.assertEqual(cal.working_days(z(2026,11,2), z(2026,11,9)), 5.0)
def test_weekend_counts_zero(self): self.assertEqual(cal.working_days(z(2026,11,7,9), z(2026,11,8,18)), 0.0)
def test_half_day(self):            self.assertAlmostEqual(cal.working_days(z(2026,11,3,6), z(2026,11,3,18)), 0.5)
def test_holiday_excluded(self):    # holidays {2026-11-04}: Mon..Fri week == 4.0
def test_dst_day_counts_one(self):  self.assertAlmostEqual(cal.working_days(z(2026,10,23), z(2026,10,27)), 2.0)  # Fri + Mon; clocks change Sun 25 Oct
def test_local_date_near_midnight(self): self.assertEqual(cal.local_date(datetime(2026,11,5,23,30,tzinfo=timezone.utc)), date(2026,11,6))
def test_sprint_bounds(self):       # (2026-10-26, 2026-11-06) -> local 26 Oct 00:00 .. local 7 Nov 00:00, as UTC
def test_add_working_days_skips_weekend(self): self.assertEqual(cal.add_working_days(date(2026,11,6), 1), date(2026,11,9))
def test_display_days(self):        self.assertEqual([display_days(x) for x in (0.2, 0.26, 4.74, 4.76)], [0.0, 0.5, 4.5, 5.0])
def test_unknown_zone_raises_ledger_error(self)  # hint mentions tzdata and that nothing may be installed
```

- [ ] **Steps 2–4:** fail, implement, pass. **Step 5:** `git commit -m "feat: working-day calendar"`

### Task 4: Model, phase mapper, snapshot files

**Files:** Create `ledger/model.py`, `ledger/phases.py`, `tests/test_model.py`, `tests/test_phases.py`.

**Interfaces — Produces:**
- `class Phase(enum.IntEnum)`: `REMOVED=-1, TODO=0, DOING=1, HANDOFF=2, DONE=3`.
- `@dataclass Revision`: `rev: int, changed: datetime, by: Optional[str], state: str, reason: str, column: Optional[str], column_done: Optional[bool], iteration: str, area: str, assignee: Optional[str], type: str, project: str, parent: Optional[int], tags: Tuple[str,...], desc_hash: str, desc_len: int, ac_hash: str, ac_len: int`. `to_json() -> dict`, `Revision.from_json(d) -> Revision`.
- `@dataclass Ticket`: `id: int, revisions: List[Revision], title: str = "", description: str = "", acceptance: str = "", parent_title: str = ""`.
- `@dataclass(frozen=True) Sprint`: `team: str, id: str, name: str, path: str, start: datetime, end: datetime, period_start: datetime`. `period_start` is the previous sprint's `end` for the same team, or `start` for the first; it makes a ticket closed on the weekend between sprints count in the later one.
- `@dataclass Snapshot`: `tickets: List[Ticket], squad: List[Ticket], sprints: Dict[str, List[Sprint]], meta: Dict[str, Any]`; `save(dir: Path) -> None` writes `tickets.jsonl`, `squad.jsonl`, `sprints.json`, `meta.json` (all `0600`); `Snapshot.load(dir: Path) -> Snapshot`. `meta` keys: `cutoff` (ISO UTC), `account`, `auth_mode`, `coverage` (`{"missing": [ids], "unreadable": [ids]}`), `tool_version`.
- `class PhaseMapper(team: Team, cfg: Config)`: `map(state: str, column: Optional[str], column_done: Optional[bool]) -> Tuple[Phase, Optional[str]]` (first matching rule; a rule with `column=None` matches any column); `not_delivered(state: str, reason: str) -> bool`.

- [ ] **Step 1: Write failing tests**

```python
def test_snapshot_round_trip(self)            # save then load equals original, including None fields and tz-aware datetimes
def test_first_matching_rule_wins(self):
    # rules: (Active, column "QA") -> handoff/"QA"; (Active) -> doing
    self.assertEqual(m.map("Active", "QA", None), (Phase.HANDOFF, "QA"))
    self.assertEqual(m.map("Active", "Dev", None), (Phase.DOING, None))
def test_split_column_done_half_is_handoff(self)   # rule (Active, column "Dev", column_done True) -> handoff/"review"
def test_unmapped_state_is_an_error(self):         # Review Focus 1
    with self.assertRaises(LedgerError) as c: m.map("Ready for UAT", None, None)
    self.assertIn("Ready for UAT", c.exception.message); self.assertIn("Claims", c.exception.message); self.assertIn("phases", c.exception.hint)
def test_not_delivered_by_reason_or_state(self)    # reasons {"Duplicate"} -> True for ("Closed","Duplicate"), False for ("Closed","Completed")
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: data model, phase mapping and snapshot files"`

### Task 5: Fixture builder

**Files:** Create `ledger/testing/__init__.py`, `ledger/testing/builder.py`, `tests/test_builder.py`.

**Interfaces — Consumes:** Tasks 2–4. **Produces:**

```python
b = PortfolioBuilder(tz="Europe/Zurich")
b.team("Claims", area="Demo\\Claims", sprints=[("S36", "2026-10-12", "2026-10-23"), ("S37", "2026-10-26", "2026-11-06")])
anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")       # returns "p:anna"; kwargs end=None, allocation=1.0
b.absence("anna", "2026-11-02", "2026-11-03"); b.assign("anna", "Pricing", start=..., end=..., allocation=...)
qa = b.other("qa")                                                           # stable "o:<32 hex>"
bot = b.service("bot")                                                       # "s:<32 hex>"
t = b.ticket(41872, team="Claims", type="User Story", title="...", description="", acceptance="", parent=None, parent_title="")
t.on("2026-10-26 09:00", state="New", iteration="S37", assignee=anna, by=qa)  # local time in tz; unspecified fields carry forward
t.on("2026-10-27 10:00", state="Active")                                     # `by` defaults to the current assignee
t.edit("2026-10-28 10:00", description="longer text", acceptance="Given ...") # text revision; hashes/lengths update
b.squad_ticket(50001, team="Claims").on("2026-10-27 09:00", state="Active").on("2026-11-03 09:00", state="Closed")
snap = b.snapshot(cutoff="2026-11-07 08:00"); cfg = b.config(); roster = b.roster(); b.write(home)  # write() saves config.json, roster.json and the snapshot under the latest sprint of each team
```

Default phase rules for every builder team: `New`→todo, `Active`→doing, `In QA`→handoff "QA", `In Review`→handoff "review", `Closed`→done, `Removed`→removed. `b.team(..., phases=[...])` overrides. `iteration="S37"` is expanded to the sprint's path `Demo\\<team>\\<name>`; `iteration=None` means the team's backlog path. Vendor tickets are also added to `snap.squad` automatically (identity-free), so squad figures include them. `on()` returns the ticket for chaining.

- [ ] **Step 1: Write failing tests**

```python
def test_revisions_carry_forward(self)        # second on() keeps iteration and assignee; rev numbers 1,2; changed is UTC
def test_sprint_bounds_and_period_start(self) # S37.start == local 26 Oct 00:00; S37.period_start == S36.end
def test_edit_changes_hash_and_length(self)
def test_vendor_ticket_appears_in_squad_without_identities(self)  # squad revision has by None and assignee None, empty title
def test_write_then_load(self)                # load_config/load_roster/Snapshot.load succeed and validate
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "test: fixture builder for synthetic ticket histories"`

### Task 6: Timeline and events

**Files:** Create `ledger/timeline.py`, `tests/test_timeline.py`.

**Interfaces — Consumes:** `Ticket`, `PhaseMapper`, `Config`. **Produces:**
- `@dataclass(frozen=True) Interval`: `start, end, phase: Phase, wait_label: Optional[str], state: str, holder: Optional[str], iteration: str, area: str, not_delivered: bool`.
- `@dataclass(frozen=True) Event`: `at: datetime, rev: int, by: Optional[str], from_phase: Optional[Phase], to_phase: Phase, admin: bool, backward: bool, is_return: bool, reopen: bool, to_handoff: bool, holder_before: Optional[str]`. One event per phase change (the first revision has `from_phase=None`).
- `@dataclass Timeline`: `ticket_id: int, intervals: List[Interval], events: List[Event], revisions: List[Revision]`; `at(ts) -> Optional[Interval]`; `phase_at(ts) -> Optional[Phase]`; `clip(start, end) -> List[Interval]`; `first(pred) -> Optional[Interval]`.
- `find_bulk_edits(tickets: Iterable[Ticket], count: int, seconds: int) -> Set[Tuple[int, int]]`: `(ticket id, rev)` for revisions where one actor changed at least `count` distinct tickets within `seconds`.
- `build_timeline(ticket: Ticket, mapper: PhaseMapper, cutoff: datetime, bulk: Set[Tuple[int,int]]) -> Timeline`.

Rules: a new interval starts when phase, wait label, state, holder, iteration or area changes; the last interval ends at `cutoff`. An event is `admin` when its revision is in `bulk`, its `by` starts with `s:`, or `project` or `type` differs from the previous revision. `backward` = lower rank and not admin; `is_return` = backward out of `HANDOFF` or `DONE`; `reopen` = return out of `DONE`. Moves to or from `REMOVED` are never backward.

- [ ] **Step 1: Write failing tests**

```python
def test_intervals_split_on_holder_and_phase(self)
    # New(anna) -> Active(anna) -> In QA(qa) -> Closed : 4 intervals, phases TODO, DOING, HANDOFF, DONE; last ends at cutoff
def test_column_only_change_is_a_phase_change(self)   # state stays Active, column Dev -> QA under a column rule: event to_phase HANDOFF, to_handoff True
def test_return_from_qa(self):  # In QA -> Active: backward True, is_return True, reopen False, holder_before == qa
def test_reopen(self):          # Closed -> Active: reopen True
def test_service_account_change_is_admin_not_backward(self)   # In QA -> Active by b.service("bot"): admin True, backward False, is_return False
def test_type_or_project_change_is_admin(self)
def test_bulk_edit_marked_admin(self)   # one actor moves 10 tickets Active -> New within 30 s: all admin; 9 tickets: none admin
def test_removed_is_not_backward(self)
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: ticket timelines with administrative and backward-move detection"`

### Task 7: Classification

**Files:** Create `ledger/classify.py`, `tests/test_classify.py`.

**Interfaces — Consumes:** `Timeline`, `Sprint`, `Roster`, `WorkCalendar`. **Produces:**
- `OUTCOMES = ("delivered", "closed_not_delivered", "reclosed", "awaiting_acceptance", "carried_in_progress", "carried_not_started", "active_outside_sprint", "assigned_outside_sprint")`.
- `is_member(tl, sprint) -> bool`: some interval overlapping `[sprint.start, sprint.end)` has `iteration == sprint.path`.
- `is_planned(tl, sprint, cal, grace_days: int) -> bool`: a member at some time before the end of the `grace_days`-th working day of the sprint.
- `left_sprint(tl, sprint) -> bool`: planned-or-member earlier in the sprint, iteration at `sprint.end` is neither this sprint's path nor a later sprint of the team, and not done.
- `involvement(tl, sprint, team: str, roster, tz) -> Optional[str]`: `"worked"` if a roster person on `team` held it in `DOING` at any time before `sprint.end`; else `"assigned"` if a roster person on `team` was holder during `[sprint.period_start, sprint.end)` while `TODO`; else `None`.
- `outcome(tl, sprint, earlier: Sequence[Sprint]) -> Optional[str]`, by phase at `sprint.end` (the instant before): `None` if `REMOVED`, or if `DONE` at `sprint.period_start` with no phase change during the period; `DONE` → `closed_not_delivered` if the interval is `not_delivered`, else `reclosed` if it was `DONE` at the end of any sprint in `earlier`, else `delivered`; `HANDOFF` → `awaiting_acceptance`; `DOING` → `carried_in_progress` if a member of this sprint else `active_outside_sprint`; `TODO` → `carried_not_started` if a member else `assigned_outside_sprint`.
- `@dataclass CarryAge`: `sprints_carried: int, carried_since: Optional[str], trail: List[str], sprints_waiting: int`. `carry_age(tl, sprints_upto: Sequence[Sprint], team, roster, tz) -> CarryAge` counts, over sprints from the first one in which a roster person on `team` held the ticket through the last in `sprints_upto`, outcomes `carried_*` into `sprints_carried` and `awaiting_acceptance` into `sprints_waiting`; `trail` is the sprint names where it was a member from that first sprint on.
- `workers(tl, upto: datetime, team, roster, cal) -> Dict[str, float]`: roster person id → working days as holder in `DOING` on `team` before `upto`.

- [ ] **Step 1: Write failing tests** (builder, team Claims with S34–S37)

```python
def test_handoff_then_closed_is_delivered_and_worked(self)     # anna Active, reassigned to qa In QA, Closed in S37 -> involvement "worked", outcome "delivered"
def test_closed_on_weekend_between_sprints_counts_in_later_sprint(self)  # Closed Sat 24 Oct -> S36 outcome carried_in_progress, S37 "delivered"
def test_done_before_period_is_not_in_play(self)               # Closed in S35, untouched since -> outcome(S37) is None
def test_reopened_and_closed_again_is_reclosed(self)           # delivered S36, reopened and closed in S37 -> S37 "reclosed"
def test_duplicate_closure_not_delivered(self)                 # reason "Duplicate" in cfg.not_delivered_reasons
def test_awaiting_acceptance_is_not_carryover(self):
    self.assertEqual(outcome(tl, s37, [s36]), "awaiting_acceptance"); self.assertEqual(carry_age(tl, [s36, s37], "Claims", roster, tz).sprints_carried, 0)
def test_carried_four_sprints(self):                           # Active in S34..S37 -> sprints_carried 4, carried_since "S34", trail ["S34","S35","S36","S37"]
def test_carry_age_starts_at_first_vendor_hold(self)           # other party held S33–S35, anna from S36 -> sprints_carried 2, carried_since "S36"
def test_moved_to_next_sprint_early_still_member(self)         # iteration changed to S38 on day 8 of S37 -> is_member(S37) True, outcome carried_in_progress
def test_added_mid_sprint_not_planned(self)                    # entered S37 on day 4 -> is_member True, is_planned False
def test_never_in_any_sprint(self):                            # Review Focus 3: iteration None, Active -> "active_outside_sprint"
def test_iteration_path_not_in_calendar_treated_as_no_sprint(self)  # Review Focus 3: iteration "Demo\\Claims\\Old Bucket" -> is_member False for every sprint, no exception
def test_assigned_never_started(self)                          # anna assignee, state New, member -> involvement "assigned", outcome "carried_not_started"
def test_triage_by_vendor_then_built_by_other_is_not_worked(self)   # anna assignee in New, qa does Active..Closed -> involvement(S37) "assigned", workers == {}
def test_removed_is_none(self)
def test_workers_two_people(self)                              # anna 3 working days, bence 2 -> {"anna": 3.0, "bence": 2.0}
def test_holder_outside_assignment_dates_not_vendor(self)      # person left the team before the sprint -> involvement None
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: sprint membership, outcomes and carryover age"`

### Task 8: Time split and state when received

**Files:** Create `ledger/buckets.py`, `ledger/received.py`, `tests/test_buckets.py`, `tests/test_received.py`.

**Interfaces — Produces:**
- `first_vendor_hold(tl, team, roster, tz) -> Optional[datetime]`: start of the first interval whose holder is a roster person on `team` (any phase).
- `time_split(tl, start: datetime, end: datetime, cal, team, roster) -> Dict[str, float]` with keys `vendor`, `other_party`, `unassigned`, `parked`, and `waiting:<label>` (label `other` when none). Only time at or after `first_vendor_hold` and inside `[start, end)`; `DONE`, `REMOVED` and `TODO` before the ticket ever entered `DOING` are not counted. `TODO` after having been in `DOING` is `parked` regardless of holder.
- `classified_days(tl, start, end, cal, team, roster) -> float`: working days in counted phases; test invariant `abs(sum(split.values()) - classified_days) < 1e-9`.
- `received_state(ticket, tl, sprint, cal, team, roster, cfg) -> Dict[str, Any]` with keys `received_at`, `no_criteria: bool`, `text_changes_after: int`, `added_mid_sprint: bool`, `assigned_late: bool`. `no_criteria`: at the revision in effect at `received_at`, `ac_len == 0` and `desc_len < thresholds["no_criteria_desc_len"]`. `text_changes_after`: later revisions where `desc_hash` or `ac_hash` differs from the previous revision and `by` does not start with `s:`. `assigned_late`: `received_at` falls in the sprint with at most `assigned_late_days` working days left.

- [ ] **Step 1: Write failing tests**

```python
def test_split_matches_mockup_evidence_card(self):
    # anna holds Active 4 working days, QA 9, Active 2, QA 12, Active 3, In Review 13 (labels QA, QA, review)
    s = time_split(tl, first, cutoff, cal, "Pricing", roster)
    self.assertEqual({k: display_days(v) for k, v in s.items() if v}, {"vendor": 9.0, "waiting:QA": 21.0, "waiting:review": 13.0})
def test_buckets_are_exhaustive_and_exclusive(self)   # doing+other, doing+unassigned, handoff+unassigned, parked: sum == classified_days
def test_closed_gap_before_reopen_not_counted(self)
def test_time_before_first_vendor_hold_not_counted(self)
def test_clipped_to_sprint(self)                       # 4-sprint ticket: split over S37 only sums to <= 10.0
def test_no_criteria_measured_at_vendor_receipt(self): # other party started with empty text; criteria added before anna received it -> no_criteria False
def test_no_criteria_true_and_text_changed_after(self) # empty at receipt, two later edits by PO, one by service account -> True, 2
def test_assigned_late(self)                           # first assigned to anna with 2 working days left -> True; with 5 left -> False
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: where ticket time went and state of work when received"`

### Task 9: Person observations

**Files:** Create `ledger/people.py`, `tests/test_people.py`.

**Interfaces — Produces:** `person_observations(pid: str, team: str, sprint: Sprint, timelines: Dict[int, Timeline], cal, roster, cfg) -> Dict[str, Any]` with keys:
`available_days` (working dates in the sprint inside an assignment to `team`, each times that day's allocation, minus absence days), `nominal_days` (the same without absences), `days_nothing_in_progress` (available working dates on which the person held no ticket in `DOING` at any moment; a date counts as its allocation), `todo_assigned_max` (largest number of `TODO` tickets held on such a date), `concurrency_avg`, `concurrency_max` (tickets held in `DOING`, sampled per working date), `pickup_median` (working days from becoming holder to the ticket's next entry to `DOING` while still holder; `None` if no samples), `board_change_days` (distinct local dates in the sprint with a revision `by` this person), `last_board_change` (ISO date or `None`, up to `sprint.end`), `joiner_days_to_first_delivery` (only when the person's first assignment starts inside the window given by `cfg.thresholds["window_sprints"]`; working days from that start to the first `DONE` entry of a ticket they worked on; `None` if not yet), `comparison_break: bool` (allocation or team changed since the previous sprint's start).

- [ ] **Step 1: Write failing tests**

```python
def test_leave_reduces_available_days(self)          # 10-day sprint, 4 days absence -> available 6.0, nominal 10.0
def test_part_time_allocation(self)                  # allocation 0.5 -> available 5.0
def test_moved_teams_mid_sprint(self)                # on Claims first 4 working days only -> available 4.0 for Claims, 6.0 for Pricing
def test_days_nothing_in_progress(self)              # holds Active Mon–Wed of week 1 only, 2 New tickets assigned -> 7.0, todo_assigned_max 2
def test_concurrency(self)                           # three tickets Active in overlap -> max 3
def test_pickup_median(self)
def test_board_change_days_and_last(self)            # revisions by anna on 3 distinct dates incl. a Saturday -> 3; last is that date
def test_joiner(self)                                # start 2026-10-26, first delivered 2026-11-02 -> 5.0
def test_absent_whole_sprint(self)                   # available 0.0, days_nothing_in_progress 0.0, no exception
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: per-person observations"`

### Task 10: Team-sprint metrics, ledger, squad figures

**Files:** Create `ledger/metrics.py`, `tests/test_metrics.py`.

**Interfaces — Consumes:** Tasks 4–9. **Produces:**
- `RULES_VERSION = "1"`, `SCHEMA = 1`.
- `analyze_team_sprint(snap: Snapshot, cfg: Config, roster: Roster, team: str, sprint: Sprint) -> Tuple[Dict, Dict]` returning `(metrics, ledger)`.
- `squad_delivered(snap, cfg, team, sprint) -> List[int]`: ids in `snap.squad` whose area at `sprint.end` maps to `team`, type in `cfg.unit_types`, and `outcome(...) == "delivered"`, using the same `outcome` function.

`metrics` shape (all durations already passed through `display_days`; medians `None` when there are no samples):

```
{"schema":1,"rules_version":"1","team":str,"sprint":{"id","name","start","finish"},"cutoff":str,"backfilled":false,
 "figures":{"delivered":int,"squad_delivered":int,"closed_not_delivered":int,"reclosed":int,
   "awaiting_acceptance":{"count":int,"longest_wait_days":float|null},
   "carried":{"in_progress":int,"not_started":int},"active_outside_sprint":int,"assigned_outside_sprint":int,
   "planned":{"total":int,"delivered":int,"awaiting":int,"carried":int,"left":int},
   "returns":{"events":int,"tickets":int,"handed_off":int},
   "median_days_with_vendor":float|null,"median_elapsed_days":float|null,
   "time_split":{bucket:float},
   "received":{"received":int,"no_criteria":int,"text_changed":int,"added_mid_sprint":int,"assigned_late":int},
   "capacity":{"available":float,"nominal":float}},
 "people":{pid:{…person_observations…,"delivered_worked_on":int,"handed_off":int,"in_progress":{"count":int,"oldest_days":float|null},
   "carried":{"count":int,"longest_sprints":int},"backward_moves":int,"returns":int,"median_days_in_progress":float|null}},
 "tickets":{str(id):{"type","title","parent","parent_title","state","outcome","involvement","planned":bool,"left_sprint":bool,
   "workers":{pid:float},"sprints_carried":int,"carried_since":str|null,"trail":[str],"sprints_waiting":int,"days_waiting":float,
   "wait_label":str|null,"backward_moves":int,"returns":[{"rev":int,"at":str,"reopen":bool}],"handed_off_in_sprint":bool,
   "received":{…},"outside_teams":bool}},
 "coverage":{"missing":[int],"unreadable":[int]},"warnings":[str]}
```

Rules: a ticket is included when `involvement` is not `None` and `outcome` is not `None`, and its area at `sprint.end` maps to `team` (a ticket mapping to no team goes into the metrics of the team of the person's assignment with `"outside_teams": true` and is excluded from `figures`). Tickets with involvement `"assigned"` count only in `carried.not_started`, `assigned_outside_sprint` and `received`; never in `delivered`. `planned` counts tickets with `planned` true and involvement `"worked"`, partitioned by outcome (`left` when `left_sprint`). `returns.events`/`tickets` are non-admin return events inside the sprint period; `handed_off` is tickets with a `to_handoff` event in the period whose `holder_before` is a vendor person. `median_days_with_vendor` is over delivered tickets, the sum of `workers`; `median_elapsed_days` is working days from first `DOING` to the delivering `DONE` entry. A person's `returns` are return events whose preceding `DOING` holder was that person. `warnings` gets `"vendor delivered is not a subset of squad delivered: [ids]"` when that invariant fails.

`ledger` maps dotted figure keys to lists: `"figures.delivered": [ids]`, `"figures.planned.carried": [ids]`, `"figures.returns.events": [{"ticket": id, "rev": n}]`, `"people.<pid>.delivered_worked_on": [ids]`, and so on for every count in `figures` and `people`. Duration and median figures list the ticket ids they were computed over.

- [ ] **Step 1: Write failing tests**

```python
def test_counts_for_small_sprint(self)       # 2 delivered (one via QA handoff), 1 awaiting, 1 carried in progress, 1 duplicate -> figures match; squad_delivered includes 3 non-vendor
def test_vendor_delivered_subset_of_squad(self):
    self.assertTrue(set(ledger["figures.delivered"]) <= set(ledger["figures.squad_delivered"])); self.assertEqual(m["warnings"], [])
def test_planned_line_partitions(self):      # total == delivered + awaiting + carried + left
def test_two_people_both_count_ticket_team_counts_once(self)   # figures.delivered 1; people anna and bence delivered_worked_on 1 each
def test_assigned_only_never_delivered(self)
def test_returns_events_vs_tickets(self)     # one ticket returned 3 times in sprint -> events 3, tickets 1
def test_empty_team_sprint(self):            # Review Focus 2
    m, l = analyze_team_sprint(snap, cfg, roster, "Data", s37)
    self.assertEqual(m["figures"]["delivered"], 0); self.assertIsNone(m["figures"]["median_days_with_vendor"]); self.assertEqual(m["figures"]["time_split"], {})
def test_every_count_has_ledger_entry(self): # walk figures and people; each int leaf key exists in ledger and len(ledger[key]) == value
def test_each_ticket_one_outcome(self)       # every tickets[*].outcome in OUTCOMES
def test_ticket_outside_configured_areas(self)   # outside_teams True, not in figures
def test_tasks_counting_level(self)          # cfg.unit_level "tasks", unit_types ("Task",): tasks counted, parent story title in parent_title
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: team-sprint metrics with a ledger entry behind every figure"`

### Task 11: History: trends, flags, status; analyze, explain and trace commands

**Files:** Create `ledger/history.py`, `ledger/analyze.py`, `tests/test_history.py`, `tests/test_analyze_cmd.py`; modify `ledger/cli.py` to register commands.

**Interfaces — Produces:**
- `history.apply(metrics: Dict, earlier: List[Dict], cfg: Config) -> None` (mutates `metrics`; `earlier` is the same team's metrics for previous sprints, oldest first). Adds `figures.trend_delivered: List[int]` (up to `window_sprints` values ending with this sprint), `figures.returns.cohort: {"sprint": prev name, "handed_off": int, "returned": int}` (tickets with `handed_off_in_sprint` in the previous sprint's metrics that have a return in the previous or this sprint), `figures.returns.cohort_so_far` (same for this sprint's handoffs), `status: {"label","rules":[str],"override":null}`, and per person `trend_delivered`, `flags: List[str]`, `context: List[str]`.
- Flag and status rules exactly as spec 13.3 and 13.4 using `cfg.thresholds`. "Fewer than usual": for each of the last `trend_runs` sprints, the value is strictly below the mean of the `trend_window` sprints before it; needs `trend_runs + trend_window` sprints of history, otherwise not triggered; for a person the run must not cross a `comparison_break`. Status rule strings: `"Chronic carryover"`, `"Returns"`, `"Fewer delivered than usual"`.
- Context strings: `"<n> days leave"`, `"Joined in <sprint>"`, `"<n> days with nothing in progress"`, `"Allocation <x>"`, `"Moved team"`.
- `analyze.run_analyze(home, cfg, roster, team: str, sprint_id: Optional[str]) -> Dict`: loads the snapshot from the latest collected sprint dir of the team (or `sprint_id`), analyses every sprint in the window oldest first that has no issued metrics, marks all but the snapshot's own sprint `backfilled: true`, applies history, writes `metrics.json` and `ledger.json` into each sprint dir.
- Commands: `analyze --team T [--sprint ID]`; `explain --team T --sprint ID FIGURE` (prints the ledger list with ticket type, title and outcome; unknown figure → `LedgerError` listing valid keys); `trace TICKET_ID [--team T]` (prints, from the latest snapshot containing the ticket: one line per interval with local start, working days, phase, state, holder display name or pseudonym, sprint name; then one line per event with flags; then outcome per sprint in the window).

- [ ] **Step 1: Write failing tests**

```python
def test_carried_more_than_two_sprints_flag(self)    # anna has an in-progress ticket with sprints_carried 3 -> flag present; with 2 -> absent
def test_awaiting_acceptance_never_flags_person(self)
def test_returned_repeatedly_and_moving_backward(self)
def test_fewer_delivered_than_usual_needs_six_sprints(self)  # trend [3,3,3,2,2,2] -> flag; [3,2,2,2] -> no flag
def test_trend_does_not_cross_comparison_break(self)
def test_nothing_delivered_two_sprints_with_availability(self)   # on leave 60% of sprint -> not flagged
def test_no_flags_and_context(self):  self.assertEqual(p["flags"], []); self.assertIn("4 days leave", p["context"])
def test_team_status_levels(self)     # 0 rules "On track"; chronic only "Needs attention"; chronic + 2 returned tickets "At risk"; rules listed
def test_return_cohort(self)          # 3 handed off in S36, 1 returned in S37 -> cohort {"sprint":"S36","handed_off":3,"returned":1}
def test_backfill_orders_and_marks(self)   # run_analyze on a 4-sprint builder home writes 4 metrics.json; earliest three backfilled True
def test_explain_lists_tickets(self); def test_explain_unknown_figure(self)
def test_trace_output(self)           # contains "In QA", "9.0", holder display name "Anna K.", "returned"
def test_unmapped_state_surfaces_from_analyze(self)   # Review Focus 1 end to end: exit code 2, message names the state
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: trends, flags, team status, and analyze/explain/trace commands"`

---

## Phase B: collection and probe

### Task 12: Auth and transports

**Files:** Create `ledger/ado/__init__.py`, `ledger/ado/auth.py`, `ledger/ado/transport.py`, `tests/test_transport.py`.

**Interfaces — Produces:**
- `ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"`.
- `class AzTokenAuth(run=subprocess.run, now=time.time)`: `header() -> str` (`"Bearer <token>"`); obtains a token with `az account get-access-token --resource <ADO_RESOURCE> -o json`, caches it, and re-acquires when less than 300 seconds remain (parse `expires_on` epoch, else `expiresOn` local time). `mode = "az"`. Failure → `LedgerError` with hint `az login`.
- `class PatAuth(pat: str)`: `header()` = `"Basic " + b64(":" + pat)`; `mode = "pat"`.
- `@dataclass Response`: `status: int, headers: Dict[str, str], body: Any` (parsed JSON or `None`).
- `class Transport` protocol: `request(method: str, url: str, body: Optional[dict] = None) -> Response`.
- `class UrllibTransport(auth, allowed_host: str, opener=None)`: refuses any URL whose host is not `allowed_host` (`LedgerError`); honours `HTTPS_PROXY` and `SSL_CERT_FILE` through the default context; never passes an unverified context.
- `class AzRestTransport(allowed_host, run=subprocess.run)`: `az rest --method <m> --url <u> --resource <ADO_RESOURCE> [--body <json>] -o json`. Used when direct HTTPS fails; it uses the same signed-in account as `AzTokenAuth`.
- `class PacedTransport(inner, sleep=time.sleep, rand=random.random, max_attempts=6)`: retries 429, 503 and connection errors with exponential backoff plus jitter capped at 60 s; honours `Retry-After` on any response, including a 200; one re-acquire-and-retry on 401; raises `LedgerError` after `max_attempts`.

- [ ] **Step 1: Write failing tests** (inject fake `run`, `opener`, `sleep`)

```python
def test_token_cached_and_refreshed_before_expiry(self)   # two header() calls -> 1 az call; advance clock to 200 s before expiry -> 2nd az call
def test_az_failure_hint(self)
def test_urllib_refuses_other_host(self)                  # url on example.com -> LedgerError, opener never called
def test_retry_after_on_success_is_obeyed(self)           # 200 with Retry-After: 7 -> sleep(7) before next request
def test_429_then_success(self); def test_gives_up_after_max_attempts(self)
def test_401_reacquires_once(self)
def test_az_rest_transport_builds_command(self)           # argv contains "--resource", ADO_RESOURCE, "--method", "post", "--body"
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: ADO authentication and paced transports"`

### Task 13: ADO client and fake ADO

**Files:** Create `ledger/ado/client.py`, `ledger/testing/fake_ado.py`, `tests/test_client.py`.

**Interfaces — Produces:**
- `class AdoClient(transport: Transport, org_url: str, project: str, api="7.1")`:
  - `connection() -> Dict` (`GET {org}/_apis/connectionData`) → `{"id","name"}` of the signed-in account.
  - `wiql_ids(where: str, id_range: Tuple[int, int] = (1, 2_000_000), batch_limit: int = 19000) -> List[int]`: `POST {org}/{project}/_apis/wit/wiql` with `SELECT [System.Id] FROM WorkItems WHERE (<where>) AND [System.Id] >= a AND [System.Id] < b`. If the response is an error mentioning the size limit, times out, or returns `>= batch_limit` ids, split the range in half and recurse; a range of width 1 that still fails raises `LedgerError`. Returns sorted unique ids. Optional `on_partition(range, ids)` callback for checkpointing.
  - `revisions(ticket_id: int, page: int = 200) -> List[Dict]`: `GET …/_apis/wit/workItems/{id}/revisions?$top={page}&$skip={n}` until a short page; verifies `rev` numbers are contiguous from 1, else `LedgerError`. 404 → raises `NotFound(ticket_id)`; 403 → `Unreadable(ticket_id)` (both subclasses of `LedgerError`).
  - `iterations(ado_team: str) -> List[Dict]` (`{"id","name","path","start": date|None,"finish": date|None}`).
  - `states(work_item_type: str) -> List[Dict]` (`{"name","category"}`).
  - `board_columns(ado_team: str) -> List[Dict]` (per board: `{"board","column_field","done_field","columns":[{"name","split": bool}]}`).
- `class FakeAdo(builder: PortfolioBuilder, identities: Dict[str, Dict], page_cap: Optional[int] = None, wiql_cap: int = 20000)` implementing `Transport`: serves the endpoints above from the builder's tickets using real-looking payloads (`fields` dict with `System.*` and `Microsoft.VSTS.*` reference names; identity fields as `{"displayName","uniqueName","id","descriptor"}`); evaluates the WIQL forms the collector emits (Task 15) by parsing the collector's own clause markers; supports failure injection: `fail_next(status, headers={})`, `drop_after(n_requests)`; records `requests` for assertions.

- [ ] **Step 1: Write failing tests**

```python
def test_revisions_paged(self)                 # 450-revision ticket, page 200 -> 3 requests, 450 revisions, contiguous
def test_revisions_gap_is_error(self)
def test_wiql_splits_on_full_batch(self)       # wiql_cap 20000, batch_limit 5: 12 matching ids -> all 12 returned, more than one request
def test_wiql_splits_on_size_error(self)       # fake returns the size-limit error for wide ranges
def test_not_found_and_unreadable(self)
def test_iterations_parses_dates_and_missing_dates(self)
def test_board_columns_with_split(self)
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: ADO client with complete WIQL enumeration, and a fake ADO for tests"`

### Task 14: Pseudonymiser and revision normalisation

**Files:** Create `ledger/ado/pseudonym.py`, `tests/test_pseudonym.py`.

**Interfaces — Produces:**
- `load_or_create_key(home: Home) -> bytes` (32 random bytes in `secret.key`, `0600`; never regenerated if present).
- `class Pseudonymizer(key: bytes, roster: Roster, service_identities: Iterable[str])`: `actor(identity: Optional[Dict]) -> Optional[str]` → `p:<id>` when `roster.match_identity(identity["id"], [uniqueName, displayName])` matches; `s:<hex>` when uniqueName or displayName is in `service_identities` (case-insensitive); else `o:<hex>`; `hex` = first 32 hex chars of `HMAC-SHA256(key, identity["id"] or uniqueName.lower())`. Keeps a per-run dict and raises `LedgerError` on a collision between two different inputs.
- `normalise_text(html_text: Optional[str]) -> str`: strip tags, unescape entities, collapse whitespace.
- `to_revision(raw: Dict, pseud: Pseudonymizer, team: Team) -> Revision`: reads `System.Rev`, `System.ChangedDate`, `System.ChangedBy`, `System.State`, `System.Reason`, `System.IterationPath`, `System.AreaPath`, `System.AssignedTo`, `System.WorkItemType`, `System.TeamProject`, `System.Parent`, `System.Tags`, `System.Description`, `Microsoft.VSTS.Common.AcceptanceCriteria`; column from `team.column_field or "System.BoardColumn"`, done from `team.done_field or "System.BoardColumnDone"`. Hashes are SHA-256 of the normalised text. An identity given as a plain string `"Name <mail>"` is accepted too.
- `to_squad_revision(raw, team) -> Revision`: as above with `by`, `assignee` set to `None`, hashes empty, lengths 0.

- [ ] **Step 1: Write failing tests**

```python
def test_roster_person_by_id_even_after_rename(self)   # identity id matches, uniqueName changed -> "p:anna"
def test_other_is_stable_and_not_reversible_without_key(self)   # same input twice -> same "o:…" of 32 hex; different key -> different
def test_service_identity(self)
def test_no_real_identity_in_output(self)              # json.dumps(to_revision(...).to_json()) contains neither the display name nor the email of a non-roster person
def test_normalise_text(self):  self.assertEqual(normalise_text("<div>Given&nbsp;x<br/> then  y</div>"), "Given x then y")
def test_formatting_only_edit_keeps_hash(self)
def test_plain_string_identity(self)
def test_squad_revision_has_no_identity_or_text(self)
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: pseudonymisation and revision normalisation"`

### Task 15: Collector

**Files:** Create `ledger/ado/collect.py`, `tests/test_collect.py`; register `collect` in `ledger/cli.py`.

**Interfaces — Consumes:** `AdoClient`, `Pseudonymizer`, `Snapshot`, `Home`, `WorkCalendar`. **Produces:**
- `collect_team(home, cfg, roster, client: AdoClient, team: str, now: datetime, refresh: bool = False) -> Dict` and command `collect [--team T | --all] [--refresh] [--transport urllib|az-rest] [--auth az|pat]`.

Procedure, in order:
1. Sprint calendar: `client.iterations`; drop iterations without both dates, adding a warning naming each; build `Sprint` objects with `cal.sprint_bounds` and `period_start`; the target sprint is the latest whose `end <= now`. If none has ended → `LedgerError("no sprint of <team> has ended yet", hint=next end date)`. Window = the target and the `window_sprints - 1` before it.
2. A run file `collect-run.json` in the target sprint's `snapshot/` holds `cutoff` (set once to `now`), `account`, `auth_mode`, completed WIQL partitions and completed ticket ids. If it exists and `refresh` is false, resume with the same `cutoff`; if `client.connection()` differs from the recorded account → `LedgerError`.
3. Vendor discovery (ids de-duplicated), for each alias of each roster person ever assigned to the team, with `T` = `[System.WorkItemType] IN (<unit_types>)` (at `tasks` level, `unit_types` holds the task type, so no separate clause is needed):
   - unfinished: `T AND EVER [System.AssignedTo] = '<alias>' AND [System.State] NOT IN (<done and removed states of the team's phase rules>)`
   - recent: `T AND EVER [System.AssignedTo] = '<alias>' AND [System.ChangedDate] >= '<window start date>'`
   - plus ids in `inventory.json` for the team.
4. Squad discovery: `T AND [System.AreaPath] UNDER '<area>' AND ([System.State] NOT IN (<done/removed>) OR [System.ChangedDate] >= '<window start date>')` for each area path.
5. For each id: `client.revisions`; drop revisions with `changed > cutoff`; convert with `to_revision` / `to_squad_revision`; append the ticket to `tickets.jsonl` / `squad.jsonl` and only then add the id to `collect-run.json`. `NotFound` → `coverage.missing`; `Unreadable` → `coverage.unreadable`. Ticket text (title, description, acceptance, parent title via one extra revision fetch of the parent, cached) is taken from the last revision at or before the target sprint's `end`.
6. Write `sprints.json`, `meta.json`; update `inventory.json` with vendor ticket ids whose last phase is not done/removed; delete `collect-run.json`.
- Result summary: counts of vendor tickets, squad tickets, coverage gaps, warnings, elapsed time.

- [ ] **Step 1: Write failing tests** (FakeAdo over a builder portfolio; `now` injected)

```python
def test_collect_round_trip_equals_builder_snapshot(self)  # analyze on collected snapshot == analyze on builder.snapshot() for figures
def test_ticket_untouched_for_months_is_collected(self)    # In QA since 4 months before window, no changes since -> present in tickets
def test_refuses_unfinished_sprint(self)                   # Review Focus 5: now inside the first sprint -> LedgerError with next end date
def test_interrupted_then_resumed(self):                   # Review Focus 5
    fake.drop_after(n)   # dies while fetching page 2 of a ticket
    with self.assertRaises(LedgerError): collect_team(...)
    r = collect_team(..., now=later)                       # resume
    # the interrupted ticket has all its revisions; meta cutoff equals the first run's now; no ticket appears twice
def test_resume_with_different_account_refused(self)
def test_revisions_after_cutoff_dropped(self)
def test_sprint_without_dates_skipped_with_warning(self)   # Review Focus 3
def test_missing_and_unreadable_recorded_in_coverage(self)
def test_no_non_roster_identity_on_disk(self)              # grep every file under home for each non-roster displayName and uniqueName -> none
def test_inventory_updated(self)
def test_only_get_and_wiql_post_issued(self)               # every recorded request is GET, or POST to a URL ending /_apis/wit/wiql…
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: resumable collection with coverage rule, squad ledger and pseudonymisation"`

### Task 16: Probe and setup-check

**Files:** Create `ledger/ado/probe.py`, `tests/test_probe.py`; register `probe` and `setup-check`.

**Interfaces — Produces:**
- `run_probe(home, cfg_or_args, transports: Dict[str, Transport], env) -> Dict` and command `probe --org URL --project P --team T [--alias NAME] [--ticket ID …]` (works before `config.json` exists).
- Result: `{"checks": [{"id": "A1".."A14" | "ENV-*", "status": "observed"|"failed"|"not checked", "sample": int, "detail": str}], "timing": {...}}`, written in full to `probe/probe-report.json` and `.md`.
- Checks, one function each: `ENV-python` (>= 3.9), `ENV-timezone` (configured zone loads), `ENV-home` (Linux filesystem, effective `0600`), A1 token + expiry field, A2 both transports reach `connectionData`, A3 `EVER` query for the alias returns ids, A4 partitioned WIQL with `batch_limit=2` returns the same ids as unpartitioned, A5 revisions with `page=2` on the supplied tickets (or the three longest-history tickets found) are contiguous and carry state/iteration/assignee/board column, A6 identity objects carry `id`, A7 iterations have dates, A8 at least one ticket has more than one distinct board column value across revisions, A9 description and acceptance-criteria reference names present, A10 timing of N revision fetches plus rate-limit headers seen, A11 states/categories and board columns readable, A12 `System.Parent` present in revisions, A13 distinct `System.Reason` values on done-state revisions (listed locally), A14 same as `ENV-timezone`.
- `export(report: Dict) -> Dict`: strict allowlist. Output keys only: `tool_version`, `checks` (`id`, `status`, `sample`, and `detail` replaced by a value from a fixed enum of detail codes), `timing` (numbers), `shapes` (for each endpoint, the sorted list of standard field reference names starting `System.` or `Microsoft.VSTS.` plus the count of other fields), `categories` (list of ADO state categories with counts), `counts`. Customer-defined strings never pass through; anything not on the allowlist is dropped. Written to `probe/probe-export.json` and `.md`.
- `setup-check`: validates config and roster, that every phase rule's state exists in the probe's recorded states (when a probe report exists), and that every roster person has at least one identity id or alias; summary lists problems.

- [ ] **Step 1: Write failing tests**

```python
def test_probe_all_observed_against_fake(self)
def test_probe_reports_failure_not_exception(self)     # fake returns 403 for boards -> A11 "failed", others still run
def test_export_drops_planted_secrets(self):
    # plant "ACME-SECRET" in a state name, custom field name, team name, area path, error text, identity, URL
    self.assertNotIn("ACME-SECRET", json.dumps(export(report))); self.assertNotIn("ACME-SECRET", export_markdown(report))
def test_export_only_allowlisted_keys(self)
def test_setup_check_flags_unknown_state_and_identityless_person(self)
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: probe with allowlisted export, and setup-check"`

---

## Phase C: reports

### Task 17: Annotations

**Files:** Create `ledger/annotations.py`, `tests/test_annotations.py`; register `annotate`.

**Interfaces — Produces:**
- Keys: `carry:<ticket>:<sprint_id>` and `return:<ticket>:<rev>`.
- `REASONS = ("dependency", "waiting on customer", "requirement changed", "unplanned work took priority", "planned leave", "larger than expected", "other")`; `RETURN_REASONS = ("defect", "requirement changed", "other")`.
- `pending(metrics: Dict, entries: List[Dict]) -> List[Dict]`: one item per carried-over ticket (`carried_in_progress`, `carried_not_started`) and per return event in the sprint without a current entry; each `{"key","ticket","title","kind","sprints_carried"}`.
- `record(home, team, sprint_id, key, agreed=None, reason=None, note="", now=None) -> Dict`: appends a new version (`version` increments per key; earlier versions are kept) to `annotations.json`; validates key exists in metrics, `agreed in ("yes","no","unknown")`, reason in the list for its kind.
- `current(entries) -> Dict[str, Dict]`: latest version per key.
- `carried_forward(home, team, ticket) -> List[Dict]`: this ticket's entries from earlier sprints.
- Command: `annotate --team T --sprint ID --list` | `--set KEY [--agreed …] [--reason …] [--note …]` | `--interactive` (prompts on the terminal for each pending item; with `agent_text_access` irrelevant because no agent is involved).

- [ ] **Step 1: Write failing tests**

```python
def test_pending_lists_carryovers_and_returns_not_awaiting(self)
def test_record_versions_and_current(self)        # two records for one key -> current has the second, file holds both
def test_invalid_reason_or_key_rejected(self)
def test_annotations_do_not_change_metrics(self)  # metrics.json bytes identical before and after record()
def test_interactive_reads_answers(self)          # feed stdin "y\n1\nnote text\n"; entry stored
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: DM annotations for carryover and returns"`

### Task 18: Report assembly and fallback text

**Files:** Create `ledger/report.py`, `ledger/narrative.py` (fallback part), `tests/test_report.py`.

**Interfaces — Produces:**
- `build_report(home, cfg, roster, as_of: date) -> Dict`:

```
{"as_of":str,"vendor_label":str,"footnote":str,"summary":str,"risks":[…],
 "groups":[{"start":str,"finish":str,
            "totals":{"delivered","squad_delivered","awaiting","carried","carried_agreed","carried_not_agreed","carried_unanswered","available","nominal","time_split":{…}},
            "teams":[{"name","sprint":{…},"unchanged":bool,"backfilled":bool,"metrics":{…},"annotations":{key:entry},"narrative":{…},"status":{…}}]}]}
```
  Each team contributes its latest analysed sprint with `finish <= as_of`. Groups are keyed by identical `(start, finish)`, ordered by finish descending then team name. `unchanged` is true when that team sprint appears in the manifest of the latest issued edition. Totals are sums within a group only. Teams with no analysed sprint are listed in `report["missing_teams"]`.
- `FOOTNOTE` (exact): `"Figures count tickets and working days taken from the board's change history. Tickets are not sized, so counts are compared only with the same team's earlier sprints. Durations reflect when the board was updated, not hours worked. Splitting or cloning a ticket cannot be detected. Sprint dates are as the board shows them today."`
- `exec_projection(report: Dict) -> Dict`: deep copy with every `people` map, every ticket's `workers`, every key named `owner`, `by`, `holder`, and the `coverage` note removed, and people-derived context dropped from risks.
- `narrative.fallback(team_metrics: Dict) -> Dict`: `{"delivered":[{"heading": parent title or "Other", "text": "", "tickets":[ids]}], "awaiting":[…], "not_delivered":[…]}` grouped by `parent_title`, ordered by ticket count then heading.
- `narrative.fallback_summary(report: Dict) -> str`: one sentence per date group built from figures only, e.g. `"Sprint ending 6 Nov: 21 of 58 squad items delivered by the Vendor team across 6 teams; 6 finished and awaiting acceptance; 7 carried over (5 agreed in advance, 2 not)."` followed by `"Needs attention: <team> (<rules>)."` for each team not on track.
- A team status override is read from `reports/<date>/overrides.json` (`{team: {"label","reason"}}`), applied to `status.override`; command flag `report --override TEAM LABEL --reason TEXT` writes it.

- [ ] **Step 1: Write failing tests**

```python
def test_groups_by_identical_dates(self)          # 3 teams end 6 Nov under different sprint names, 1 team ends 30 Oct -> 2 groups; totals not summed across
def test_unchanged_marker(self)
def test_carried_split_by_annotation(self)        # 2 yes, 1 no, 1 none -> carried_agreed 2, carried_not_agreed 1, carried_unanswered 1
def test_exec_projection_has_no_person_fields(self):
    s = json.dumps(exec_projection(report))
    for p in roster.people: self.assertNotIn(p.display_name, s); self.assertNotIn('"' + p.id + '"', s)
    self.assertNotIn('"people"', s); self.assertNotIn('"workers"', s)
def test_fallback_groups_by_parent(self)
def test_fallback_summary_uses_counts_not_percent(self)
def test_missing_team_listed(self)
def test_override_recorded_with_reason(self)      # override without --reason -> LedgerError
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: report assembly by sprint dates, executive projection, fallback text"`

### Task 19: HTML and Markdown rendering

**Files:** Create `ledger/render/__init__.py`, `ledger/render/style.py`, `ledger/render/html.py`, `ledger/render/markdown.py`, `tests/test_render.py`; register `report`.

**Interfaces — Consumes:** `build_report`, `exec_projection`. **Produces:**
- `render_html(report: Dict, variant: str) -> str` with `variant in ("exec", "dm")`. The exec variant must be called with an `exec_projection` and raises `ValueError` if it finds a `people` key.
- `render_markdown(exec_report: Dict) -> str`.
- Command `report [--as-of YYYY-MM-DD] [--override …]` writes `reports/<date>/draft/executive.html`, `executive.md`, `delivery-manager.html`.
- Helpers: `ratio(n, m) -> str` ("n of m" when `m < 5`, else "n of m (p%)"); `days(x) -> str` ("–" for `None`); `clip(text, 90) -> str` with an ellipsis.

Layout follows the mockup: header (sprint dates per group, title `Delivery report: <vendor_label> contribution`); per date group a key-figures strip (delivered of squad, finished and awaiting acceptance, carried over with the agreed split, time with a vendor engineer versus waiting, available person-days) and a team table (team, own sprint name, engineers, trend bars, delivered, squad total, planned line, carried over, returns, median days with a vendor engineer, status with rules); risks and asks; a block per team (delivered / finished and awaiting acceptance / not delivered lists, the time-split bar with legend, the state-when-received line, the carried-over table with since, sprints, agreed-with-PO and note); footnote. The DM variant adds: the `Restricted: delivery manager` mark, the view switch (a class toggle hiding DM-only elements), an owner column, the people table per team (spec 13.2 columns, flags and context as pills, `No flags raised`), the coverage note and warnings, and for each flagged ticket an evidence card: holder timeline bar, dated change list, annotations. Trend bars are inline elements with percentage heights; no SVG paths, no script in the exec variant. Colours come from CSS custom properties with light and dark values; status is always text, with colour as a secondary cue.

- [ ] **Step 1: Write failing tests**

```python
def test_exec_html_has_no_external_requests(self):
    for needle in ("http://", "https://fonts", "<link", "src=", "@import"): self.assertNotIn(needle, exec_html)
def test_exec_html_has_no_script_and_no_person_data(self)   # no "<script"; no roster display name; no "p:" actor strings
def test_exec_renderer_refuses_dm_dataset(self)
def test_dm_html_has_mark_people_table_and_switch(self)     # "Restricted: delivery manager", each person's display name, "No flags raised"
def test_labels_exact_and_forbidden_words_absent(self)      # "Finished, awaiting acceptance" present; none of the forbidden words in either file
def test_hostile_text_is_escaped_and_clipped(self):         # Review Focus 4
    # ticket title '<script>alert(1)</script> | `x` 🙂 ' + 'y'*400
    self.assertNotIn("<script>alert(1)", dm_html); self.assertIn("&lt;script&gt;", dm_html); self.assertIn("…", dm_html)
    self.assertIn("\\|", md)
def test_empty_team_row_renders_dashes(self)                # Review Focus 2: medians shown as "–", no "None", no ZeroDivisionError
def test_small_denominator_is_count(self):  self.assertEqual(ratio(2, 3), "2 of 3"); self.assertEqual(ratio(21, 58), "21 of 58 (36%)")
def test_unchanged_and_backfilled_markers(self)
def test_markdown_has_tables_per_group(self)
def test_override_shown_with_reason_in_both(self)
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: executive and delivery manager reports in HTML and Markdown"`

### Task 20: Names check and issuing

**Files:** Create `ledger/names_check.py`, `ledger/issue.py`, `tests/test_issue.py`; register `names-check`, `issue`.

**Interfaces — Produces:**
- `names_check(texts: Dict[str, str], roster: Roster, approved: List[Dict]) -> List[Dict]`: scans each file's text, case-insensitively and on word boundaries, for every roster `display_name`, every alias, and each word of a display name that is at least 4 letters; returns hits `{"file","term","context"}` (40 characters either side) not covered by an approval `{"term","context"}`.
- Command `names-check [--as-of DATE] [--approve TERM --context TEXT]`: approvals stored in `reports/<date>/names-approved.json`.
- `issue(home, cfg, roster, as_of: date, note: str = "") -> int`: refuses (`LedgerError`) when `names_check` on the draft executive files has hits, when there is no draft, or when the draft is older than any input it was built from; copies the draft to `reports/<date>/editions/<NNN>/`, writes `manifest.json` (`edition`, `issued_at`, `note`, `tool_version`, `rules_version`, `team_sprints: [{team, sprint_id}]`, and `sha256` for every output file, each team sprint's snapshot files, metrics, ledger, annotations, narrative, plus `config.json`, `roster.json`, risks and overrides), marks the edition directory read-only (`0500`, files `0400`), and marks each included sprint's `metrics.json` as issued by writing `issued.json` beside it. Editions are never overwritten; the next call creates `NNN+1` and requires `note`.

- [ ] **Step 1: Write failing tests**

```python
def test_name_in_ticket_title_is_a_hit(self)          # title "Fix Bence's export" -> hit term "Bence"
def test_name_in_dm_note_is_a_hit(self)
def test_approved_occurrence_passes(self)
def test_short_name_parts_ignored(self)               # "Anna K." -> "Anna" scanned, "K" not
def test_issue_blocked_by_hit(self)
def test_issue_creates_immutable_edition_with_manifest(self)   # files 0400; manifest lists hashes that match
def test_second_issue_needs_note_and_keeps_first(self)
def test_stale_draft_refused(self)                    # re-run analyze after report -> issue raises, hint says run report again
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: executive names check and immutable editions"`

### Task 21: Golden portfolio and demo command

**Files:** Create `ledger/testing/portfolio.py`, `tests/test_golden.py`; register `demo`.

**Interfaces — Produces:**
- `build_demo(b: Optional[PortfolioBuilder] = None) -> PortfolioBuilder`: three teams and eight people with the mockup's invented names (Claims Intake: Anna K., Bence T., Csilla M.; Pricing Models: Dávid R., Emese V., Ferenc P.; Data Platform: Gábor S., Hanna L.). Claims Intake and Pricing Models share sprint dates (S32–S37, S37 = 26 Oct–6 Nov 2026) under different sprint names; Data Platform is offset by one week. Story lines to encode as ticket histories: rate table versioning ticket #40655 held by Dávid 4, 2 and 3 working days with QA waits of 9 and 12 and a review wait of 13, returned three times with acceptance criteria edited after each return, in progress at S37 end; a ticket finished 28 Oct awaiting review; Csilla on leave 4 days; Ferenc joining in S36; Emese with 3 days with nothing in progress; one ticket closed as duplicate; one ticket added mid-sprint by the PO; Gábor the only vendor contributor on three parent features; enough non-vendor squad tickets for squad totals well above vendor totals.
- Command `demo --home DIR`: refuses a non-empty home; writes the portfolio, runs analyze for every team, records sample annotations, builds the report with `as_of` 2026-11-07. Lets anyone see the outputs with no ADO access.

- [ ] **Step 1: Write failing tests** (compute expectations from the story lines, not from the mockup's printed numbers)

```python
def test_demo_end_to_end(self)             # demo into tmp home; three files exist; exec passes names_check with no hits
def test_two_date_groups(self)
def test_rate_table_ticket(self)           # sprints_carried 4 in S37; 3 returns over its life; flags for Dávid include "Carried over more than 2 sprints" and "Ticket returned repeatedly"; workers {"david": 9.0}
def test_pricing_status_at_risk(self)      # rules include "Chronic carryover" and "Returns"
def test_csilla_context_leave(self); def test_ferenc_context_joiner(self); def test_emese_context_nothing_in_progress(self)
def test_invariants_across_portfolio(self) # subset, one outcome per ticket, ledger completeness, bucket conservation for every team sprint
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "test: golden demo portfolio and demo command"`

---

## Phase D: risks, narrative, evidence, skills

### Task 22: Risk candidates and register

**Files:** Create `ledger/risks.py`, `tests/test_risks.py`; register `risks`; modify `ledger/report.py` to load accepted open risks into `report["risks"]`.

**Interfaces — Produces:**
- `detect(report: Dict, cfg: Config) -> List[Dict]`: candidates `{"id","kind","team","tickets":[ids],"figures":{…},"text":str,"ask":str,"people":[pids]}`; `id` = first 12 hex of SHA-256 of `kind|team|sorted tickets or feature`. Kinds and default rules (spec 14.4): `chronic_carryover` (`sprints_carried >= carry_flag_sprints`), `repeated_returns` (ticket returns `>= returns_flag`; text notes whether text changed between returns), `long_wait` (`days_waiting > long_wait_days` or `sprints_waiting >= long_wait_sprint_ends`), `unready_work` (`no_criteria >= unready_min` and `no_criteria / received >= unready_share`), `midsprint_additions` (`added_mid_sprint >= midsprint_min`), `one_vendor_contributor` (a parent with at least 2 tickets across the window where one roster person is the only one in `workers`), `fewer_delivered` (the team status rule), `nothing_in_progress` (a person with `days_nothing_in_progress >= nothing_in_progress_days`), `low_capacity` (`available / nominal < capacity_share`). `text` and `ask` use fixed templates containing counts and ticket ids, never a person's name.
- Register in `risks.json` at the data home root: `{"id","status": "open"|"closed","text","ask","opened":date,"closed":date|null,"evidence_ok":bool}`.
- Command `risks --as-of DATE --candidates` | `--accept ID [--text T] [--ask A]` | `--drop ID` | `--close ID`. Building a report re-runs `detect` and sets `evidence_ok` false on open risks whose id is no longer detected; the summary line of `report` names them.

- [ ] **Step 1: Write failing tests**

```python
def test_each_detector_fires_on_demo(self)      # chronic_carryover, repeated_returns, long_wait, unready_work, one_vendor_contributor present for expected teams
def test_candidate_text_has_no_names(self)
def test_ids_stable_across_runs(self)
def test_accept_edit_close_cycle(self)
def test_open_risk_persists_and_flags_stale_evidence(self)
def test_accepted_risks_appear_in_report(self)
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: rule-based risk candidates and risk register"`

### Task 23: Agent narrative input and validation

**Files:** Modify `ledger/narrative.py`; create `tests/test_narrative.py`; register `narrative-input`, `narrative-validate`; modify `ledger/report.py` to prefer a validated `narrative.json` over the fallback (never for backfilled sprints).

**Interfaces — Produces:**
- `agent_input(metrics: Dict, snap: Snapshot, access: str) -> Dict`: buckets `delivered`, `awaiting`, `not_delivered` (carried outcomes). Per ticket: `id`, `type`; with `titles` or `full` also `title`, `parent_title`; with `full` also `description` truncated to 1500 characters. With `none`: ids and types only. No person fields at any level.
- `CLAIM_TERMS = ("live", "in production", "deployed", "released", "rolled out", "accepted", "signed off", "as planned", "because", "due to", "caused by")`.
- `validate(narrative: Dict, metrics: Dict, roster: Roster, confirmed: List[str]) -> List[str]` problems: cited id not in that bucket; a bucket ticket covered by no entry; heading over 60 characters or text over 280; a roster name or alias in any text; a sentence containing a claim term (word-boundary, case-insensitive) that is not in `confirmed` verbatim; summary over 600 characters.
- `narrative.json` shape: `{"teams": {team: {"delivered":[{"heading","text","tickets":[ids]}], "awaiting":[…], "not_delivered":[…]}}, "summary": str, "confirmed": [sentences]}`.
- Commands: `narrative-input --team T --sprint ID` (prints JSON at the configured access level; refuses with a hint when access is `none` and `--force-ids` is not given); `narrative-validate --team T --sprint ID --file PATH` (on success copies to the sprint dir; on failure exit 2 listing problems).

- [ ] **Step 1: Write failing tests**

```python
def test_access_levels(self)                  # none: no "title" key anywhere; titles: no "description"; full: description <= 1500 chars
def test_input_has_no_person_fields(self)
def test_uncited_and_wrong_bucket_rejected(self)
def test_roster_name_rejected(self)
def test_claim_needs_confirmation(self)       # "Rule set is live for motor claims." rejected; accepted once the sentence is in confirmed
def test_valid_narrative_used_by_report_but_not_for_backfilled(self)
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: agent narrative input by access level and validation"`

### Task 24: Evidence briefs

**Files:** Create `ledger/evidence.py`, `tests/test_evidence.py`; register `evidence`; reuse card and table helpers from `ledger/render/html.py`.

**Interfaces — Produces:**
- `build_person_brief(home, cfg, roster, pid: str, sprints: int = 6) -> Dict` and `build_team_brief(home, cfg, roster, team: str, sprints: int = 6, executive: bool = False) -> Dict`.
- Person brief: assignments with dates; one row per team sprint the person was on (following them across teams) with the spec 13.2 figures; flags by sprint; every delivered and handed-off ticket with a link `{org_url}/{project}/_workitems/edit/{id}`; for each flagged ticket the holder timeline, dated change list and annotations; context lines. Header mark `Restricted: delivery manager`.
- Team brief: per-sprint table of spec 13.1 figures, status and rules by sprint, flagged tickets. With `executive=True` it is built from `exec_projection` data and must pass `names_check`.
- Command `evidence (--person ID | --team NAME) [--sprints N] [--executive]` writes `evidence/<date>-<slug>.html` and prints the path.

- [ ] **Step 1: Write failing tests**

```python
def test_person_brief_follows_person_across_teams(self)     # moved Claims -> Pricing: rows from both teams in date order
def test_person_brief_lists_ticket_links_and_timeline(self) # contains "_workitems/edit/40655" and "9.0"
def test_person_brief_marked_restricted(self)
def test_team_brief_executive_has_no_names(self)
def test_unknown_person_or_team(self)                       # LedgerError listing valid ids
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: person and team evidence briefs"`

### Task 25: Skills, install script, AGENTS.md, README

**Files:** Create `skills/delivery-setup/SKILL.md`, `skills/delivery-sprint-close/SKILL.md`, `skills/delivery-evidence/SKILL.md`, `scripts/install-skills.sh`, `AGENTS.md`, `tests/test_skills.py`; rewrite `README.md`.

**Content decisions:**
- Each `SKILL.md` has frontmatter `name` (equal to its directory name) and `description` (starts "Use when", under 300 characters), then numbered steps that call `bin/ledger … --json` by absolute path placeholder `<LEDGER>` resolved in step 1 (`LEDGER="$(cd "$(dirname "<this skill dir>")/.." && pwd)/bin/ledger"`, with the install script writing the resolved repo path into an installed copy). Procedures as in spec section 17. Rules stated in each skill: never open files under the data home directly; never compute or restate a figure the CLI did not print; never send the report; at `agent_text_access none` refer to tickets by id only and tell the DM to use `annotate --interactive`.
- `delivery-sprint-close` asks annotation questions one ticket at a time: "Was carrying #ID over agreed with the PO and communicated in advance? (yes / no / unknown)", then the reason list, then an optional note, then calls `annotate --set`.
- `scripts/install-skills.sh [--project DIR | --user] [--agents claude,copilot]`: copies each skill directory to `.claude/skills/` and `.github/skills/` under the project (or `~/.claude/skills/` and `~/.copilot/skills/` with `--user`), substituting the repo path; prints what it wrote; never overwrites a differing file without `--force`.
- `AGENTS.md`: the three procedures in prose plus the command list, for agents with no skill support.
- `README.md`: what it is; requirements; quick start with `bin/ledger demo --home /tmp/ledger-demo`; first run on a real board (probe → setup → trace check → collect → analyze → annotate → report → issue); the data home and how to delete it; what the executive report does and does not guarantee (spec 15.2); what an agent may read (spec 14.2); the notice that per-person reporting may be subject to local law and works-council rules and the operator must check; known limits (the footnote). No licence file: the README says the licence is to be decided before publishing.
- Before writing the install paths, verify the current skill discovery locations for Claude Code, GitHub Copilot in VS Code and Copilot CLI from their official documentation, and record the checked locations and date in `docs/team-performance-report/skill-locations.md`. If a location differs from the above, follow the documentation.

- [ ] **Step 1: Write failing tests**

```python
def test_every_skill_has_valid_frontmatter(self)      # name == directory name; description starts "Use when" and < 300 chars
def test_skills_only_use_existing_commands(self)      # every "ledger <cmd>" in skills and AGENTS.md is a registered subcommand
def test_skills_contain_no_metric_logic(self)         # none of "threshold", ">= 3", "percent" formulas; no forbidden words
def test_install_script_writes_both_locations(self)   # run with --project tmp: files exist under .claude/skills and .github/skills with the repo path substituted
def test_repo_has_no_real_names(self)                 # grep the tracked files for a deny-list read from an untracked local file if present; skip when absent
```

- [ ] **Steps 2–4.** **Step 5:** `git commit -m "feat: agent skills, installer, AGENTS.md and README"`

### Task 26: Whole-pipeline check

**Files:** Create `tests/test_pipeline.py`.

- [ ] **Step 1:** Write one test that drives the CLI only, against `FakeAdo` over `build_demo()` (inject the transport through an environment hook `LEDGER_TEST_TRANSPORT` honoured only when `ledger.testing` is importable and the variable is set): `collect --all` → `analyze` per team → `annotate --set` for two keys → `risks --candidates`, `--accept` one → `report` → `names-check` → `issue` → `evidence --person david` → `status`. Assert exit code 0 at each step, the edition manifest lists every team sprint, and a second `collect --all` with no board changes yields byte-identical `metrics.json`.
- [ ] **Step 2:** Run it; fix whatever integration gaps it exposes in the owning module, adding a unit test there for each.
- [ ] **Step 3:** Run the whole suite: `python3 -m unittest discover -s tests -v`. Expected: all pass. Then run `bin/ledger demo --home /tmp/ledger-demo-check` and open both HTML files once to confirm they render.
- [ ] **Step 4:** `git commit -m "test: whole-pipeline check through the CLI"`

---

## Deviations from the spec, decided here

- The fallback transport is `az rest` rather than `az devops invoke`: it takes a full URL and the same resource id, so one client code path serves both transports.
- Two extra outcomes, `active_outside_sprint` and `assigned_outside_sprint`, give every vendor ticket exactly one outcome (spec 12.5 and 12.6 imply them).
- A `period_start` on each sprint assigns closures made between two sprints to the later one.
- The squad ledger uses per-ticket revisions, the same path as vendor tickets. The reporting revisions feed is only timed by the probe; switching to it is a later optimisation.
- Commands added beyond spec 9.4: `demo`, `narrative-input`, `names-check` (the spec's name check).
- Default thresholds the spec left open: `midsprint_min 3`, `capacity_share 0.7`, `bulk_edit_count 10` within `60` seconds, `no_criteria_desc_len 40`.
- Source-control corroboration (spec phase 4) is out of this plan.
