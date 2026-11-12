"""The analyze, explain and trace commands."""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import history, reopen as reopening
from .buckets import time_split
from .config import Config, Roster, load_config, load_roster, validate
from .errors import LedgerError
from .home import Home, slug
from .ado.pseudonym import role_map
from .metrics import RULES_VERSION, analyze_team_sprint
from .model import Snapshot, Sprint, _dt_in
from .phases import AreaPhaseMapper, PhaseMapper
from .textaccess import for_command
from .timeline import build_timeline, find_bulk_edits
from .workcal import WorkCalendar, display_days

OUTCOME_LABELS = {
    "delivered": "Delivered",
    "closed_not_delivered": "Closed, not delivered",
    "awaiting_acceptance": "Finished, awaiting acceptance",
    "carried_in_progress": "Carried over, in progress",
    "carried_not_started": "Carried over, not started",
    "reclosed": "Re-closed",
    "active_outside_sprint": "Active outside a sprint",
    "assigned_outside_sprint": "Assigned, not started",
}


# ---- locating snapshots ----------------------------------------------------

def _collected(home: Home, team: str) -> List[Tuple[Sprint, Path]]:
    """(sprint, dir) for each collected sprint of the team, oldest first."""
    root = home.path("teams", slug(team), "sprints")
    out = []
    if root.is_dir():
        for d in root.iterdir():
            f = d / "snapshot" / "sprints.json"
            if not f.is_file():
                continue
            raw = json.loads(f.read_text(encoding="utf-8"))
            for s in raw.get(team, []):
                if s["id"] == d.name:
                    out.append((Sprint.from_json(s), d))
    return sorted(out, key=lambda x: x[0].end)


def _no_snapshot(team: str) -> LedgerError:
    return LedgerError("No collected snapshot for team %s." % team,
                       "Run `ledger collect --team %s` first." % team)


def _load_snapshot(home: Home, team: str, sprint_id: Optional[str]
                   ) -> Tuple[Snapshot, Sprint]:
    found = _collected(home, team)
    if sprint_id is not None:
        found = [x for x in found if x[0].id == sprint_id]
    if not found:
        raise _no_snapshot(team)
    sprint, d = found[-1]
    return Snapshot.load(d / "snapshot"), sprint


def _window(snap: Snapshot, cfg: Config, team: str, own: Sprint
            ) -> List[Sprint]:
    cutoff = _dt_in(snap.meta["cutoff"])
    sps = snap.sprints[team]
    upto = sps[:sps.index(own) + 1]
    done = [s for s in upto if s.end <= cutoff]
    return done[-int(cfg.thresholds["window_sprints"]):]


# ---- unmapped states -------------------------------------------------------

def unmapped_states(snap: Snapshot, cfg: Config, team: str
                    ) -> Dict[str, Dict[Tuple[str, Optional[str]], set]]:
    """Every (state, column) with no phase rule that analysing `team` from
    this snapshot would meet: team name -> pair -> ticket ids. The column
    is given only when that team has some rule for the state."""
    cutoff = _dt_in(snap.meta["cutoff"])
    out: Dict[str, Dict[Tuple[str, Optional[str]], set]] = {}

    def check(ticket, owner: str) -> None:
        tm = cfg.team(owner)
        mapper = PhaseMapper(tm, cfg)
        ruled = {(rule.state or "").strip().lower() for rule in tm.phases}
        for r in ticket.revisions:
            if r.changed >= cutoff:
                continue
            try:
                mapper.map(r.state, r.column, r.column_done)
            except LedgerError:
                column = r.column if r.state.strip().lower() in ruled \
                    else None
                out.setdefault(owner, {}).setdefault(
                    (r.state, column), set()).add(ticket.id)

    def counted(ticket) -> bool:
        return any(r.type in cfg.unit_types for r in ticket.revisions)

    for t in snap.tickets:
        if counted(t):
            for owner in sorted({cfg.team_for_area(r.area) or team
                                 for r in t.revisions}):
                check(t, owner)
    for t in snap.squad:
        if counted(t) and any(cfg.team_for_area(r.area) == team
                              for r in t.revisions):
            check(t, team)
    return out


def require_mapped(snap: Snapshot, cfg: Config, team: str) -> None:
    """One error naming every unmapped state, so the delivery manager can
    fix the mapping in one pass."""
    found = unmapped_states(snap, cfg, team)
    if not found:
        return
    lines, total = [], 0
    for owner in sorted(found):
        pairs = sorted(found[owner].items(),
                       key=lambda kv: (kv[0][0].lower(), kv[0][1] or ""))
        total += len(pairs)
        lines.append("%d board state%s of team %s ha%s no phase rule:" % (
            len(pairs), "" if len(pairs) == 1 else "s", owner,
            "s" if len(pairs) == 1 else "ve"))
        for (state, column), ids in pairs:
            rule = {"state": state}
            if column is not None:
                rule["column"] = column
            rule["phase"] = "?"
            lines.append("  %s  (%d ticket%s)" % (
                json.dumps(rule, ensure_ascii=False), len(ids),
                "" if len(ids) == 1 else "s"))
    raise LedgerError(
        "\n".join(lines),
        "Add each line to that team's \"phases\" list in config.json with "
        "\"?\" replaced by todo, doing, handoff, done or removed (a "
        "handoff rule may also carry a \"wait_label\"), then run analyze "
        "again. Nothing was analysed.")


# ---- analyze ---------------------------------------------------------------

def run_analyze(home: Home, cfg: Config, roster: Roster, team: str,
                sprint_id: Optional[str] = None) -> Dict[str, Any]:
    snap, own = _load_snapshot(home, team, sprint_id)
    earlier: List[Dict] = []
    rows = []
    moved: List[Dict[str, Any]] = []
    warnings: List[str] = []
    checked = False
    roles = role_map(home, cfg.backlog_roles, team)
    wanted = sorted({r.id for r in cfg.backlog_roles if r.applies_to(team)}
                    - set(roles.values()))
    if wanted:
        warnings.append(
            "%s: no board identity matched backlog role(s) %s; their changes "
            "count as another party. Check the role's sign-in names, or run "
            "`ledger collect --team \"%s\" --refresh` if this data was "
            "collected before roles could be matched."
            % (team, ", ".join(wanted), team))
    for s in _window(snap, cfg, team, own):
        d = home.team_sprint_dir(team, s.id)
        parts = d.relative_to(home.root).parts
        if (d / "issued.json").exists():
            m = home.read_json(*parts, "metrics.json")
            if m is not None:
                earlier.append(m)
                rows.append({"id": s.id, "name": s.name,
                             "backfilled": bool(m.get("backfilled")),
                             "recomputed": False})
                continue
        if not checked:
            require_mapped(snap, cfg, team)
            checked = True
        m, ledger = analyze_team_sprint(snap, cfg, roster, team, s, roles)
        m["backfilled"] = s.id != own.id
        history.apply(m, ledger, list(earlier), cfg)
        m["earlier_rules_sprints"] = [
            e["sprint"]["name"] for e in earlier
            if e.get("rules_version") != RULES_VERSION]
        entry = reopening.pending(d, home)
        if entry is not None:
            try:
                old = home.read_json(*reopening.superseded_dir(d, entry)
                                     .relative_to(home.root).parts,
                                     "metrics.json")
            except LedgerError:
                old = None
            if isinstance(old, dict) and isinstance(old.get("figures"),
                                                    dict):
                changes = reopening.figure_changes(old["figures"],
                                                   m["figures"])
            else:
                changes = []
                warnings.append("%s %s: the superseded figures are not "
                                "available for comparison."
                                % (team, s.name))
            m["reopened"] = {k: entry.get(k) for k in (
                "edition", "as_of", "reopened_at", "superseded")
                if entry.get(k) is not None}
            m["reopened"].update(note=entry.get("note", ""),
                                 moved=[c["key"] for c in changes])
            moved += [dict(c, sprint_id=s.id) for c in changes]
        home.write_json(m, *parts, "metrics.json")
        home.write_json(ledger, *parts, "ledger.json")
        earlier.append(m)
        rows.append({"id": s.id, "name": s.name,
                     "backfilled": m["backfilled"], "recomputed": True})
    lines = ["%s: analysed %d sprint(s)" % (team, len(rows))]
    for r in rows:
        m = next(x for x in earlier if x["sprint"]["id"] == r["id"])
        lines.append("  %s  %s%s  %s" % (
            r["name"], "recomputed" if r["recomputed"] else "issued, kept",
            ", backfilled" if r["backfilled"] else "",
            (m.get("status") or {}).get("label", "")))
        if not r["recomputed"]:
            lines[-1] += ("  (an issued sprint can be corrected with "
                          "`ledger reopen`)")
        elif m.get("reopened"):
            lines[-1] += ("  reopened from edition %03d; %d figure(s) moved"
                          % (int(m["reopened"]["edition"] or 0),
                             len(m["reopened"]["moved"])))
    lines += ["    %s %s: %s -> %s" % (c["sprint_id"], c["key"], c["old"],
                                       c["new"]) for c in moved]
    lines += ["  warning: " + w for w in warnings]
    return {"summary": "\n".join(lines), "team": team, "sprints": rows,
            "moved": moved, "warnings": warnings}


def _setup(home: Home) -> Tuple[Config, Roster]:
    cfg, roster = load_config(home), load_roster(home)
    validate(cfg, roster)
    return cfg, roster


def _analyze_cmd(args, home: Home) -> Dict[str, Any]:
    cfg, roster = _setup(home)
    if args.all:
        teams = [t.name for t in cfg.teams if _collected(home, t.name)]
        if not teams:
            raise LedgerError("No team has a collected snapshot.",
                              "Run `ledger collect` first.")
        results = [run_analyze(home, cfg, roster, t, None) for t in teams]
        return {"summary": "\n".join(r["summary"] for r in results),
                "teams": results,
                "moved": [dict(c, team=r["team"]) for r in results
                          for c in r["moved"]],
                "warnings": [w for r in results for w in r["warnings"]]}
    if not args.team:
        raise LedgerError("analyze needs --team or --all.",
                          "Name a team from config.json, or pass --all.")
    return run_analyze(home, cfg, roster, args.team, args.sprint)


# ---- explain ---------------------------------------------------------------

def _explain_cmd(args, home: Home) -> Dict[str, Any]:
    d = home.team_sprint_dir(args.team, args.sprint)
    parts = d.relative_to(home.root).parts
    ledger = home.read_json(*parts, "ledger.json")
    metrics = home.read_json(*parts, "metrics.json")
    if ledger is None or metrics is None:
        raise LedgerError(
            "No analysis for team %s sprint %s." % (args.team, args.sprint),
            "Run `ledger analyze --team %s` first." % args.team)
    if args.figure not in ledger:
        keys = sorted(ledger)
        more = " (and %d more)" % (len(keys) - 40) if len(keys) > 40 else ""
        raise LedgerError("Unknown figure %r." % args.figure,
                          "Valid keys: %s%s." % (", ".join(keys[:40]), more))
    items = ledger[args.figure]
    tickets = metrics.get("tickets", {})
    access = for_command(args, home)

    def title(t: Dict) -> str:
        return access.title((t.get("title") or "")[:80])
    lines = ["%s (%d)" % (args.figure, len(items))]
    for it in items:
        if isinstance(it, dict) and "ticket" in it:
            t = tickets.get(str(it["ticket"]), {})
            lines.append("  %s  rev %s  %s" % (it["ticket"], it.get("rev"),
                                               title(t)))
        elif isinstance(it, int) and str(it) in tickets:
            t = tickets[str(it)]
            lines.append("  %s  %s  %s  %s" % (
                it, t.get("type", ""), title(t),
                OUTCOME_LABELS.get(t.get("outcome"), t.get("outcome", ""))))
        else:
            lines.append("  %s" % (it,))
    return {"summary": "\n".join(lines), "figure": args.figure,
            "items": items}


# ---- trace -----------------------------------------------------------------

def _holder(roster: Roster, actor: Optional[str]) -> str:
    if actor is None:
        return "unassigned"
    if actor.startswith("p:"):
        for p in roster.people:
            if p.id == actor[2:]:
                return p.display_name
    return actor


def _find_ticket(home: Home, cfg: Config, ticket_id: int,
                 team: Optional[str]):
    teams = [team] if team else [t.name for t in cfg.teams]
    best = None
    for name in teams:
        found = _collected(home, name)
        if not found:
            continue
        sprint, d = found[-1]
        if best is None or sprint.end > best[1].end:
            snap = Snapshot.load(d / "snapshot")
            t = next((t for t in snap.tickets if t.id == ticket_id), None)
            if t is not None:
                best = (name, sprint, snap, t)
    if best is None:
        raise LedgerError("Ticket %d is not in any collected snapshot."
                          % ticket_id,
                          "Check the id, or run `ledger collect` for the "
                          "team that owns it.")
    return best


def _changes(ticket, cal: WorkCalendar, by_path: Dict[str, str]
             ) -> List[Dict[str, Any]]:
    """What changed in each revision: state, assignee, sprint and text.
    Actors are returned raw; callers decide how to show them."""
    out: List[Dict[str, Any]] = []
    prev = None
    for r in sorted(ticket.revisions, key=lambda r: (r.changed, r.rev)):
        sprint = by_path.get(r.iteration, "no sprint")
        if prev is None:
            what = [{"field": "created", "from": None, "to": r.state},
                    {"field": "sprint", "from": None, "to": sprint}]
            if r.assignee:
                what.append({"field": "assignee", "from": None,
                             "to": r.assignee})
        else:
            what = []
            if r.state != prev.state:
                what.append({"field": "state", "from": prev.state,
                             "to": r.state})
            if r.assignee != prev.assignee:
                what.append({"field": "assignee", "from": prev.assignee,
                             "to": r.assignee})
            if r.iteration != prev.iteration:
                what.append({"field": "sprint",
                             "from": by_path.get(prev.iteration, "no sprint"),
                             "to": sprint})
            if r.desc_hash != prev.desc_hash:
                what.append({"field": "description", "from": None,
                             "to": None})
            if r.ac_hash != prev.ac_hash:
                what.append({"field": "acceptance", "from": None,
                             "to": None})
        prev = r
        if what:
            local = r.changed.astimezone(cal.tz)
            out.append({"at": local.strftime("%Y-%m-%d %H:%M"),
                        "date": local.date().isoformat(), "rev": r.rev,
                        "by": r.by, "changes": what})
    return out


def trace_data(home: Home, cfg: Config, roster: Roster, ticket_id: int,
               team: Optional[str] = None) -> Dict[str, Any]:
    """One ticket's whole history as data: intervals, phase events,
    revision changes, whole-life time split and per-sprint outcomes.
    Holders and actors are raw identities (roster id, pseudonym or None)."""
    team, own, snap, ticket = _find_ticket(home, cfg, ticket_id, team)
    cal = WorkCalendar(cfg.timezone, cfg.holidays)
    cutoff = _dt_in(snap.meta["cutoff"])
    th = cfg.thresholds
    bulk = find_bulk_edits(snap.tickets, int(th["bulk_edit_count"]),
                           int(th["bulk_edit_seconds"]))
    # each revision is read with the rules of the team owning its area
    tl = build_timeline(ticket, AreaPhaseMapper(cfg, team), cutoff, bulk)
    by_path = {s.path: s.name for s in snap.sprints.get(team, [])}
    intervals = []
    for i in tl.intervals:
        intervals.append({
            "start": i.start.astimezone(cal.tz).strftime("%Y-%m-%d %H:%M"),
            "days": display_days(cal.working_days(i.start, i.end)),
            "phase": i.phase.name.lower(), "state": i.state,
            "wait_label": i.wait_label, "holder": i.holder,
            "sprint": by_path.get(i.iteration, "no sprint")})
    events = []
    for e in tl.events:
        words = [w for w, on in (("returned", e.is_return),
                                 ("reopened", e.reopen),
                                 ("backward", e.backward),
                                 ("administrative", e.admin)) if on]
        events.append({
            "at": e.at.astimezone(cal.tz).strftime("%Y-%m-%d %H:%M"),
            "rev": e.rev, "by": e.by,
            "from": e.from_phase.name.lower() if e.from_phase is not None
            else "none",
            "to": e.to_phase.name.lower(), "flags": words})
    split: Dict[str, float] = {}
    if tl.intervals:
        raw = time_split(tl, tl.intervals[0].start, cutoff, cal, team,
                         roster)
        split = {k: display_days(v) for k, v in sorted(raw.items())
                 if display_days(v) > 0}
    outcomes = []
    for s in _window(snap, cfg, team, own):
        m = home.read_json(*home.team_sprint_dir(team, s.id).relative_to(
            home.root).parts, "metrics.json")
        if m is None:
            out, inv = "not analysed", ""
        else:
            t = m.get("tickets", {}).get(str(ticket.id))
            out = OUTCOME_LABELS.get(t["outcome"], t["outcome"]) if t \
                else "not counted"
            inv = t["involvement"] if t else ""
        outcomes.append({"sprint": s.name, "outcome": out,
                         "involvement": inv})
    last = ticket.revisions[-1]
    return {"ticket": ticket.id, "team": team, "type": last.type,
            "title": ticket.title, "intervals": intervals, "events": events,
            "changes": _changes(ticket, cal, by_path), "time_split": split,
            "outcomes": outcomes}


def _trace_cmd(args, home: Home) -> Dict[str, Any]:
    cfg, roster = _setup(home)
    d = trace_data(home, cfg, roster, args.ticket, args.team)
    shown = for_command(args, home, cfg).title(d["title"])
    lines = ["%d  %s  %s" % (d["ticket"], d["type"], shown)]
    intervals = []
    for i in d["intervals"]:
        row = {k: i[k] for k in ("start", "days", "phase", "state",
                                 "sprint")}
        row["holder"] = _holder(roster, i["holder"])
        intervals.append(row)
        lines.append("  %(start)s  %(days)5s d  %(phase)-7s  %(state)s  "
                     "%(holder)s  %(sprint)s" % row)
    events = []
    for e in d["events"]:
        row = {k: e[k] for k in ("at", "rev", "from", "to", "flags")}
        events.append(row)
        lines.append("  %s  rev %d  %s -> %s%s" % (
            row["at"], row["rev"], row["from"], row["to"],
            ("  " + ", ".join(row["flags"])) if row["flags"] else ""))
    for o in d["outcomes"]:
        lines.append("  %s  %s%s" % (o["sprint"], o["outcome"],
                                     ("  " + o["involvement"])
                                     if o["involvement"] else ""))
    return {"summary": "\n".join(lines), "ticket": d["ticket"],
            "title": shown,
            "team": d["team"], "intervals": intervals, "events": events,
            "outcomes": d["outcomes"]}


# ---- registration ----------------------------------------------------------

def add_parser(subparsers) -> None:
    p = subparsers.add_parser("analyze", help="compute metrics for a team")
    p.add_argument("--team")
    p.add_argument("--sprint", help="sprint id whose snapshot to use")
    p.add_argument("--all", action="store_true",
                   help="every team with a snapshot")
    p.set_defaults(func=_analyze_cmd)

    p = subparsers.add_parser("explain", help="list what is behind a figure")
    p.add_argument("--team", required=True)
    p.add_argument("--sprint", required=True)
    p.add_argument("figure")
    p.set_defaults(func=_explain_cmd)

    p = subparsers.add_parser("trace", help="show one ticket's history")
    p.add_argument("ticket", type=int)
    p.add_argument("--team")
    p.set_defaults(func=_trace_cmd)
