# Configurable manager metrics: design

Status: implemented (2026-11-27).

## 1. Intent

Today `manager_.html` shows the same fixed set of five Delivery stats tiles, the *Where the time goes* bar, and the same five rows in the 6-sprint trend table for every team. In practice, squads differ widely:
- one team may not track `test_states`, making `first_pass` noisy or permanently "not tracked";
- a support or Kanban-like squad may not care about `planned` vs delivered;
- some teams collect `po_satisfaction` while others do not;
- a team with sparse board state transitions may want to hide *Where the time goes*.

Configurable manager metrics let the DM choose — globally and per team in `config.json` — which metrics appear on `manager_` and in what order, while keeping defaults 100% backwards-compatible.

## 2. Goals, non-goals, anti-goals

- **Goals:**
  - Global default `manager_metrics` and `show_flow` in `config.json`, overridable per team under `teams.<TEAM>`.
  - Ordering in `metrics` controls both the Delivery stats tile order and the 6-sprint trend table row order.
  - Dynamic manager footnote: defines only the metrics configured for at least one team in the report, in a fixed canonical order.
  - Suppress irrelevant metric warnings (such as `no test states`) when no reported team displays a metric that depends on them.
  - Zero-config backwards compatibility: existing `config.json` files produce byte-for-byte identical HTML (with fixed timestamp).
- **Non-goals:**
  - Custom user-defined formulas or calculated expressions in `config.json`.
  - Skipping board calculations in `metrics.py` (in-memory replay is already fast, and `dm_` still requires `planned`, `delivered`, `carry`, `cycle_window`, `p85`, and `waiting_share` for signals and status).
  - Changing `dm_.html` layout, signals, or RAG status rules.
  - Adding interactive metric prompts to `ledger init` (setup stays minimal per `docs/design-v3.md`).
- **Anti-goals:**
  - Silently ignoring typos in metric keys or `null` config values (invalid config must fail fast at load time).
  - Showing a hidden metric in the 6-sprint trend table or explaining a disabled metric in the footnote.
  - Comparing teams against each other because they display different subsets of metrics.

## 3. Metric catalog

Fixed, documented metric keys (also defines the canonical footnote order):

| Key | Manager stat tile | Trend table row | Data key adapter |
|---|---|---|---|
| `planned` | Planned work items delivered (`X of Y`) | `Planned work items delivered` | `delivered_planned` / `planned` (highlighted with `.key`) |
| `delivered` | Work items delivered in total | `Work items delivered in total` | `delivered` |
| `throughput` | Work items per 10 person-days (last 3 sprints) | `Work items per 10 PD` | `throughput["per10"]` / `trend[i]["per10"]` |
| `build_time` | Build time, median (last 3 sprints) | `Build time median (days)` | `build["median"]` / `trend[i]["build"]` |
| `first_pass` | First-time pass (last 3 sprints) | `First-time pass` | `first_pass` / `trend[i]["first_pass"]` |
| `po_satisfaction` | PO satisfaction (`X / 5` + band chip, or `–`) | `PO satisfaction` | `po_satisfaction` / `trend[i]["po_satisfaction"]` |

- **Default `manager_metrics`:** `["planned", "delivered", "throughput", "build_time", "first_pass"]`.
- **`show_flow` (boolean, default `true`):** controls whether *Where the time goes (last 6 sprints)* is rendered on `manager_`. Kept separate from `metrics` because it is a full-width breakdown section rather than a stat tile + trend row. **(builder's call)**

## 4. Configuration (`config.json`)

```json
{
  "manager_metrics": ["planned", "delivered", "throughput", "build_time", "first_pass"],
  "show_flow": true,
  "teams": {
    "ALFA": {
      "count": "stories"
    },
    "BETA": {
      "count": "tasks",
      "metrics": ["planned", "delivered", "build_time", "po_satisfaction"],
      "show_flow": false
    }
  }
}
```

### Resolution rules
- **Team metrics:** `teams.<TEAM>.metrics` if present in the team entry, otherwise top-level `manager_metrics`, otherwise `DEFAULT_MANAGER_METRICS`. Team lookup is case-insensitive (matching `count_of()`).
- **Team `show_flow`:** `teams.<TEAM>.show_flow` if present in the team entry, otherwise top-level `show_flow`, otherwise `True`.

### Validation in `load_config()` and `run_init()` lifecycle
- `teams` must be a `dict`; every team entry must be a `dict` (e.g. `{"teams": {"ALFA": null}}` raises `ValueError`).
- Case-colliding keys in `teams` (e.g. both `"alfa"` and `"ALFA"`) raise `ValueError` naming the collision.
- `run_init()` in `ledger/setup.py` must reuse an existing case-insensitive matching key in `cfg.teams` rather than creating a duplicate with `setdefault(team, {})`, preserving custom `metrics` and `show_flow` across re-initializations.
- `manager_metrics` (when key is present) and `teams.<TEAM>.metrics` (when key is present):
  - Must be a `list` (never `null` or string); every element must be a `str` (checked before duplicate/set checks so malformed items raise `ValueError`, never `TypeError`).
  - Every entry must be in `KNOWN_METRICS`; unknown keys raise `ValueError` naming the key (and team if per-team).
  - Duplicate keys raise `ValueError` naming the duplicate.
  - An empty list `[]` is allowed (renders no stat tiles and no trend table; `Scope:` still renders). **(builder's call)**
- `show_flow` (global or per-team, when key is present): must be a JSON boolean (`isinstance(v, bool)`, rejecting `null`, ints, and strings), else `ValueError`. To inherit the default, omit the key.
- Unknown keys inside `teams.<TEAM>` (anything other than `count`, `metrics`, `show_flow`): emit a `warn(...)` naming the team and keys, matching top-level unknown-key behaviour.

## 5. What the reader sees (`manager_` only)

### Delivery stats & Scope
- `.figs` renders one tile per key in `t["manager_metrics"]`, in the configured order.
  - `planned` keeps the `.key` accent styling whenever it is included.
  - `po_satisfaction` always renders a tile when included — showing `X / 5` + band chip + arrow when rated, or `–` (`PO satisfaction`) when unrated.
  - If `t["manager_metrics"]` is empty, the `.figs` container is omitted and only the `Scope:` line renders under `Delivery stats`.
- **PO feedback line:** rendered below `Scope:` only when `"po_satisfaction"` is in `t["manager_metrics"]` **and** the current sprint has a non-empty PO comment.

### Where the time goes (`_flow`)
- Rendered only when `t["show_flow"]` is `True` and `t["flow"]` is not `None`.

### 6-sprint trend table (`_trend_table`)
- Renders rows in the exact order of `t["manager_metrics"]`.
- If `t["manager_metrics"]` is empty, the folded trend `<details>` panel is omitted.

### Footnote (`_footnote(False)`)
Built deterministically in fixed canonical order from the **configured** metrics and `show_flow` across teams in the report (for missing-sprint teams, their resolved config still applies, or default config when no teams are present):
- **Always present:** base method paragraph (team counting level, PD, planned/added/close-iteration/late), On Hold sentence, and recomputation sentence.
- **Conditional paragraphs/sentences (canonical order):**
  1. `throughput` and/or `build_time` — when both are enabled, preserve today's single combined paragraph verbatim; when only one is enabled, emit only that sentence.
  2. `first_pass` — included only if any team enables `first_pass`.
  3. Delivery-metric arrows paragraph — included only if any team enables at least one of `throughput`, `build_time`, `first_pass`. Wording adapts to the enabled subset so disabled metrics are not mentioned (when all 3 are enabled, keeps today's exact phrasing: *"green means better (higher throughput or first-time pass, lower build time), red worse"*).
  4. `Where the time goes` paragraph — included if any team has `show_flow` enabled (preserving byte-for-byte compatibility with today's reports even when all teams have `flow=None`).
  5. `PO satisfaction` sentence (and qualified *"Delivery-metric arrows..."* / *"Board figures..."* wording) — included only if any team enables `po_satisfaction`.
- With default config (5 default metrics, `show_flow=True`, no `po_satisfaction`), `_footnote(False)` is **byte-for-byte identical** to today — including on all-missing or no-flow reports.

### `dm_` card
No change. `dm_` always computes and displays its full signals, status, and ours-vs-rest comparison.

## 6. Warning suppression

- In `run_report()`, the warning `"no test states in config.json; first-time pass counts reopens only"` is printed only when `not cfg.test_states` **and** at least one reported team includes `"first_pass"` in its resolved `manager_metrics`.

## 7. Data shape

- `Config` in `ledger/config.py`:
  - `manager_metrics: List[str] = field(default_factory=lambda: list(DEFAULT_MANAGER_METRICS))`
  - `show_flow: bool = True`
  - `teams: Dict[str, Dict[str, Any]] = field(default_factory=dict)`
- Helpers in `ledger/metrics.py` (mirroring `count_of`):
  - `metrics_of(cfg: Config, team: str) -> List[str]`
  - `show_flow_of(cfg: Config, team: str) -> bool`
- `team_sprint()` (and missing-sprint result dicts in `run_report()`) attach resolved display config:
  - `"manager_metrics": metrics_of(cfg, team)`
  - `"show_flow": show_flow_of(cfg, team)`
  So `render_manager()` remains a pure renderer taking only `teams` (no `cfg` parameter needed).

## 8. Interaction with `notes.md`, implementation order, and docs

- **Implementation order (shipped together):**
  1. Config validation/resolution + `run_init()` case-insensitive reuse (`config.py`, `setup.py`, `metrics.py`).
  2. `## PO satisfaction` parsing, reused-folder clearing, and historical discovery (`config.py`, `metrics.py`).
  3. Configurable manager rendering, PO tile/feedback/trend, and dynamic footnote (`render.py`).
  4. Docs (`docs/design-v3.md`, `AGENTS.md`, `skills/delivery-report/SKILL.md`) and size cap (`tests/test_size.py`).
- `notes.md` always keeps `## PO satisfaction` for every team even if disabled in `config.json`, so toggling the metric off and back on never loses notes.
- Keep the implementation compact: small mapping/helper functions, no metric-class hierarchy.

## 9. Edge cases

- **Existing `config.json` without `manager_metrics` or `show_flow`:** Loads cleanly with defaults; output is byte-for-byte identical to today (with fixed timestamp).
- **Team in `team.csv` not listed in `config.json`:** Inherits global `manager_metrics` and `show_flow` (same fallback pattern as `count`).
- **Case mismatch between `team.csv` (`ALFA`) and `config.json` (`alfa`):** Handled by case-insensitive lookup; `run_init()` reuses `"alfa"` instead of duplicating `"ALFA"`; duplicate case-colliding keys in `config.json` fail fast with `ValueError`.
- **Null or malformed values (`show_flow: null`, `metrics: null`, `metrics: [123]`, `teams: {"ALFA": null}`):** All raise `ValueError` (never `TypeError` or silent `None` propagation).
- **Explicit `metrics: []` vs omitted `metrics`:** Explicit `[]` overrides global `manager_metrics` (renders no tiles/trend); omitted inherits global `manager_metrics`. Same for explicit `show_flow: false` vs omitted.
- **Unknown key inside `teams.<TEAM>`:** Emits `warning:` and is ignored.
- **One team has `po_satisfaction` enabled and unrated; another has it disabled:** First team shows a `–` tile and `PO satisfaction` trend row; second team shows neither (and hides any PO feedback comment in `notes.md`).

## 10. Tests

All tests use invented histories and configs only:
- `load_config()` validation:
  - defaults when omitted;
  - valid global and per-team `metrics` and `show_flow`;
  - inheritance vs explicit `metrics: []` and explicit `show_flow: false`;
  - invalid metric key, duplicate key, non-list `metrics`, non-string element (e.g. `[{"x": 1}]`), `null` values, non-bool `show_flow`, non-dict `teams` or team entry, and case-colliding team keys all raise `ValueError`;
  - unknown per-team key warns.
- `run_init()` round-trip: load → `init` → save → reload with lowercase team key, custom `metrics`, and `show_flow: false` preserves custom config without creating a duplicate uppercase team key.
- Manager rendering:
  - custom subset and custom ordering reflected in both `.figs` tiles and `_trend_table` rows;
  - `show_flow: false` hides *Where the time goes* for that team only; hides its footnote paragraph when disabled for all teams;
  - `metrics: []` omits `.figs` and trend panel without crashing;
  - `po_satisfaction` enabled + blank renders `–` tile and trend row; disabled omits both (and omits PO feedback line even if `notes.md` has a comment);
  - footnote emits canonical order, adapts arrow wording to enabled subset, and produces byte-for-byte identical HTML with default config (including no-flow and all-missing cases, with fixed timestamp).
- Warning suppression: `no test states` warning suppressed when no reported team enables `first_pass`, printed when at least one does.
- `dm_.html` is byte-for-byte unchanged regardless of `manager_metrics` / `show_flow`.
