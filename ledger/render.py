"""The manager_, dm_ and person_ HTML pages, and the `report` and `person` commands."""
import os
import re
from datetime import date, datetime
from html import escape, unescape
from typing import Dict, List, Optional
from urllib.parse import quote

from ledger.ado import Client, cached_ids, fetch, load_cache, save_cache
from ledger.config import (Notes, Person, email_index, load_config, load_report_notes,
                           notes_for_team, read_team, reconcile_notes, write_report_notes)
from ledger.metrics import (CARRIED_FLAG, GREEN_SHARE, NOT_STARTED_DAY, STALE_DAYS, TREND,
                            WAIT_FLAG_DAYS, WAIT_RED_DAYS, WIP_FLAG, team_actions,
                            team_sprint, team_sprints)
from ledger.replay import local_tz, parse_ts
from ledger.xlsx import read_vacations, resolve_name

MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
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
.GREEN,.yes{background:var(--good-soft);color:var(--good)}
.AMBER,.unk{background:var(--warn-soft);color:var(--warn)}
.RED,.no{background:var(--bad-soft);color:var(--bad)}.wait,.track{background:var(--accent-soft);color:var(--accent)}
.hot{color:var(--bad);font-weight:600}
.red{background:var(--bad-soft);color:var(--bad)}.amber{background:var(--warn-soft);color:var(--warn)}
.grey{background:var(--bg);color:var(--muted)}.green{background:var(--good-soft);color:var(--good)}
.foot{font-size:12.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px;
display:flex;flex-direction:column;gap:6px}
.private{background:var(--bad-soft);color:var(--bad);font-size:12px;padding:6px 10px;
border-radius:6px;align-self:flex-start}
details{border:1px solid var(--line);border-radius:6px;padding:8px 12px}summary{cursor:pointer;font-weight:600}
.carry-empty{background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:12px 14px}"""


def _tag(name: str, inner: str, cls: str = "") -> str:
    return '<%s%s>%s</%s>' % (name, ' class="%s"' % cls if cls else "", inner, name)


def _num(x) -> str:
    return "–" if x is None else "%g" % round(x, 1)


def _days(x) -> str:
    return "–" if x is None else "%d d" % x


def _pct(x, cap=None) -> str:
    if x is None:
        return "–"
    n = round(x * 100)
    return "%d%%" % (min(cap, n) if cap is not None else n)


def _items(n: int) -> str:
    return "%d work item%s" % (n, "" if n == 1 else "s")


def _last(n: int) -> str:
    return "last sprint" if n == 1 else "last %d sprints" % n


def _ids(ids) -> str:
    return _tag("span", " ".join("#%d" % i for i in ids), "id") if ids else "–"


def _table(head: List[str], rows: List[List[str]]) -> str:
    """`head` is plain text; cells are HTML already."""
    th = "".join(_tag("th", escape(h)) for h in head)
    body = "".join(_tag("tr", "".join(_tag("td", c) for c in r)) for r in rows)
    return _tag("div", _tag("table", _tag("thead", _tag("tr", th)) + _tag("tbody", body)), "tbl")


def _day(d: date, year: bool = True) -> str:
    return "%d %s%s" % (d.day, MONTHS[d.month - 1], " %d" % d.year if year else "")


def _dates(sprint: dict) -> str:
    a, b = date.fromisoformat(sprint["start"]), date.fromisoformat(sprint["end"])
    return "%s – %s" % (_day(a, a.year != b.year), _day(b))


def _page(title: str, body: str, light: bool = False) -> str:
    """`light` drops the dark theme, for pages sent to readers whatever their system setting."""
    css = re.sub(r"@media \(prefers-color-scheme: dark\)\{.*?\}\}\n", "", CSS, flags=re.S) \
        if light else CSS
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<title>%s</title>\n<style>\n%s\n</style></head>\n<body><div class="wrap">\n%s\n'
            '</div></body></html>\n' % (escape(title), css, body))


def _head(title: str, teams: List[dict], generated: datetime, private: bool, board: str) -> str:
    dated = next((t["sprint"] for t in teams if not t.get("missing")), None)
    meta = [_dates(dated)] if dated else []
    meta.append("%d team%s" % (len(teams), "" if len(teams) == 1 else "s"))
    if board:
        meta.append("Board data as of %s" % board)
    return "\n".join(([_tag("p", "Internal: delivery manager and team leads only. Not for the customer.", "private")] if private else [])
                     + [_tag("h1", escape(title)), _tag("p", escape(" · ".join(meta)), "meta"),
                        _tag("p", "generated %s" % generated.strftime("%Y-%m-%d %H:%M %Z").strip(),
                             "meta")])


def _missing(t: dict) -> str:
    return _tag("article", _tag("div", _tag("h2", escape(t["team"])), "top")
                + _tag("p", escape(t["missing"]), "note"), "squad")


def _capacity(t: dict, dm: bool) -> str:
    c = t["capacity"]
    eng = "%d engineer%s" % (c["engineers"], "" if c["engineers"] == 1 else "s")
    if c["onboarding"]:
        eng += ", %d onboarding" % c["onboarding"]
    lines = ["%s (%s)" % (t["sprint"]["name"], _dates(t["sprint"])),
             "%s (%s PD capacity)" % (eng, _num(c["available"]))]
    if dm:
        lines.append("Counts: %s" % t["count"])
    return _tag("span", "<br>".join(escape(line) for line in lines), "cap")


def _fig(n: str, label: str, key: bool = False) -> str:
    return _tag("div", _tag("span", n, "n") + _tag("span", escape(label), "l"),
                "fig key" if key else "fig")


def _cycle(t: dict) -> str:
    c, w = t["cycle"], t["cycle_window"]
    if not c["n"]:
        main = _fig("–", "cycle time median / P85 (no work items delivered)")
    else:
        main = _fig("%d <small>/ %d days</small>" % (c["median"], c["p85"]),
                    "cycle time median / P85 (n = %s)" % _items(c["n"]))
    if c["n"] >= 10 or not w["n"]:
        return main
    return main + _fig("%d <small>/ %d days</small>" % (w["median"], w["p85"]),
                       "cycle time median / P85 over the %s (n = %s)"
                       % (_last(len(t["trend"])), _items(w["n"])))


def _first_pass(t: dict) -> str:
    f = t["first_pass"]
    if not f["scored"]:
        return _fig("–", "first-time pass")
    if not f["tracked"]:
        return _fig("not tracked", "first-time pass: no item moved back on the board in the "
                    "last %d sprints" % TREND)
    return _fig(_pct(f["share"], 99 if f["returned"] else 100),
                "first-time pass" + (" (%d returned)" % len(f["returned"]) if f["returned"] else ""))


def _figs(t: dict, carried: bool = False) -> str:
    figs = [_fig("%d <small>of %d</small>" % (len(t["delivered_planned"]), len(t["planned"])),
                 "planned work items delivered", True),
            _fig(str(len(t["delivered"])), "work items delivered in total")]
    if carried:
        figs.append(_fig(str(len(t["carry"])), "work items carried over"))
    figs += [_fig("+%d / −%d" % (len(t["added"]), len(t["removed"])),
                  "work items added / removed mid-sprint"),
             _fig(str(len(t["late"])), "planned work items completed after the sprint ended"),
             _cycle(t), _fig(_pct(t["waiting_share"]), "of cycle time spent in waiting states"),
             _first_pass(t)]
    return _tag("div", "".join(figs), "figs")


def _fold(summary: str, inner: str) -> str:
    return _tag("details", _tag("summary", escape(summary)) + inner)


def _summary(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) == 1:
        return _tag("p", escape(lines[0]))
    return _tag("ul", "".join(_tag("li", escape(line)) for line in lines))


def _carry(t: dict, age: bool = True) -> str:
    p85 = t["cycle_window"]["p85"]
    agreed = {"agreed": ("Yes", "yes"), "not agreed": ("No", "no")}
    rows = []
    for c in t["carry"]:
        old = p85 is not None and c["age"] is not None and c["age"] > p85
        word, cls = agreed.get(c["agreed"], ("Unknown", "unk"))
        rows.append([_tag("span", "#%d" % c["id"], "id"), escape(c["title"] or "")]
                    + ([_tag("span", _days(c["age"]), "hot" if old else "")] if age else [])
                    + [_tag("span", str(c["sprints"]), "hot" if c["sprints"] >= CARRIED_FLAG else ""),
                       _tag("span", escape(c["state"]), "chip wait") if c["waiting"]
                       else escape(c["state"]),
                       _days(c["held"]) if c["held"] else "–",
                       escape(c["reason"]) or "–", _tag("span", word, "chip " + cls)])
    held = sum(1 for c in t["carry"] if c["held"])
    if age and not rows:
        return _tag("div", _tag("h3", "Carried over") + _tag("p", "No work items carried over.", "note"))
    if not rows:
        return _tag("div", _tag("h3", "Carried over items (0)")
                    + _tag("p", "No work items carried over.", "note"), "carry-empty")
    table = _table(["Work item", "Title"] + (["Age"] if age else [])
                   + ["Sprints carried", "Now in", "On Hold", "Reason", "Agreed with PO"], rows)
    if age:
        heading = "Carried over (%d On Hold)" % held if held else "Carried over"
        return _tag("div", _tag("h3", heading) + table)
    return _fold("Carried over items (%d)%s" % (len(rows), " (%d On Hold)" % held if held else ""),
                 table)


def _delivered(t: dict) -> str:
    items = t["delivered_items"]
    if not items:
        return _tag("p", "No work items delivered.", "note")
    return _fold("Delivered items (%d)" % len(items), _table(
        ["Work item", "Title", "Planned or added"],
        [[_tag("span", "#%d" % d["id"], "id"), escape(d["title"] or ""),
          "Planned" if d["planned"] else "Added"] for d in items]))


def _trend_table(t: dict) -> str:
    tr = t["trend"]
    rows = [["Planned work items delivered"] + ["%d/%d" % (s["delivered_planned"], s["planned"])
                                                for s in tr],
            ["Work items delivered in total"] + [str(s["delivered"]) for s in tr],
            ["Cycle time P85 (days)"] + [_num(s["p85"]) for s in tr],
            ["First-time pass"] + ["–" if s["first_pass"] is None else
                                   _pct(s["first_pass"], 99 if s["first_pass"] < 1.0 else 100)
                                   if t["first_pass"]["tracked"] else "not tracked" for s in tr]]
    return _table([""] + [s["sprint"] for s in tr], [[r[0]] + [escape(c) for c in r[1:]] for r in rows])


def _trend(t: dict) -> str:
    return _tag("div", _tag("h3", _last(len(t["trend"])).capitalize()) + _trend_table(t))


def _exec_team(t: dict) -> str:
    """The manager_ card: highlights, stats, delivered and carried-over panels, trend; no RAG
    status and no per-person data."""
    parts = [_tag("div", _tag("h2", escape(t["team"])) + _capacity(t, False), "top")]
    summary, exec_note = t["notes"]["summary"], t["notes"]["exec_note"]
    if summary or exec_note:
        parts.append(_tag("div", _tag("h3", "Delivery highlights")
                          + (_summary(summary) if summary else "")
                          + (_tag("p", escape(exec_note)) if exec_note else "")))
    parts += [_tag("div", _tag("h3", "Delivery stats") + _figs(t, carried=True)), _delivered(t),
              _carry(t, age=False), _fold(_last(len(t["trend"])).capitalize(), _trend_table(t))]
    return _tag("article", "\n".join(parts), "squad")


def _team(t: dict, dm: bool) -> str:
    st = t["status"]
    reason = st["reason"] + (" (set by hand)" if st["manual"] else "")
    parts = [_tag("div", _tag("h2", escape(t["team"])) + _capacity(t, dm), "top"),
             _tag("p", _tag("span", escape(st["colour"]), "chip " + st["colour"]) + " "
                  + escape(reason.strip()))]
    if t["worked_on"]:
        parts.append(_tag("div", _tag("h3", "The team worked on") + _tag("ul", "".join(
            _tag("li", escape("%s (%s)" % (title, _items(n)))) for title, n in t["worked_on"]))))
    parts += [_summary(t["notes"][k]) for k in ("summary", "exec_note") if t["notes"][k]]
    parts += [_figs(t), _carry(t), _trend(t)]
    return _tag("article", "\n".join(parts), "squad")


def _footnote(dm: bool) -> str:
    first = ("<b>Method.</b> Each team counts the work items of one level, shown on its card as "
             "“Counts: stories” (backlog items), “Counts: tasks”, or a list of work item types. "
             if dm else
             "<b>Method.</b> Each team counts its own agreed work item types, so figures are not "
             "comparable between teams. PD = person-days available after leave. ")
    lines = [
        first + "Planned = in the sprint and assigned to one of the team's engineers at the end of "
        "its first day; work that becomes theirs later counts as added. Scope is set by the "
        "customer's product owner. A sprint gets credit for the work items closed while on its "
        "iteration, even after its end date. Cycle time = calendar days from first active to done, "
        "inclusive; we show the median and the 85th percentile (P85), not averages, so figures can "
        "differ from Azure DevOps widgets. Ages and the status rule use the team's P85 over its "
        "last %d sprints. " % TREND
        + "First-time pass = share of delivered work items never sent back from a test state "
        "and never reopened, counted per team; “not tracked” means no item moved back in the "
        "last %d sprints." % TREND]
    if dm:
        lines.append(
            "<b>Status rule</b> (our convention, not an industry standard): GREEN = at least %d%% of "
            "planned work items delivered, not counting removed ones, and no carry-over older than the "
            "team's P85 without PO agreement; AMBER = one condition missed; RED = both missed, or a "
            "work item waiting (in a waiting state or blocked) for more than %d working days."
            % (round(GREEN_SHARE * 100), WAIT_RED_DAYS))
        lines.append(
            "<b>Running sprint.</b> Rows use the start of today in the configured time zone "
            "(end of yesterday). Working days count Monday–Friday through yesterday. Planned "
            "scope uses the end of the first day, clamped to that snapshot; added, removed and "
            "done use that snapshot too. There is no running carry-over or running status.")
        lines.append(
            "<b>Signals</b> (our convention, not an industry standard): Stuck = waiting or On Hold "
            "over %d working days, or older than P85 and carried in; Waiting = waiting or Blocked "
            "over %d working days; On Hold = in a hold state; Chase = older than P85; No movement "
            "= no board change for %d working days; Not started = still Proposed after working "
            "day %d. Carry-over = no PO agreement or carried %d+ sprints (red when also older "
            "than P85). No work = available, not onboarding, no open assignment; Too much WIP = "
            "over %d in progress; Nothing delivered = no delivery in the closed sprint. Check = "
            "never active, reopened or shared; On track = open without a signal; Done = delivered "
            "in the running sprint. Handled = answered "
            "without worsening; ↑ = higher severity, a new signal kind or a new sprint since "
            "the answer. Copied answers do not renew PO agreement; change or clear and re-enter "
            "the Answer to renew it."
            % (WAIT_RED_DAYS, WAIT_FLAG_DAYS, STALE_DAYS, NOT_STARTED_DAY, CARRIED_FLAG, WIP_FLAG))
    lines.append("On Hold = days the work item spent in a hold state or tagged Blocked during the "
                 "sprint; shown for context and not counted against the team.")
    lines.append("Figures are recomputed from the board history on every run and do not change for "
                 "a closed sprint unless the history changes.")
    return _tag("div", "".join(_tag("p", line) for line in lines), "foot")


def _teams(teams: List[dict], dm: bool) -> List[str]:
    return [_missing(t) if t.get("missing") else (_team(t, True) if dm else _exec_team(t))
            for t in teams]


def render_manager(sprint_name: str, teams: List[dict], generated: datetime, board: str = "") -> str:
    """Render closed-sprint results; `board` is the board data time."""
    title = "%s delivery summary" % sprint_name
    body = [_head(title, teams, generated, False, board)]
    return _page(title, "\n".join(body + _teams(teams, False) + [_footnote(False)]), light=True)


def link_ids(page: str, organization: str, project: str) -> str:
    """Turn every #123 in the text after </style> into a link to the Azure DevOps work item."""
    head, sep, body = page.partition("</style>")
    if not (organization and project and sep):
        return page
    base = escape("%s/%s/_workitems/edit/" % (organization.rstrip("/"), quote(project)))

    def link(m):
        return ">" + re.sub(r"(?<![\w&])#(\d+)\b",
                            lambda n: '<a href="%s%s">#%s</a>' % (base, n.group(1), n.group(1)),
                            m.group(1)) + "<"
    return head + sep + re.sub(r">([^<]+)<", link, body)


def _action_rank(row: dict) -> int:
    if row.get("handled"):
        return 3
    return {"red": 0, "amber": 1, "grey": 2, "green": 4 if row["open"] else 5}[row["level"]]


def _actions(result: dict) -> str:
    rows = result["rows"]
    people = sorted(result["people"], key=lambda p: (
        min([3] + [_action_rank(r) for r in rows if r["person"] == p["name"]]), p["name"]))
    body = []
    for person in people:
        mine = sorted((r for r in rows if r["person"] == person["name"]), key=lambda r: (
            _action_rank(r), r["id"] is None, r["id"] or 0, r["badge"]))
        who = escape(person["name"]) + "<br>" + _tag("span", "%s / %s d available" % (
            _num(person["available"]), _num(person["nominal"])), "note")
        if person["onboarding"]:
            who += " " + _tag("span", "onboarding", "chip wait")
        done = [r for r in mine if r["badge"] == "Done" and not r["open"] and not r.get("handled")]
        mine = [r for r in mine if r not in done] + ([{"done": done}] if done else [])
        for index, row in enumerate(mine or [None]):
            cell = '<td rowspan="%d">%s</td>' % (max(1, len(mine)), who) if index == 0 else ""
            if row is None:
                cells = ["nothing open", "–", "–", "–", "–", "–"]
            elif "done" in row:  # one row for everything the person delivered in the running sprint
                cells = ["<br>".join(escape("#%d %s" % (r["id"], r["title"])) for r in done),
                         escape(", ".join(sorted({r["sprint"] for r in done}))),
                         '<span class="chip green">Done%s</span>' % (
                             " (%d)" % len(done) if len(done) > 1 else ""), "", "", "–"]
            else:
                handled = row.get("handled")
                badge = "Handled" if handled else row["badge"] + (" ↑" if row.get("worse") else "")
                tooltip = ' title="worse since your answer"' if row.get("worse") and not handled else ""
                signal = '<span class="chip %s"%s>%s</span>' % (
                    "grey" if handled else "track" if row["level"] == "green" and row["open"]
                    else row["level"], tooltip, escape(badge))
                work = ("#%d %s" % (row["id"], row["title"])) if row["id"] is not None else "–"
                cells = [escape(work), escape(row["sprint"]), signal, escape(row["problem"]),
                         escape(row.get("answer") or row["ask"]),
                         _days(row["age"]) if row["open"] else "–"]
            body.append(_tag("tr", cell + "".join(_tag("td", c) for c in cells)))
    head = "".join(_tag("th", h) for h in (
        "Person", "Work item", "Sprint", "Signal", "Problem", "Action", "Age"))
    return _tag("div", _tag("table", _tag("thead", _tag("tr", head))
                           + _tag("tbody", "".join(body))), "tbl")


def _dm_team(t: dict) -> str:
    parts = [_tag("div", _tag("h2", escape("%s: ours vs rest of squad" % t["team"])), "top")]
    r = t.get("rest")
    if r is None:
        message = ("No closed sprint available for comparison." if t.get("missing") else
                   "No other delivered work items in this team's areas.")
        parts.append(_tag("p", message, "note"))
    else:
        c = t["cycle"]
        rows = [["<b>Our engineers</b>", str(t["capacity"]["engineers"]), str(len(t["delivered"])),
                 _days(c["median"]), _days(c["p85"]), _pct(t["waiting_share"])],
                ["Rest of squad", str(r["assignees"]), str(r["delivered"]), _days(r["median"]),
                 _days(r["p85"]), _pct(r["waiting_share"])]]
        parts += [_table(["", "Engineers", "Work items delivered", "Cycle time median", "P85",
                          "Waiting share"], rows),
                  _tag("p", "The two groups may pick up different kinds of work. "
                            "Use this as context, not as a ranking.", "note")]
    actions = t.get("actions", {"people": [], "rows": [], "current": None})
    current = actions["current"]
    if current:
        line = ("%s · day %d of %d working days · planned done %d of %d · "
                "+%d added / −%d removed · %d On Hold · %d waiting") % (
            current["sprint"]["name"], current["day"], current["days"], len(current["delivered_planned"]),
            len(current["planned"]), len(current["added"]), len(current["removed"]),
            len(current["held"]), len(current["waiting"]))
        parts.append(_tag("p", escape(line), "meta"))
    parts.append(_actions(actions))
    return _tag("article", "\n".join(parts), "squad")


def render_dm(sprint_name: str, teams: List[dict], generated: datetime, board: str = "") -> str:
    title = "%s delivery review" % sprint_name
    notes = [_tag("li", escape("%s: %s" % (t["team"], t["notes"]["note"])))
             for t in teams if t.get("notes", {}).get("note")]
    body = [_head(title, teams, generated, True, board),
            _tag("div", _tag("h3", "Your notes for this sprint")
                 + (_tag("ul", "".join(notes)) if notes else
                    _tag("p", "No notes for this sprint.", "note")), "squad")]
    body += [_dm_team(t) for t in teams if not t.get("missing") or t.get("actions", {}).get("current")]
    body += [_tag("h2", "Team figures")] + _teams(teams, True)
    return _page(title, "\n".join(body + [_footnote(True)]), light=True)


def render_person(person: Person, rows: List[dict], generated: datetime) -> str:
    cells = []
    for r in rows:
        if r.get("available") is None:
            cells.append([escape(r["sprint"]), _tag("span", "not on the team", "note")] + ["–"] * 4)
        else:
            cells.append([escape(r["sprint"]), "%s / %s" % (_num(r["available"]), _num(r["nominal"])),
                          str(r["delivered"]), str(r["in_progress"]), _days(r["median"]),
                          _ids(r["items"])])
    body = [_tag("p", "Private: delivery manager only", "private"),
            _tag("h1", escape(person.name)),
            _tag("p", escape("Team %s · last %d sprints" % (person.team, len(rows))), "meta"),
            _tag("p", "generated %s" % generated.strftime("%Y-%m-%d %H:%M %Z").strip(), "meta"),
            _table(["Sprint", "Days available", "Work items delivered", "In progress at end",
                    "Median cycle", "Work items"], cells),
            _tag("div", _tag("p", "This page lists what the board recorded, not an assessment. Cycle "
                                  "time = calendar days from first active to done, inclusive.")
                 + _tag("p", "Per-person reporting may fall under local labour law and works-council "
                             "rules."), "foot")]
    return _page(person.name, "\n".join(body), light=True)


def folder_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "-", name)


def _warn(out, line: str) -> None:
    print("warning: %s" % line, file=out)


def _load(args, client, out):
    """Config, people, board history, the calendar (days off by team.csv name), today and the
    team names from team.csv, for `report` and `person`."""
    d = args.dir
    if not os.path.isfile(os.path.join(d, "config.json")):
        raise ValueError("no config.json in %s; run ledger init first" % d)
    cfg = load_config(d, lambda line: _warn(out, line))
    people = read_team(os.path.join(d, "team.csv"))
    teams = sorted({p.team for p in people if p.team})
    for team in teams:
        if not any(k.lower() == team.lower() and (v or {}).get("count") for k, v in cfg.teams.items()):
            _warn(out, "team %s in team.csv has no counting level in config.json; it counts stories "
                       "(set \"teams\": {\"%s\": {\"count\": \"stories\" or \"tasks\"}})" % (team, team))
    for team in sorted(cfg.teams):
        if team.lower() not in {t.lower() for t in teams}:
            _warn(out, "team %s in config.json has no one in team.csv; it is not reported" % team)
    today = date.fromisoformat(args.today) if args.today else date.today()
    if not cfg.vacations or not os.path.isfile(cfg.vacations):
        raise ValueError("team calendar not found: '%s'; set \"vacations\" in config.json"
                         % cfg.vacations)
    if args.offline:
        data = load_cache(d)
    else:
        warn = lambda line: _warn(out, line)  # noqa: E731
        data = save_cache(d, fetch(client or Client(cfg.organization, cfg.project), cfg.query,
                                   sorted({e for p in people for e in p.emails}), today,
                                   cfg.history_days, warn, known=cached_ids(d)), warn)
    vac = read_vacations(cfg.vacations, cfg.half_day_codes)
    names = [p.name for p in people]
    days_off = {}  # type: Dict[str, Dict[date, float]]
    for cal, days in vac.days_off.items():
        target = resolve_name(cal, names, cfg.name_aliases)
        if target is None:
            vac.warnings.append("calendar name '%s' matches no one in team.csv; its leave is not "
                                "counted (add it to name_aliases in config.json)" % cal)
            continue
        mine = days_off.setdefault(target, {})  # two calendar names for one person: merge
        for day, off in days.items():
            mine[day] = max(mine.get(day, 0.0), off)
    vac.warnings += ["no calendar row for %s; their leave is not counted" % p.name for p in people
                     if p.name not in days_off and (p.left is None or p.left > today)]
    for line in vac.warnings:
        _warn(out, line)
    vac.days_off = days_off
    return cfg, people, data, vac, today, teams


def _ended(data, cfg, people, team, today) -> List[dict]:
    return [s for s in team_sprints(data, cfg, people, team)
            if date.fromisoformat(s["end"]) < today]


def _no_sprint(data: dict, cfg, people: List[Person], team: str) -> str:
    """Why a team has no ended sprint."""
    emails = {e.lower() for p in people if p.team.lower() == team.lower() for e in p.emails}
    if any((r["assigned"] or "").lower() in emails for r in data["revisions"]):
        return "No ended sprint: none of the team's counted work items is on a sprint."
    n = sum(1 for p in people if p.team.lower() == team.lower() and not p.left)
    return ("No work assigned: none of the team's %d engineer%s has a work item assigned in the "
            "board history (last %d days)." % (n, "" if n == 1 else "s", cfg.history_days))


def _pick(data, cfg, people, team_names, today, wanted) -> tuple:
    """(report name, {team: sprint or None}); without `wanted` each team gets its last ended
    sprint."""
    by_team = {t: team_sprints(data, cfg, people, t) for t in team_names}
    if wanted:
        if not any(s["name"] == wanted for ss in by_team.values() for s in ss):
            raise ValueError("no team has a sprint named '%s'" % wanted)
        if any(s["name"] == wanted and date.fromisoformat(s["end"]) >= today
               for ss in by_team.values() for s in ss):
            raise ValueError("sprint '%s' has not ended before %s" % (wanted, today.isoformat()))
        return wanted, {t: next((s for s in reversed(ss) if s["name"] == wanted), None)
                        for t, ss in by_team.items()}
    picked = {t: next((s for s in reversed(ss) if date.fromisoformat(s["end"]) < today), None)
              for t, ss in by_team.items()}
    found = [s for s in picked.values() if s]
    if not found:
        raise ValueError("no sprint ended before %s" % today.isoformat())
    names = {s["name"] for s in found}
    name = names.pop() if len(names) == 1 else "Sprints ended by %s" % max(s["end"] for s in found)
    return name, picked


def run_report(args, client: Optional[Client], out) -> int:
    cfg, people, data, vac, today, team_names = _load(args, client, out)
    if not cfg.test_states:
        _warn(out, "no test states in config.json; first-time pass counts reopens only")
    name, picked = _pick(data, cfg, people, team_names, today, args.sprint)
    folder = os.path.join(args.dir, folder_name(name))
    os.makedirs(folder, exist_ok=True)
    notes_path = os.path.join(folder, "notes.md")
    notes = load_report_notes(args.dir, folder, picked, data["iterations"], lambda line: _warn(out, line))
    loaded_warnings = len(notes.warnings)
    running = {team: next((s for s in reversed(team_sprints(data, cfg, people, team))
                           if s["start"] <= today.isoformat() <= s["end"]), None)
               if not args.sprint else None for team in picked}
    # Always reconcile source signals without agreement. Otherwise an agreed row can
    # disappear, lose its baseline and return as unanswered on alternating reruns.
    closed = {team: team_sprint(data, cfg, people, team, sprint, Notes(), vac.days_off)
              if sprint else None for team, sprint in picked.items()}
    actions = {team: team_actions(data, cfg, people, team, closed[team], running[team], today, vac.days_off)
               for team in picked}
    reconcile_notes(notes, actions, picked, today)
    teams = []
    for team, sprint in picked.items():
        scoped = notes_for_team(notes, team)
        if sprint is not None:
            result = team_sprint(data, cfg, people, team, sprint, scoped, vac.days_off)
        else:
            why = ("No sprint named %s." % name if args.sprint
                   else _no_sprint(data, cfg, people, team))
            result = {"team": team, "missing": why, "notes": {"note": notes.note.get(team, "")}}
        refreshed = team_actions(data, cfg, people, team, result if sprint else None,
                                 running[team], today, vac.days_off)
        reconcile_notes(notes, {team: refreshed}, picked, today, observed=actions)
        result["actions"] = refreshed
        teams.append(result)
    write_report_notes(folder, notes)
    for s in {(t["sprint"]["name"], t["sprint"]["start"], t["sprint"]["end"])
              for t in teams if not t.get("missing")}:
        if not any(date.fromisoformat(s[1]) <= d <= date.fromisoformat(s[2]) for d in vac.days):
            _warn(out, "the calendar does not cover %s (%s to %s); leave is not counted" % s)
    board = parse_ts(data["fetched"]).astimezone(local_tz(cfg.timezone)).strftime("%Y-%m-%d %H:%M %Z")
    generated = datetime.now().astimezone()
    paths, pages = [], []
    for prefix, render in (("manager", render_manager), ("dm", render_dm)):
        paths.append(os.path.join(folder, "%s_%s.html" % (prefix, today.isoformat())))
        pages.append(link_ids(render(name, teams, generated, board),
                              cfg.organization, cfg.project))
        with open(paths[-1], "w", encoding="utf-8") as f:
            f.write(pages[-1])
    manager = unescape(pages[0]).lower()
    for term in sorted({t for p in people for t in [p.name] + p.emails}):
        word = r"(?<!\w)%s(?!\w)" % re.escape(term.lower())
        if (term.lower() in manager) if "@" in term else re.search(word, manager):
            _warn(out, "manager file mentions %s" % term)
    for line in notes.warnings[loaded_warnings:]:
        _warn(out, line)
    for t in teams:
        if not t.get("missing") and not t["notes"]["summary"]:
            _warn(out, "%s has no summary in notes.md; the manager card shows no highlights"
                  % t["team"])
    for t in teams:
        if t.get("missing"):
            print("%s: %s" % (t["team"], t["missing"]), file=out)
    print("Written:\n  %s\n  %s\n  %s (notes)" % (paths[0], paths[1], notes_path), file=out)
    print("sprint: %s\nnotes: %s" % (name, notes_path), file=out)
    for t in sorted((t for t in teams if not t.get("missing")), key=lambda t: t["team"]):
        lines = ["carry-over without note: #%d %s" % (c["id"], c["title"] or "")
                 for c in t["carry"] if c["id"] not in notes_for_team(notes, t["team"]).items]
        lines += ["worked on: #%d %s (%s)" % (w["id"], w["title"],
                                             "delivered" if w["delivered"] else "in progress")
                  for w in t["worked_items"]]
        for line in lines:
            print("%s %s" % (t["team"], " ".join(line.split())), file=out)
    for t in teams:
        for row in t["actions"]["rows"]:
            if row["level"] in ("red", "amber") and (not row.get("answer") or row.get("worse")
                    or (row.get("carry") and row["id"] not in notes_for_team(notes, t["team"]).items)):
                item = " #%d" % row["id"] if row["id"] is not None else ""
                line = "%s action: %s%s %s - %s" % (
                    t["team"], row["person"], item, row["badge"] + (" ↑" if row.get("worse") else ""), row["problem"])
                print(" ".join(line.split()), file=out)
    return 0


def run_person(args, client: Optional[Client], out) -> int:
    cfg, people, data, vac, today, _ = _load(args, client, out)
    person = email_index(people).get(args.email.strip().lower())
    if person is None:
        raise ValueError("no one in team.csv has the email %s" % args.email)
    rows = []
    for sprint in _ended(data, cfg, people, person.team, today)[-TREND:]:
        r = team_sprint(data, cfg, people, person.team, sprint, Notes(), vac.days_off)
        p = next((x for x in r["people"] if x["name"] == person.name), None)
        rows.append({"sprint": sprint["name"]} if p is None else {
            "sprint": sprint["name"], "available": p["available"], "nominal": p["nominal"],
            "delivered": len(p["delivered"]), "in_progress": len(p["in_progress"]),
            "median": p["median"], "items": p["items"]})
    path = os.path.join(args.dir, "people", "person_%s_%s.html"
                        % (folder_name(person.name).replace(" ", "_"), today.isoformat()))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_person(person, rows, datetime.now().astimezone()))
    print("Written:\n  %s" % path, file=out)
    return 0
