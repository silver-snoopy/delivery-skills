"""The manager_, dm_ and person_ HTML pages, and the `report` and `person` commands."""

import os
import re
from datetime import date, datetime
from html import escape, unescape
from urllib.parse import quote

from ledger.ado import Client, cached_ids, fetch, load_cache, save_cache
from ledger.config import (
    DEFAULT_MANAGER_METRICS,
    SEVERITY,
    Notes,
    Person,
    carry_reasons,
    email_index,
    load_config,
    load_report_notes,
    metrics_of,
    read_team,
    reconcile_notes,
    show_flow_of,
    write_report_notes,
)
from ledger.metrics import (
    CARRIED_FLAG,
    GREEN_SHARE,
    NOT_STARTED_DAY,
    STALE_DAYS,
    TREND,
    WAIT_FLAG_DAYS,
    WAIT_RED_DAYS,
    WIP_FLAG,
    team_actions,
    team_sprint,
    team_sprints,
)
from ledger.replay import local_tz, parse_ts
from ledger.xlsx import read_vacations, resolve_name

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
PO_BANDS = {
    5: ("Very satisfied", "green"),
    4: ("Satisfied", "green"),
    3: ("Mixed", "amber"),
    2: ("Dissatisfied", "red"),
    1: ("Very dissatisfied", "red"),
}
CSS = """:root{--bg:#F6F7F9;--paper:#FFF;--fg:#16202A;--muted:#5A6573;--line:#DCE1E7;--accent:#1E5B86;
--accent-soft:#E6EEF5;--good:#2C7A4B;--good-soft:#E5F2EA;--warn:#9A6514;--warn-soft:#FBF0DC;
--bad:#A8352D;--bad-soft:#F8E4E2}
@media (prefers-color-scheme: dark){:root{--bg:#11161C;--paper:#171E26;--fg:#E4E9EF;--muted:#9AA6B4;
--line:#2A3440;--accent:#7FB3DA;--accent-soft:#1D2B38;--good:#7CC79A;--good-soft:#18291F;
--warn:#E3B260;--warn-soft:#2E2414;--bad:#EE8E86;--bad-soft:#331C1A;color-scheme:dark}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",
Roboto,sans-serif;margin:0;padding:24px 16px 56px}
.wrap{max-width:980px;margin:0 auto;display:flex;flex-direction:column;gap:20px}
h1,h2,h3,p{margin:0}h1{font-size:26px}h2{font-size:20px}
h3{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-bottom:4px}
.meta,.cap,.note,.ask{color:var(--muted);font-size:13px}.cap{text-align:right}a{color:inherit}
.squad{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:22px;
display:flex;flex-direction:column;gap:18px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
ul{margin:0;padding-left:18px}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;
background:var(--line);border:1px solid var(--line);border-radius:6px;overflow:hidden}
.fig{background:var(--paper);padding:12px 14px;display:flex;flex-direction:column}
.n{font-size:24px;font-weight:600}.n small{font-size:14px;color:var(--muted)}
.fig .l{font-size:12.5px;color:var(--muted)}.key .n{color:var(--accent)}
.tbl{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13.5px;
font-variant-numeric:tabular-nums}th{text-align:left;font-size:12px;color:var(--muted);
text-transform:uppercase;padding:6px 10px;border-bottom:1px solid var(--line)}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.id{font-family:ui-monospace,Menlo,Consolas,monospace;color:var(--accent);white-space:nowrap}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.GREEN,.green{background:var(--good-soft);color:var(--good)}
.AMBER,.amber{background:var(--warn-soft);color:var(--warn)}
.RED,.red{background:var(--bad-soft);color:var(--bad)}.wait,.track{background:var(--accent-soft);color:var(--accent)}
.grey{background:var(--bg);color:var(--muted)}
.foot{font-size:12.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px;
display:flex;flex-direction:column;gap:6px}
.private{background:var(--bad-soft);color:var(--bad);font-size:12px;padding:6px 10px;
border-radius:6px;align-self:flex-start}
details{border:1px solid var(--line);border-radius:6px;padding:8px 12px}summary{cursor:pointer;font-weight:600}
.carry-empty{background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:12px 14px}"""


MANAGER_CSS = (
    ".d{font-size:12px;font-weight:600;margin-top:2px}.d.up{color:var(--good)}.d.down{color:var(--bad)}"
)


def _tag(name: str, inner: str, cls: str = "") -> str:
    cls_attr = f' class="{cls}"' if cls else ""
    return f"<{name}{cls_attr}>{inner}</{name}>"


def _num(x) -> str:
    return "–" if x is None else f"{round(x, 1):g}"


def _days(x) -> str:
    return "–" if x is None else f"{int(x)} d"


def _pct(x, cap=None) -> str:
    if x is None:
        return "–"
    n = round(x * 100)
    return f"{min(cap, n) if cap is not None else n}%"


def _items(n: int) -> str:
    return f"{n} work item{'' if n == 1 else 's'}"


def _last(n: int) -> str:
    return "last sprint" if n == 1 else f"last {n} sprints"


def _ids(ids) -> str:
    return _tag("span", " ".join(f"#{i}" for i in ids), "id") if ids else "–"


def _table(head: list[str], rows: list[list[str]]) -> str:
    """`head` is plain text; cells are HTML already."""
    th = "".join(_tag("th", escape(h)) for h in head)
    body = "".join(_tag("tr", "".join(_tag("td", c) for c in r)) for r in rows)
    return _tag("div", _tag("table", _tag("thead", _tag("tr", th)) + _tag("tbody", body)), "tbl")


def _day(d: date, year: bool = True) -> str:
    yr = f" {d.year}" if year else ""
    return f"{d.day} {MONTHS[d.month - 1]}{yr}"


def _dates(sprint: dict) -> str:
    a, b = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["end"])
    return f"{_day(a, a.year != b.year)} – {_day(b)}"


def _page(title: str, body: str, light: bool = False, extra_css: str = "") -> str:
    """`light` drops the dark theme, for pages sent to readers whatever their system setting."""
    css = re.sub(r"@media \(prefers-color-scheme: dark\)\{.*?\}\}\n", "", CSS, flags=re.S) if light else CSS
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n<style>\n{css + extra_css}\n</style></head>\n"
        f'<body><div class="wrap">\n{body}\n</div></body></html>\n'
    )


def _head(title: str, teams: list[dict], generated: datetime, private: bool, board: str) -> str:
    dated = next((t["sprint"] for t in teams if not t.get("missing")), None)
    meta = [_dates(dated)] if dated else []
    meta.append(f"{len(teams)} team{'' if len(teams) == 1 else 's'}")
    if board:
        meta.append(f"Board data as of {board}")
    return "\n".join(
        (
            [_tag("p", "Internal: delivery manager and team leads only. Not for the customer.", "private")]
            if private
            else []
        )
        + [
            _tag("h1", escape(title)),
            _tag("p", escape(" · ".join(meta)), "meta"),
            _tag("p", f"generated {generated.strftime('%Y-%m-%d %H:%M %Z').strip()}", "meta"),
        ]
    )


def _missing(t: dict) -> str:
    return _tag(
        "article",
        _tag("div", _tag("h2", escape(t["team"])), "top") + _tag("p", escape(t["missing"]), "note"),
        "squad",
    )


def _capacity(t: dict) -> str:
    c = t["capacity"]
    eng = f"{c['engineers']} engineer{'' if c['engineers'] == 1 else 's'}"
    if c["onboarding"]:
        eng += f", {c['onboarding']} onboarding"
    lines = [f"{t['sprint']['name']} ({_dates(t['sprint'])})", f"{eng} ({_num(c['available'])} PD capacity)"]
    return _tag("span", "<br>".join(escape(line) for line in lines), "cap")


def _fig(n: str, label: str, key: bool = False, arrow: str = "", extra: str = "") -> str:
    return _tag(
        "div",
        _tag("span", n, "n") + _tag("span", escape(label), "l") + extra + arrow,
        "fig key" if key else "fig",
    )


def _arrow(now_text: str, before_text: str | None, better: bool, sprints: list[str]) -> str:
    if before_text is None or before_text == now_text or not sprints:
        return ""
    try:
        now, before = [
            float(re.sub(r"<[^>]+>", "", v).split()[0].rstrip("%")) for v in (now_text, before_text)
        ]
    except ValueError:
        return ""
    if now == before:
        return ""
    names = sprints[0] if len(sprints) == 1 else sprints[0] + "–" + sprints[-1]
    prefix = "" if sprints[0].lower().startswith("sprint") else "sprints "
    tri = "▲" if now > before else "▼"
    return _tag(
        "span", f"{tri} from {escape(before_text)} vs {prefix}{escape(names)}", "d up" if better else "d down"
    )


def _decimal(value) -> str:
    return "–" if value is None else f"{value:.1f}"


def _day_count(value: str) -> str:
    n = float(value)
    return (str(int(n)) if n in (0, 1) else value) + (" day" if n == 1 else " days")


def _first_pass(t: dict) -> str:
    f = t["first_pass"]
    if not f["scored"]:
        return _fig("–", "first-time pass")
    if not f["tracked"]:
        return _fig(
            "not tracked",
            f"first-time pass: too few items went through a test state in the last {TREND} sprints",
        )
    now = _pct(f["share"], 99 if f["returned"] else 100)
    before = f.get("before")
    previous = None if before is None else _pct(before, 99 if before < 1 else 100)
    return _fig(
        now,
        f"first-time pass (last 3 sprints, {len(f['returned'])} of {f['scored']} returned)",
        arrow=_arrow(now, previous, before is not None and f["share"] > before, t["windows"]["before"]),
    )


def _po_satisfaction(t: dict) -> str:
    po = t.get("po_satisfaction")
    if po is None:
        return _fig("–", "PO satisfaction")
    label, colour = PO_BANDS[po["score"]]
    chip = f'<div style="margin-top:4px"><span class="chip {colour}">{escape(label)}</span></div>'
    arrow = (
        ""
        if po["before"] is None or not po.get("before_sprint")
        else _arrow(str(po["score"]), str(po["before"]), po["score"] > po["before"], [po["before_sprint"]])
    )
    return _fig(f"{po['score']} <small>/ 5</small>", "PO satisfaction", arrow=arrow, extra=chip)


def _window_fig(t: dict, key: str, field: str, label: str, unit: str = "") -> str:
    value, before = t[key][field], t[key]["before"]
    now = _day_count(_decimal(value)) if unit and value is not None else _decimal(value)
    previous = None if before is None else (_day_count(_decimal(before)) if unit else _decimal(before))
    better = (
        value is not None and before is not None and (value < before if key == "build" else value > before)
    )
    return _fig(
        re.sub(r" (days?)$", r" <small>\1</small>", now),
        label,
        arrow=_arrow(now, previous, better, t["windows"]["before"]),
    )


_METRIC_RENDERERS = {
    "planned": (
        lambda t: _fig(
            f"{len(t['delivered_planned'])} <small>of {len(t['planned'])}</small>",
            "planned work items delivered",
            True,
        ),
        lambda t, tr: (
            ["Planned work items delivered"] + [f"{s['delivered_planned']}/{s['planned']}" for s in tr]
        ),
    ),
    "delivered": (
        lambda t: _fig(str(len(t["delivered"])), "work items delivered in total"),
        lambda t, tr: ["Work items delivered in total"] + [str(s["delivered"]) for s in tr],
    ),
    "throughput": (
        lambda t: _window_fig(t, "throughput", "per10", "work items per 10 person-days (last 3 sprints)"),
        lambda t, tr: ["Work items per 10 PD"] + [_decimal(s["per10"]) for s in tr],
    ),
    "build_time": (
        lambda t: _window_fig(t, "build", "median", "build time, median (last 3 sprints)", " days"),
        lambda t, tr: ["Build time median (days)"] + [_decimal(s["build"]) for s in tr],
    ),
    "first_pass": (
        _first_pass,
        lambda t, tr: (
            ["First-time pass"]
            + [
                "–"
                if s["first_pass"] is None
                else _pct(s["first_pass"], 99 if s["first_pass"] < 1.0 else 100)
                if t["first_pass"]["tracked"]
                else "not tracked"
                for s in tr
            ]
        ),
    ),
    "po_satisfaction": (
        _po_satisfaction,
        lambda t, tr: (
            ["PO satisfaction"]
            + [f"{s['po_satisfaction']}/5" if s.get("po_satisfaction") is not None else "–" for s in tr]
        ),
    ),
}


def _figs(t: dict, metrics=None) -> str:
    metrics = t.get("manager_metrics", DEFAULT_MANAGER_METRICS) if metrics is None else metrics
    return _tag("div", "".join(_METRIC_RENDERERS[m][0](t) for m in metrics), "figs") if metrics else ""


def _scope(t: dict) -> str:
    changes = (
        f"+{len(t['added'])} added / −{len(t['removed'])} removed mid-sprint"
        if t["added"] or t["removed"]
        else "no changes mid-sprint"
    )
    carry = _items(len(t["carry"])) + " carried over" if t["carry"] else "nothing carried over"
    return _tag("p", f"Scope: {changes} · {carry}", "note")


def _flow(t: dict) -> str:
    f = t["flow"]
    if f is None:
        return ""
    states = f" ({escape(', '.join(f['states']))})" if f["states"] else ""
    sentence = (
        f"{_day_count(str(f['total']))} typical from start to done: "
        f"{f['building']}% building · {f['waiting']}% waiting{states} · {f['other']}% other"
    )
    bar = (
        '<div aria-hidden="true" style="display:flex;height:6px;border-radius:3px;overflow:hidden">'
        + "".join(
            f'<span style="flex:{f[k]};background:{color}"></span>'
            for k, color in (("building", "var(--accent)"), ("waiting", "var(--warn)"), ("other", "#B8C0CA"))
        )
        + "</div>"
    )
    return _tag("div", _tag("h3", "Where the time goes (last 6 sprints)") + _tag("p", sentence) + bar)


def _fold(summary: str, inner: str) -> str:
    return _tag("details", _tag("summary", escape(summary)) + inner)


def _summary(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) == 1:
        return _tag("p", escape(lines[0]))
    return _tag("ul", "".join(_tag("li", escape(line)) for line in lines))


def _carry(t: dict) -> str:
    held = sum(1 for c in t["carry"] if c["held"])
    rows = [
        [
            _tag("span", f"#{c['id']}", "id"),
            escape(c["title"] or ""),
            str(c["sprints"]),
            escape(c["state"]),
            _days(c["held"]) if c["held"] else "–",
            escape(c["reason"]) or "–",
        ]
        for c in t["carry"]
    ]
    if not rows:
        return _tag(
            "div",
            _tag("h3", "Carried over items (0)") + _tag("p", "No work items carried over.", "note"),
            "carry-empty",
        )
    table = _table(["Work item", "Title", "Times carried", "At sprint end", "On Hold", "Reason"], rows)
    held_suffix = f" ({held} On Hold)" if held else ""
    return _fold(f"Carried over items ({len(rows)}){held_suffix}", table)


def _delivered(t: dict) -> str:
    items = t["delivered_items"]
    if not items:
        return _tag("p", "No work items delivered.", "note")
    late = sum(d["late"] for d in items)
    head = ["Work item", "Title", "Planned or added"] + (["Late"] if late else [])
    rows = [
        [_tag("span", f"#{d['id']}", "id"), escape(d["title"] or ""), "Planned" if d["planned"] else "Added"]
        + ([_tag("span", "Late", "chip amber") if d["late"] else ""] if late else [])
        for d in items
    ]
    late_suffix = f", {late} late" if late else ""
    return _fold(f"Delivered items ({len(items)}{late_suffix})", _table(head, rows))


def _trend_table(t: dict, metrics=None) -> str:
    tr = t["trend"]
    metrics = t.get("manager_metrics", DEFAULT_MANAGER_METRICS) if metrics is None else metrics
    rows = [_METRIC_RENDERERS[m][1](t, tr) for m in metrics]
    return _table([""] + [s["sprint"] for s in tr], [[r[0]] + [escape(c) for c in r[1:]] for r in rows])


def _exec_team(t: dict) -> str:
    """The manager_ card: highlights, stats, delivered and carried-over panels, trend; no RAG
    status and no per-person data."""
    parts = [_tag("div", _tag("h2", escape(t["team"])) + _capacity(t), "top")]
    summary, exec_note = t["notes"]["summary"], t["notes"]["exec_note"]
    if summary or exec_note:
        parts.append(
            _tag(
                "div",
                _tag("h3", "Delivery highlights")
                + (_summary(summary) if summary else "")
                + (_tag("p", escape(exec_note)) if exec_note else ""),
            )
        )
    metrics = t.get("manager_metrics", DEFAULT_MANAGER_METRICS)
    po = t.get("po_satisfaction") if "po_satisfaction" in metrics else None
    po_feedback = (
        _tag("p", f"PO feedback: {escape(po['comment'])}", "note") if po and po.get("comment") else ""
    )
    parts += [
        _tag("div", _tag("h3", "Delivery stats") + _figs(t, metrics) + _scope(t) + po_feedback),
        _flow(t) if t.get("show_flow", True) else "",
        _delivered(t),
        _carry(t),
    ]
    if metrics:
        parts.append(_fold(f"Delivery stats per sprint ({_last(len(t['trend']))})", _trend_table(t, metrics)))
    return _tag("article", "\n".join(parts), "squad")


def _footnote(dm: bool, teams: list[dict] | None = None) -> str:
    has_po = False
    if dm:
        first = (
            "<b>Method.</b> Each team counts the work items of one level set in config.json: "
            "stories (backlog items), tasks, or a list of work item types. The figures per team "
            "are in the manager report. "
        )
        lines = [
            first + "Planned = in the sprint and assigned to one of the team's engineers at the end of "
            "its first day; work that becomes theirs later counts as added. Scope is set by the "
            "customer's product owner. A sprint gets credit for the work items closed while on its "
            "iteration, even after its end date. Cycle time = calendar days from first active to done, "
            "inclusive; we show the median and the 85th percentile (P85), not averages, so figures can "
            "differ from Azure DevOps widgets. Ages and the status rule use the team's P85 over its "
            f"last {TREND} sprints. "
            "First-time pass = share of delivered work items never sent back from a test state "
            "and never reopened, counted per team; “not tracked” means no item moved back and "
            f"fewer than half of the delivered items went through a test state in the last {TREND} "
            "sprints."
        ]
        lines.append(
            f"<b>Status rule</b> (our convention, not an industry standard): GREEN = at least {round(GREEN_SHARE * 100)}% of "
            "planned work items delivered, not counting removed ones, and no carry-over older than the "
            "team's P85 (or no carry-over when P85 is unavailable); AMBER = one condition missed; RED = both missed, or a "
            f"work item waiting (in a waiting state or blocked) for more than {WAIT_RED_DAYS} working days."
        )
        lines.append(
            "<b>Running sprint.</b> Rows use the start of today in the configured time zone "
            "(end of yesterday). Working days count Monday–Friday through yesterday. Planned "
            "scope uses the end of the first day, clamped to that snapshot; added, removed and "
            "done use that snapshot too. There is no running carry-over or running status."
        )
        lines.append(
            "<b>Signals</b> (our convention, not an industry standard): Stuck = waiting or On Hold "
            f"over {WAIT_RED_DAYS} working days, or older than P85 and carried in; Waiting = waiting or Blocked "
            f"over {WAIT_FLAG_DAYS} working days; On Hold = in a hold state; Chase = older than P85; No movement "
            f"= no board change for {STALE_DAYS} working days; Not started = still Proposed after working "
            f"day {NOT_STARTED_DAY}. Carry-over = unfinished planned item still on a sprint at sprint end "
            f"(red when carried {CARRIED_FLAG}+ sprints and older than P85). No work = available, not onboarding, "
            f"no open assignment; Too much WIP = over {WIP_FLAG} in progress; Nothing delivered = no delivery in "
            "the closed sprint. Check = never active, reopened or shared; On track = open without a signal; "
            "Done = delivered in the running sprint. Handled = answered without worsening (carry-over rows also "
            "need a local reason in this folder); ↑ = higher severity, a new signal kind or a new sprint since "
            "the answer. Answers inherited from another report folder do not supply a carry-over reason; "
            "change or clear and re-enter the Answer to publish it."
        )
    else:
        enabled = (
            {m for t in teams for m in t.get("manager_metrics", DEFAULT_MANAGER_METRICS)}
            if teams
            else set(DEFAULT_MANAGER_METRICS)
        )
        show_flow = any(t.get("show_flow", True) for t in teams) if teams else True
        has_po = "po_satisfaction" in enabled
        first = (
            "<b>Method.</b> Each team counts its own agreed work item types, so figures are not "
            "comparable between teams. PD = person-days available after leave. "
        )
        lines = [
            first + "Planned = in the sprint and assigned to one of the team's engineers at the end of "
            "its first day; work that becomes theirs later counts as added. Scope is set by the "
            "customer's product owner. A sprint gets credit for work items closed while on its "
            "iteration, even after its end date. Late = a planned work item closed after its sprint end date."
        ]
        tb = []
        if "throughput" in enabled:
            tb.append(
                "Work items per 10 person-days = delivered work items divided by available person-days "
                "after leave, times 10, pooled over the last 3 sprints."
            )
        if "build_time" in enabled:
            tb.append(
                "Build time = calendar time from first active while ours to the first test handoff or "
                "delivered close, minus waiting, hold and Blocked time; the median of measured items "
                "delivered in the last 3 sprints."
            )
        if tb:
            lines.append(" ".join(tb))
        if "first_pass" in enabled:
            lines.append(
                "First-time pass = share of delivered work items never sent back from a test state "
                "and never reopened, pooled over the last 3 sprints; “not tracked” means no item moved "
                "back and fewer than half of the delivered items went through a test state in the "
                "last 6 sprints. – means no scored items; a return caps the display at 99%."
            )
        higher = [
            label
            for key, label in (("throughput", "throughput"), ("first_pass", "first-time pass"))
            if key in enabled
        ]
        better_parts = (["higher " + " or ".join(higher)] if higher else []) + (
            ["lower build time"] if "build_time" in enabled else []
        )
        if better_parts:
            arrows_label = "Delivery-metric arrows" if has_po else "Arrows"
            lines.append(
                f"{arrows_label} compare the last 3 sprints with the 3 before: the triangle follows the number; "
                f"green means better ({', '.join(better_parts)}), red worse. "
                "No arrow means a missing value or no change at displayed precision. Compare a team only "
                "with its own past. Arrows are a direction, not a verdict, especially with small samples."
            )
        if show_flow:
            lines.append(
                "Where the time goes = median start-to-done time over the last 6 sprints, split using "
                "pooled time shares into building, waiting (named states or Blocked) and other, including "
                "hold and rework. Parts are whole percentages of that time, with the remainder in other."
            )
        if has_po:
            lines.append(
                "PO satisfaction = product owner's 1–5 sprint rating recorded by the delivery manager "
                "(5 Very satisfied, 4 Satisfied, 3 Mixed, 2 Dissatisfied, 1 Very dissatisfied); its arrow "
                "compares with the most recent prior rated sprint in the last 6 sprints."
            )
    lines.append(
        "On Hold = days the work item spent in a hold state or tagged Blocked during the "
        "sprint; shown for context and not counted against the team."
    )
    figures_label = "Board figures" if has_po else "Figures"
    lines.append(
        f"{figures_label} are recomputed from the board history on every run and do not change for "
        "a closed sprint unless the history changes."
    )
    return _tag("div", "".join(_tag("p", line) for line in lines), "foot")


def _teams(teams: list[dict]) -> list[str]:
    return [_missing(t) if t.get("missing") else _exec_team(t) for t in teams]


def render_manager(sprint_name: str, teams: list[dict], generated: datetime, board: str = "") -> str:
    """Render closed-sprint results; `board` is the board data time."""
    title = f"{sprint_name} delivery summary"
    body = [_head(title, teams, generated, False, board)]
    return _page(
        title, "\n".join(body + _teams(teams) + [_footnote(False, teams)]), light=True, extra_css=MANAGER_CSS
    )


def link_ids(page: str, organization: str, project: str) -> str:
    """Turn every #123 in the text after </style> into a link to the Azure DevOps work item."""
    head, sep, body = page.partition("</style>")
    if not (organization and project and sep):
        return page
    base = escape(f"{organization.rstrip('/')}/{quote(project)}/_workitems/edit/")

    def _item_link(n: re.Match[str]) -> str:
        return f'<a href="{base}{n.group(1)}">#{n.group(1)}</a>'

    def link(m: re.Match[str]) -> str:
        return ">" + re.sub(r"(?<![\w&])#(\d+)\b", _item_link, m.group(1)) + "<"

    return head + sep + re.sub(r">([^<]+)<", link, body)


def _action_rank(row: dict) -> int:
    if row.get("handled"):
        return 3
    if row["level"] == "green":
        return 4 if row["open"] else 5
    return SEVERITY[row["level"]]


def _actions(result: dict) -> str:
    """Render the per-person action table, grouping each person's running-sprint Done items into one row."""
    rows = result["rows"]
    people = sorted(
        result["people"],
        key=lambda p: (min([3] + [_action_rank(r) for r in rows if r["person"] == p["name"]]), p["name"]),
    )
    body = []
    for person in people:
        mine = sorted(
            (r for r in rows if r["person"] == person["name"]),
            key=lambda r: (_action_rank(r), r["id"] is None, r["id"] or 0, r["badge"]),
        )
        who = (
            escape(person["name"])
            + "<br>"
            + _tag("span", f"{_num(person['available'])} / {_num(person['nominal'])} d available", "note")
        )
        if person["onboarding"]:
            who += " " + _tag("span", "onboarding", "chip wait")
        done = [r for r in mine if r["badge"] == "Done" and not r["open"] and not r.get("handled")]
        mine = [r for r in mine if r not in done] + ([{"done": done}] if done else [])
        for index, row in enumerate(mine or [None]):
            cell = f'<td rowspan="{max(1, len(mine))}">{who}</td>' if index == 0 else ""
            if row is None:
                cells = ["nothing open", "–", "–", "–", "–", "–"]
            elif "done" in row:
                done_count = f" ({len(done)})" if len(done) > 1 else ""
                cells = [
                    "<br>".join(escape(f"#{r['id']} {r['title']}") for r in done),
                    escape(", ".join(sorted({r["sprint"] for r in done}))),
                    f'<span class="chip green">Done{done_count}</span>',
                    "",
                    "",
                    "–",
                ]
            else:
                handled = row.get("handled")
                badge = "Handled" if handled else row["badge"] + (" ↑" if row.get("worse") else "")
                tooltip = ' title="worse since your answer"' if row.get("worse") and not handled else ""
                chip_cls = (
                    "grey"
                    if handled
                    else "track"
                    if row["level"] == "green" and row["open"]
                    else row["level"]
                )
                signal = f'<span class="chip {chip_cls}"{tooltip}>{escape(badge)}</span>'
                work = f"#{row['id']} {row['title']}" if row["id"] is not None else "–"
                cells = [
                    escape(work),
                    escape(row["sprint"]),
                    signal,
                    escape(row["problem"]),
                    escape(row.get("answer") or row["ask"]),
                    _days(row["age"]) if row["open"] else "–",
                ]
            body.append(_tag("tr", cell + "".join(_tag("td", c) for c in cells)))
    head = "".join(
        _tag("th", h) for h in ("Person", "Work item", "Sprint", "Signal", "Problem", "Action", "Age")
    )
    return _tag("div", _tag("table", _tag("thead", _tag("tr", head)) + _tag("tbody", "".join(body))), "tbl")


def _dm_team(t: dict) -> str:
    """Team name and closed-sprint status (the running sprint has none), last sprint's comparison, then current sprint."""
    st = t.get("status")
    chip = " " + _tag("span", escape(st["colour"]), "chip " + st["colour"]) if st else ""
    parts = [_tag("div", _tag("h2", escape(t["team"]) + chip), "top")]
    last = f"Last sprint: {t['sprint']['name']} ({_dates(t['sprint'])})" if t.get("sprint") else "Last sprint"
    parts.append(_tag("h3", escape(last)))
    if st:
        parts.append(_tag("p", escape((st["reason"] + (" (set by hand)" if st["manual"] else "")).strip())))
    r = t.get("rest")
    if r is None:
        message = (
            "No closed sprint available for comparison."
            if t.get("missing")
            else "No other delivered work items in this team's areas."
        )
        parts.append(_tag("p", message, "note"))
    else:
        c = t["cycle"]
        rows = [
            [
                "<b>Our engineers</b>",
                str(t["capacity"]["engineers"]),
                str(len(t["delivered"])),
                _days(c["median"]),
                _days(c["p85"]),
                _pct(t["waiting_share"]),
            ],
            [
                "Rest of squad",
                str(r["assignees"]),
                str(r["delivered"]),
                _days(r["median"]),
                _days(r["p85"]),
                _pct(r["waiting_share"]),
            ],
        ]
        parts += [
            _table(
                ["", "Engineers", "Work items delivered", "Cycle time median", "P85", "Waiting share"], rows
            ),
            _tag(
                "p",
                "The two groups may pick up different kinds of work. Use this as context, not as a ranking.",
                "note",
            ),
        ]
    actions = t.get("actions", {"people": [], "rows": [], "current": None})
    current = actions["current"]
    if current:
        line = (
            f"{current['sprint']['name']} · day {current['day']} of {current['days']} working days · "
            f"planned done {len(current['delivered_planned'])} of {len(current['planned'])} · "
            f"+{len(current['added'])} added / −{len(current['removed'])} removed · "
            f"{len(current['held'])} On Hold · {len(current['waiting'])} waiting"
        )
        parts += [_tag("h3", "Current sprint"), _tag("p", escape(line), "meta")]
    parts.append(_actions(actions))
    return _tag("article", "\n".join(parts), "squad")


def render_dm(sprint_name: str, teams: list[dict], generated: datetime, board: str = "") -> str:
    title = f"{sprint_name} delivery review"
    notes = [
        _tag("li", escape(f"{t['team']}: {t['notes']['note']}"))
        for t in teams
        if t.get("notes", {}).get("note")
    ]
    body = [
        _head(title, teams, generated, True, board),
        _tag(
            "div",
            _tag("h3", "Your notes for this sprint")
            + (_tag("ul", "".join(notes)) if notes else _tag("p", "No notes for this sprint.", "note")),
            "squad",
        ),
    ]
    body += [
        _missing(t) if t.get("missing") and not t.get("actions", {}).get("current") else _dm_team(t)
        for t in teams
    ]
    return _page(title, "\n".join([*body, _footnote(True)]), light=True)


def render_person(person: Person, rows: list[dict], generated: datetime) -> str:
    cells = []
    for r in rows:
        if r.get("available") is None:
            cells.append([escape(r["sprint"]), _tag("span", "not on the team", "note")] + ["–"] * 4)
        else:
            cells.append(
                [
                    escape(r["sprint"]),
                    f"{_num(r['available'])} / {_num(r['nominal'])}",
                    str(r["delivered"]),
                    str(r["in_progress"]),
                    _days(r["median"]),
                    _ids(r["items"]),
                ]
            )
    body = [
        _tag("p", "Private: delivery manager only", "private"),
        _tag("h1", escape(person.name)),
        _tag("p", escape(f"Team {person.team} · last {len(rows)} sprints"), "meta"),
        _tag("p", f"generated {generated.strftime('%Y-%m-%d %H:%M %Z').strip()}", "meta"),
        _table(
            [
                "Sprint",
                "Days available",
                "Work items delivered",
                "In progress at end",
                "Median cycle",
                "Work items",
            ],
            cells,
        ),
        _tag(
            "div",
            _tag(
                "p",
                "This page lists what the board recorded, not an assessment. Cycle "
                "time = calendar days from first active to done, inclusive.",
            )
            + _tag("p", "Per-person reporting may fall under local labour law and works-council rules."),
            "foot",
        ),
    ]
    return _page(person.name, "\n".join(body), light=True)


def folder_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "-", name)


def report_folder(name: str) -> str:
    """Kebab-case folder name for a report: 'Sprints ended by 2026-11-07' -> 'sprints-ended-by-2026-11-07'."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "report"


def _warn(out, line: str) -> None:
    print(f"warning: {line}", file=out)


def _load(args, client, out):
    """Config, people, board history, merged calendar leave by team.csv name (combining any
    duplicate calendar aliases), today and team names from team.csv, for `report` and `person`."""
    d = args.dir
    if not os.path.isfile(os.path.join(d, "config.json")):
        raise ValueError(f"no config.json in {d}; run ledger init first")

    def warn(line: str) -> None:
        _warn(out, line)

    cfg = load_config(d, warn)
    people = read_team(os.path.join(d, "team.csv"))
    teams = sorted({p.team for p in people if p.team})
    for team in teams:
        if not any(k.lower() == team.lower() and (v or {}).get("count") for k, v in cfg.teams.items()):
            _warn(
                out,
                f"team {team} in team.csv has no counting level in config.json; it counts stories "
                f'(set "teams": {{"{team}": {{"count": "stories" or "tasks"}}}})',
            )
    for team in sorted(cfg.teams):
        if team.lower() not in {t.lower() for t in teams}:
            _warn(out, f"team {team} in config.json has no one in team.csv; it is not reported")
    today = date.fromisoformat(args.today) if args.today else date.today()
    if not cfg.vacations or not os.path.isfile(cfg.vacations):
        raise ValueError(f"team calendar not found: '{cfg.vacations}'; set \"vacations\" in config.json")
    if args.offline:
        data = load_cache(d)
    else:
        data = save_cache(
            d,
            fetch(
                client or Client(cfg.organization, cfg.project),
                cfg.query,
                sorted({e for p in people for e in p.emails}),
                today,
                cfg.history_days,
                warn,
                known=cached_ids(d),
            ),
            warn,
        )
    vac = read_vacations(cfg.vacations, cfg.half_day_codes)
    names = [p.name for p in people]
    days_off: dict[str, dict[date, float]] = {}
    for cal, days in vac.days_off.items():
        target = resolve_name(cal, names, cfg.name_aliases)
        if target is None:
            vac.warnings.append(
                f"calendar name '{cal}' matches no one in team.csv; its leave is not "
                "counted (add it to name_aliases in config.json)"
            )
            continue
        mine = days_off.setdefault(target, {})
        for day, off in days.items():
            mine[day] = max(mine.get(day, 0.0), off)
    vac.warnings += [
        f"no calendar row for {p.name}; their leave is not counted"
        for p in people
        if p.name not in days_off and (p.left is None or p.left > today)
    ]
    for line in vac.warnings:
        _warn(out, line)
    vac.days_off = days_off
    return cfg, people, data, vac, today, teams


def _ended(data, cfg, people, team, today) -> list[dict]:
    return [s for s in team_sprints(data, cfg, people, team) if date.fromisoformat(s["end"]) < today]


def _no_sprint(data: dict, cfg, people: list[Person], team: str) -> str:
    """Why a team has no ended sprint."""
    emails = {e.lower() for p in people if p.team.lower() == team.lower() for e in p.emails}
    if any((r["assigned"] or "").lower() in emails for r in data["revisions"]):
        return "No ended sprint: none of the team's counted work items is on a sprint."
    n = sum(1 for p in people if p.team.lower() == team.lower() and not p.left)
    return (
        f"No work assigned: none of the team's {n} engineer{'' if n == 1 else 's'} has a work item assigned in the "
        f"board history (last {cfg.history_days} days)."
    )


def _pick(data, cfg, people, team_names, today, wanted) -> tuple:
    """(report name, {team: sprint or None}); without `wanted` each team gets its last ended
    sprint."""
    by_team = {t: team_sprints(data, cfg, people, t) for t in team_names}
    if wanted:
        if not any(s["name"] == wanted for ss in by_team.values() for s in ss):
            raise ValueError(f"no team has a sprint named '{wanted}'")
        if any(
            s["name"] == wanted and date.fromisoformat(s["end"]) >= today
            for ss in by_team.values()
            for s in ss
        ):
            raise ValueError(f"sprint '{wanted}' has not ended before {today.isoformat()}")
        return wanted, {
            t: next((s for s in reversed(ss) if s["name"] == wanted), None) for t, ss in by_team.items()
        }
    picked = {
        t: next((s for s in reversed(ss) if date.fromisoformat(s["end"]) < today), None)
        for t, ss in by_team.items()
    }
    found = [s for s in picked.values() if s]
    if not found:
        raise ValueError(f"no sprint ended before {today.isoformat()}")
    names = {s["name"] for s in found}
    name = names.pop() if len(names) == 1 else f"Sprints ended by {max(s['end'] for s in found)}"
    return name, picked


def run_report(args, client: Client | None, out) -> int:
    """Generate manager_ and dm_ reports: compute team results, reconcile DM answers, and attach
    scoped carry-over reasons."""
    cfg, people, data, vac, today, team_names = _load(args, client, out)
    if not cfg.test_states and any("first_pass" in metrics_of(cfg, team) for team in team_names):
        _warn(out, "no test states in config.json; first-time pass counts reopens only")
    name, picked = _pick(data, cfg, people, team_names, today, args.sprint)
    fetched = parse_ts(data["fetched"]).astimezone(local_tz(cfg.timezone)).strftime("%Y-%m-%d %H:%M")
    source_label = "cached board from" if args.offline else "fetched board at"
    print(f"data: {source_label} {fetched}", file=out)
    folder = os.path.join(args.dir, "reports", report_folder(name))
    os.makedirs(folder, exist_ok=True)
    notes_path = os.path.join(folder, "notes.md")
    notes = load_report_notes(args.dir, folder, picked, data["iterations"], lambda line: _warn(out, line))
    loaded_warnings = len(notes.warnings)
    running = {
        team: next(
            (
                s
                for s in reversed(team_sprints(data, cfg, people, team))
                if s["start"] <= today.isoformat() <= s["end"]
            ),
            None,
        )
        if not args.sprint
        else None
        for team in picked
    }
    teams = []
    actions = {}
    for team, sprint in picked.items():
        if sprint is not None:
            result = team_sprint(data, cfg, people, team, sprint, notes, vac.days_off)
        else:
            why = f"No sprint named {name}." if args.sprint else _no_sprint(data, cfg, people, team)
            result = {
                "team": team,
                "missing": why,
                "manager_metrics": metrics_of(cfg, team),
                "show_flow": show_flow_of(cfg, team),
                "notes": {"note": notes.note.get(team, "")},
            }
        actions[team] = team_actions(
            data, cfg, people, team, result if sprint else None, running[team], today, vac.days_off
        )
        result["actions"] = actions[team]
        teams.append(result)
    reconcile_notes(notes, actions, picked, today)
    for result in teams:
        if not result.get("missing"):
            reasons = carry_reasons(notes, result["team"])
            for carry in result["carry"]:
                carry["reason"] = reasons.get(carry["id"], "")
    write_report_notes(folder, notes)
    for s in {
        (t["sprint"]["name"], t["sprint"]["start"], t["sprint"]["end"]) for t in teams if not t.get("missing")
    }:
        if not any(date.fromisoformat(s[1]) <= d <= date.fromisoformat(s[2]) for d in vac.days):
            _warn(out, f"the calendar does not cover {s[0]} ({s[1]} to {s[2]}); leave is not counted")
    board = parse_ts(data["fetched"]).astimezone(local_tz(cfg.timezone)).strftime("%Y-%m-%d %H:%M %Z")
    generated = datetime.now().astimezone()
    paths, pages = [], []
    for prefix, render in (("manager", render_manager), ("dm", render_dm)):
        paths.append(os.path.join(folder, f"{prefix}_{today.isoformat()}.html"))
        pages.append(link_ids(render(name, teams, generated, board), cfg.organization, cfg.project))
        with open(paths[-1], "w", encoding="utf-8") as f:
            f.write(pages[-1])
    manager = unescape(pages[0]).lower()
    for term in sorted({t for p in people for t in [p.name, *p.emails]}):
        word = rf"(?<!\w){re.escape(term.lower())}(?!\w)"
        if (term.lower() in manager) if "@" in term else re.search(word, manager):
            _warn(out, f"manager file mentions {term}")
    for line in notes.warnings[loaded_warnings:]:
        _warn(out, line)
    for t in teams:
        if not t.get("missing") and not t["notes"]["summary"]:
            _warn(out, f"{t['team']} has no summary in notes.md; the manager card shows no highlights")
    for t in teams:
        if t.get("missing"):
            print(f"{t['team']}: {t['missing']}", file=out)
    print(f"Written:\n  {paths[0]}\n  {paths[1]}\n  {notes_path} (notes)", file=out)
    print(f"sprint: {name}\nnotes: {notes_path}", file=out)
    for t in sorted((t for t in teams if not t.get("missing")), key=lambda t: t["team"]):
        lines = [f"carry-over without note: #{c['id']} {c['title']}" for c in t["carry"] if not c["reason"]]
        lines += [
            f"worked on: #{w['id']} {w['title']} ({'delivered' if w['delivered'] else 'in progress'})"
            for w in t["worked_items"]
        ]
        for line in lines:
            print(f"{t['team']} {' '.join(line.split())}", file=out)
    for t in teams:
        for row in t["actions"]["rows"]:
            if row["level"] in ("red", "amber") and not row.get("handled"):
                item = f" #{row['id']}" if row["id"] is not None else ""
                badge_text = row["badge"] + (" ↑" if row.get("worse") else "")
                line = f"{t['team']} action: {row['person']}{item} {badge_text} - {row['problem']}"
                print(" ".join(line.split()), file=out)
    return 0


def run_person(args, client: Client | None, out) -> int:
    cfg, people, data, vac, today, _ = _load(args, client, out)
    person = email_index(people).get(args.email.strip().lower())
    if person is None:
        raise ValueError(f"no one in team.csv has the email {args.email}")
    rows = []
    for sprint in _ended(data, cfg, people, person.team, today)[-TREND:]:
        r = team_sprint(data, cfg, people, person.team, sprint, Notes(), vac.days_off)
        p = next((x for x in r["people"] if x["name"] == person.name), None)
        rows.append(
            {"sprint": sprint["name"]}
            if p is None
            else {
                "sprint": sprint["name"],
                "available": p["available"],
                "nominal": p["nominal"],
                "delivered": len(p["delivered"]),
                "in_progress": len(p["in_progress"]),
                "median": p["median"],
                "items": p["items"],
            }
        )
    path = os.path.join(
        args.dir, "people", f"person_{folder_name(person.name).replace(' ', '_')}_{today.isoformat()}.html"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_person(person, rows, datetime.now().astimezone()))
    print(f"Written:\n  {path}", file=out)
    return 0
