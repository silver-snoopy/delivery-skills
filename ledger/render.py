"""The manager_, dm_ and person_ HTML pages, and the `report` and `person` commands."""
import os
import re
from datetime import date, datetime
from html import escape, unescape
from typing import Dict, List, Optional

from ledger.ado import Client, cached_ids, fetch, load_cache, save_cache
from ledger.config import Notes, Person, email_index, load_config, parse_notes, read_team
from ledger.metrics import (histories, CARRIED_FLAG, GREEN_SHARE, STALE_DAYS, TREND, WAIT_FLAG_DAYS,
                            WAIT_RED_DAYS, WIP_FLAG, team_sprint, team_sprints)
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
.meta,.cap,.note,.ask{color:var(--muted);font-size:13px}
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
.chip{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600}
.GREEN,.yes{background:var(--good-soft);color:var(--good)}
.AMBER,.unk{background:var(--warn-soft);color:var(--warn)}
.RED,.no{background:var(--bad-soft);color:var(--bad)}.wait{background:var(--accent-soft);color:var(--accent)}
.hot{color:var(--bad);font-weight:600}
.foot{font-size:12.5px;color:var(--muted);border-top:1px solid var(--line);padding-top:14px;
display:flex;flex-direction:column;gap:6px}
.attn{background:var(--paper);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.row{border-left:6px solid var(--warn);border-bottom:1px solid var(--line);padding:12px 16px}
.row.red{border-left-color:var(--bad)}.row.grey{border-left-color:var(--line)}
.private{background:var(--bad-soft);color:var(--bad);font-size:12px;padding:6px 10px;
border-radius:6px;align-self:flex-start}"""


def _tag(name: str, inner: str, cls: str = "") -> str:
    return '<%s%s>%s</%s>' % (name, ' class="%s"' % cls if cls else "", inner, name)


def _num(x) -> str:
    return "–" if x is None else "%g" % round(x, 1)


def _days(x) -> str:
    return "–" if x is None else "%d d" % x


def _pct(x) -> str:
    return "–" if x is None else "%d%%" % round(x * 100)


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


def _page(title: str, body: str) -> str:
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '<title>%s</title>\n<style>\n%s\n</style></head>\n<body><div class="wrap">\n%s\n'
            '</div></body></html>\n' % (escape(title), CSS, body))


def _head(title: str, teams: List[dict], generated: datetime, private: bool, board: str) -> str:
    dated = next((t["sprint"] for t in teams if not t.get("missing")), None)
    meta = [_dates(dated)] if dated else []
    meta.append("%d team%s" % (len(teams), "" if len(teams) == 1 else "s"))
    if board:
        meta.append("Board data as of %s" % board)
    return "\n".join(([_tag("p", "Private: delivery manager only", "private")] if private else [])
                     + [_tag("h1", escape(title)), _tag("p", escape(" · ".join(meta)), "meta"),
                        _tag("p", "generated %s" % generated.strftime("%Y-%m-%d %H:%M %Z").strip(),
                             "meta")])


def _title(sprint_name: str, kind: str, running: bool) -> str:
    return "%s delivery %s%s" % (sprint_name, kind, " (sprint still running)" if running else "")


def _missing(sprint_name: str, t: dict) -> str:
    return _tag("p", escape("No sprint named %s for team %s." % (sprint_name, t["team"])), "note")


def _capacity(t: dict) -> str:
    c = t["capacity"]
    eng = "%d engineer%s" % (c["engineers"], "" if c["engineers"] == 1 else "s")
    if c["onboarding"]:
        eng += " (%d onboarding)" % c["onboarding"]
    return "counts %s · %s · %s of %s engineer-days available" % (
        t["count"], eng, _num(c["available"]), _num(c["nominal"]))


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


def _carry(t: dict) -> str:
    p85 = t["cycle_window"]["p85"]
    agreed = {"agreed": ("Yes", "yes"), "not agreed": ("No", "no")}
    rows = []
    for c in t["carry"]:
        old = p85 is not None and c["age"] is not None and c["age"] > p85
        word, cls = agreed.get(c["agreed"], ("Unknown", "unk"))
        rows.append([_tag("span", "#%d" % c["id"], "id"), escape(c["title"] or ""),
                     _tag("span", _days(c["age"]), "hot" if old else ""),
                     _tag("span", str(c["sprints"]), "hot" if c["sprints"] >= CARRIED_FLAG else ""),
                     _tag("span", escape(c["state"]), "chip wait") if c["waiting"]
                     else escape(c["state"]),
                     escape(c["reason"]) or "–", _tag("span", word, "chip " + cls)])
    if not rows:
        return _tag("div", _tag("h3", "Carried over") + _tag("p", "No work items carried over.", "note"))
    return _tag("div", _tag("h3", "Carried over") + _table(
        ["Work item", "Title", "Age", "Sprints carried", "Now in", "Reason", "Agreed with PO"], rows))


def _trend(t: dict) -> str:
    tr = t["trend"]
    rows = [["Planned work items delivered"] + ["%d/%d" % (s["delivered_planned"], s["planned"])
                                                for s in tr],
            ["Work items delivered in total"] + [str(s["delivered"]) for s in tr],
            ["Cycle time P85 (days)"] + [_num(s["p85"]) for s in tr]]
    return _tag("div", _tag("h3", _last(len(tr)).capitalize()) + _table(
        [""] + [s["sprint"] for s in tr], [[r[0]] + [escape(c) for c in r[1:]] for r in rows]))


def _team(t: dict) -> str:
    st = t["status"]
    reason = st["reason"] + (" (set by hand)" if st["manual"] else "")
    parts = [_tag("div", _tag("h2", escape(t["team"])) + _tag("span", escape(_capacity(t)), "cap"),
                  "top"),
             _tag("p", _tag("span", escape(st["colour"]), "chip " + st["colour"]) + " "
                  + escape(reason.strip()))]
    if t["worked_on"]:
        parts.append(_tag("div", _tag("h3", "The team worked on") + _tag("ul", "".join(
            _tag("li", escape("%s (%s)" % (title, _items(n)))) for title, n in t["worked_on"]))))
    parts += [_tag("p", escape(t["notes"][k])) for k in ("summary", "exec_note") if t["notes"][k]]
    figs = [_fig("%d <small>of %d</small>" % (len(t["delivered_planned"]), len(t["planned"])),
                 "planned work items delivered", True),
            _fig(str(len(t["delivered"])), "work items delivered in total"),
            _fig("+%d / −%d" % (len(t["added"]), len(t["removed"])),
                 "work items added / removed mid-sprint"),
            _fig(str(len(t["late"])), "planned work items completed after the sprint ended"),
            _cycle(t), _fig(_pct(t["waiting_share"]), "of cycle time spent in waiting states")]
    parts += [_tag("div", "".join(figs), "figs"), _carry(t), _trend(t)]
    return _tag("article", "\n".join(parts), "squad")


def _footnote(dm: bool) -> str:
    lines = [
        "<b>Method.</b> Each team counts work items at one level, shown beside its name: "
        "“counts stories” (backlog items) or “counts tasks”, never both. Planned = in "
        "the sprint at the end of its first day. Scope is set by the customer's product owner. A sprint "
        "gets credit for the work items completed within its dates, as in Azure DevOps velocity. Cycle "
        "time = calendar days from first active to done, inclusive; we show the median and the 85th "
        "percentile (P85), not averages, so figures can differ from Azure DevOps widgets. Ages and the "
        "status rule use the team's P85 over its last %d sprints." % TREND,
        "<b>Status rule</b> (our convention, not an industry standard): GREEN = at least %d%% of planned "
        "work items delivered, not counting removed ones, and no carry-over older than the team's P85 "
        "without PO agreement; AMBER = one condition missed; RED = both missed, or a work item waiting "
        "(in a waiting state or blocked) for more than %d working days."
        % (round(GREEN_SHARE * 100), WAIT_RED_DAYS)]
    if dm:
        lines.append(
            "<b>Needs attention</b> thresholds (our convention, not an industry standard), evaluated "
            "at the sprint end: open longer than the team's P85; carried over %d or more sprints; "
            "waiting (in a waiting state or blocked) more than %d working days; no board change for "
            "%d working days; more than %d work items in progress for one engineer."
            % (CARRIED_FLAG, WAIT_FLAG_DAYS, STALE_DAYS, WIP_FLAG))
    lines.append("Figures are recomputed from the board history on every run and do not change for "
                 "a closed sprint unless the history changes.")
    return _tag("div", "".join(_tag("p", line) for line in lines), "foot")


def _teams(sprint_name: str, teams: List[dict]) -> List[str]:
    return [_missing(sprint_name, t) if t.get("missing") else _team(t) for t in teams]


def render_manager(sprint_name: str, teams: List[dict], generated: datetime, running: bool = False,
                   board: str = "") -> str:
    """`board` is the board data time; `running` marks a sprint that has not ended."""
    title = _title(sprint_name, "summary", running)
    body = [_head(title, teams, generated, False, board)]
    return _page(title, "\n".join(body + _teams(sprint_name, teams) + [_footnote(False)]))


def _person_row(p: dict) -> List[str]:
    name = escape(p["name"]) + (" " + _tag("span", "onboarding", "chip wait") if p["onboarding"] else "")
    oldest = "%s %s" % (_ids([p["oldest"][0]]), _days(p["oldest"][1])) if p["oldest"] else "–"
    worked = _tag("span", "no item assigned", "hot") if p["no_assignment"] else _ids(p["items"])
    return [name, "%s / %s" % (_num(p["available"]), _num(p["nominal"])), str(len(p["delivered"])),
            str(len(p["in_progress"])), oldest, _days(p["median"]), worked]


def _dm_team(t: dict) -> str:
    parts = [_tag("div", _tag("h2", escape("%s: ours vs rest of squad" % t["team"])), "top")]
    r = t["rest"]
    if r is None:
        parts.append(_tag("p", "No other delivered work items in this team's areas.", "note"))
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
    parts.append(_tag("div", _tag("h3", "People") + _table(
        ["Engineer", "Days available", "Work items delivered", "In progress at end", "Oldest open",
         "Median cycle", "Worked on"], [_person_row(p) for p in t["people"]])))
    return _tag("article", "\n".join(parts), "squad")


def render_dm(sprint_name: str, teams: List[dict], generated: datetime, running: bool = False,
              board: str = "") -> str:
    title = _title(sprint_name, "review", running)
    present = [t for t in teams if not t.get("missing")]
    notes = [_tag("li", escape("%s: %s" % (t["team"], t["notes"]["note"])))
             for t in present if t["notes"]["note"]]
    body = [_head(title, teams, generated, True, board),
            _tag("h2", "Needs attention"),
            _tag("div", _tag("div", _tag("p", "<b>Your notes for this sprint</b>")
                             + (_tag("ul", "".join(notes)) if notes else
                                _tag("p", "No notes for this sprint.", "note")), "row grey"),
                 "attn")]
    for t in present:
        rows = [_tag("div", _tag("p", escape(a["text"])) + _tag("p", escape(a["ask"]), "ask"),
                     "row " + a["level"]) for a in t["attention"]]
        body.append(_tag("div", _tag("h3", escape(t["team"])) + (
            _tag("div", "".join(rows), "attn") if rows
            else _tag("p", "Nothing needs attention.", "note"))))
    body += [_missing(sprint_name, t) for t in teams if t.get("missing")]
    body += [_dm_team(t) for t in present]
    body += [_tag("h2", "Team figures (as in the manager report)")] + _teams(sprint_name, teams)
    return _page(title, "\n".join(body + [_footnote(True)]))


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
    return _page(person.name, "\n".join(body))


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


def _check_notes(notes: Notes, teams: List[dict], name: str, out) -> None:
    """Warn about notes.txt lines that no reported team uses."""
    present = [t for t in teams if not t.get("missing")]
    reported = {t["team"].lower() for t in present}
    for kind in ("summary", "note", "exec note", "status"):
        for team in getattr(notes, kind.replace(" ", "_")):
            if team.lower() not in reported:
                _warn(out, "notes.txt has a %s %s line, but no team %s is in this report"
                      % (team, kind, team))
    carried = {c["id"] for t in present for c in t["carry"]}
    for i in sorted(set(notes.items) - carried):
        _warn(out, "notes.txt #%d is not a carry-over of any team in %s" % (i, name))


def run_report(args, client: Optional[Client], out) -> int:
    cfg, people, data, vac, today, team_names = _load(args, client, out)
    by_team = {t: team_sprints(data, cfg, people, t) for t in team_names}
    name = args.sprint
    if not name:
        ended = [s for t in team_names for s in _ended(data, cfg, people, t, today)]
        if not ended:
            raise ValueError("no sprint ended before %s" % today.isoformat())
        name = max(ended, key=lambda s: (s["end"], s["start"], s["name"]))["name"]
    if not any(s["name"] == name for ss in by_team.values() for s in ss):
        raise ValueError("no team has a sprint named '%s'" % name)
    folder = os.path.join(args.dir, folder_name(name))
    os.makedirs(folder, exist_ok=True)
    notes_path = os.path.join(folder, "notes.txt")
    if not os.path.exists(notes_path):
        open(notes_path, "w", encoding="utf-8").close()
    with open(notes_path, encoding="utf-8-sig") as f:
        notes = parse_notes(f.read())
    teams = []
    for team, sprints in by_team.items():
        sprint = next((s for s in reversed(sprints) if s["name"] == name), None)
        teams.append({"team": team, "missing": True} if sprint is None else
                     team_sprint(data, cfg, people, team, sprint, notes, vac.days_off))
    for s in {(t["sprint"]["start"], t["sprint"]["end"]) for t in teams if not t.get("missing")}:
        if not any(date.fromisoformat(s[0]) <= d <= date.fromisoformat(s[1]) for d in vac.days):
            _warn(out, "the calendar does not cover %s (%s to %s); leave is not counted" % (name, *s))
    running = any(date.fromisoformat(t["sprint"]["end"]) >= today
                  for t in teams if not t.get("missing"))
    if running:
        _warn(out, "%s has not ended by %s; the report shows it as it stands (sprint still running)"
              % (name, today.isoformat()))
    board = parse_ts(data["fetched"]).astimezone(local_tz(cfg.timezone)).strftime("%Y-%m-%d %H:%M %Z")
    generated = datetime.now().astimezone()
    paths, pages = [], []
    for prefix, render in (("manager", render_manager), ("dm", render_dm)):
        paths.append(os.path.join(folder, "%s_%s.html" % (prefix, today.isoformat())))
        pages.append(render(name, teams, generated, running, board))
        with open(paths[-1], "w", encoding="utf-8") as f:
            f.write(pages[-1])
    manager = unescape(pages[0]).lower()
    for term in sorted({t for p in people for t in [p.name] + p.emails}):
        word = r"(?<!\w)%s(?!\w)" % re.escape(term.lower())
        if (term.lower() in manager) if "@" in term else re.search(word, manager):
            _warn(out, "manager file mentions %s" % term)
    for line in notes.warnings:
        _warn(out, line)
    _check_notes(notes, teams, name, out)
    for t in teams:
        if t.get("missing"):
            print("No sprint named %s for team %s." % (name, t["team"]), file=out)
    print("Written:\n  %s\n  %s\n  %s (notes)" % (paths[0], paths[1], notes_path), file=out)
    print("sprint: %s\nnotes: %s" % (name, notes_path), file=out)
    hist = histories(data, cfg.not_delivered_reasons)
    for t in sorted((t for t in teams if not t.get("missing")), key=lambda t: t["team"]):
        lines = ["carry-over without note: #%d %s" % (c["id"], c["title"] or "")
                 for c in t["carry"] if c["id"] not in notes.items]
        lines += ["delivered: #%d %s" % (i, hist[i].title or "") for i in sorted(t["delivered"])]
        for line in lines:
            print("%s %s" % (t["team"], " ".join(line.split())), file=out)
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
