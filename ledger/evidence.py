"""Evidence briefs: what the board recorded for one person across the
sprints they were on, or for one team across its recent sprints. Built
from the analysed files on disk; nothing is recomputed. The brief states
observations only."""
import argparse
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import annotations, risks
from .config import Config, Roster, load_config, load_roster, validate
from .errors import LedgerError
from .home import Home, slug
from .names_check import names_check
from .textaccess import for_command
from .render import _write_text
from .render.evidence_cards import (MAX_CARDS, flag_reasons, on_card,
                                    ticket_card)
from .render.evidence_html import (DISCLAIMER, render_person_brief,
                                   render_team_brief)
from .render.fmt import returns_reason_text
from .report import FOOTNOTE, strip_person_keys

__all__ = ["DISCLAIMER", "build_person_brief", "build_team_brief",
           "render_person_brief", "render_team_brief", "add_parser"]

_NOW = ("carried_in_progress", "active_outside_sprint")
_CARRIED = ("carried_in_progress", "carried_not_started")
_ANALYZE = "Run `ledger collect` and `ledger analyze --all` first."


# ---- inputs ----------------------------------------------------------------

def _team_sprints(home: Home, team: str, as_of: Optional[date]
                  ) -> List[Dict[str, Any]]:
    """Analysed sprints of a team finishing on or before as_of, oldest
    first, each with its metrics, ledger and current annotations."""
    root = home.path("teams", slug(team), "sprints")
    out = []
    if not root.is_dir():
        return out
    for d in root.iterdir():
        if not (d / "metrics.json").is_file():
            continue
        parts = d.relative_to(home.root).parts
        m = home.read_json(*parts, "metrics.json")
        if as_of is not None and m["sprint"]["finish"][:10] > as_of.isoformat():
            continue
        out.append({
            "team": team, "metrics": m,
            "ledger": home.read_json(*parts, "ledger.json", default={}) or {},
            "annotations": annotations.current(
                home.read_json(*parts, annotations.FILE, default=[]) or [])})
    out.sort(key=lambda x: (x["metrics"]["sprint"]["finish"][:10],
                            x["metrics"]["sprint"]["start"][:10]))
    return out


def _today(cfg: Config) -> date:
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def _base(cfg: Config, picked: List[Dict]) -> Dict[str, Any]:
    first, last = picked[0]["metrics"]["sprint"], picked[-1]["metrics"]["sprint"]
    return {"org_url": cfg.org_url, "project": cfg.project,
            "vendor_label": cfg.vendor_label, "footnote": FOOTNOTE,
            "generated": _today(cfg).isoformat(),
            "period": {"start": first["start"][:10],
                       "finish": last["finish"][:10]}}


def _carry_answer(ts: Dict, tid: str) -> Dict:
    key = "carry:%s:%s" % (tid, ts["metrics"]["sprint"]["id"])
    return ts["annotations"].get(key) or {}


def _cards(home: Home, cfg: Config, roster: Roster, found: Dict[str, Tuple]
           ) -> List[Dict[str, Any]]:
    """found: ticket id -> (team sprint, ticket, reasons). Most sprints
    carried first, then most returns, at most MAX_CARDS."""
    order = sorted(found.items(), key=lambda kv: (
        -(kv[1][1].get("sprints_carried") or 0),
        -len(kv[1][1].get("returns") or []), int(kv[0])))
    out = []
    for tid, (ts, t, reasons) in order[:MAX_CARDS]:
        card = ticket_card(home, cfg, roster, ts["team"], tid, t, reasons)
        card["team"] = ts["team"]
        card["sprint"] = ts["metrics"]["sprint"]["name"]
        card["annotations"] = ts["annotations"]
        out.append(card)
    return out


# ---- person brief ----------------------------------------------------------

def _person_row(ts: Dict, p: Dict) -> Dict[str, Any]:
    s = ts["metrics"]["sprint"]
    prog, car = p.get("in_progress") or {}, p.get("carried") or {}
    return {
        "team": ts["team"], "sprint": s["name"], "start": s["start"][:10],
        "finish": s["finish"][:10],
        "available_days": p.get("available_days") or 0.0,
        "nominal_days": p.get("nominal_days") or 0.0,
        "delivered_worked_on": p.get("delivered_worked_on") or 0,
        "handed_off": p.get("handed_off") or 0,
        "delivered_elsewhere": p.get("delivered_elsewhere") or 0,
        "handed_off_elsewhere": p.get("handed_off_elsewhere") or 0,
        "in_progress": {"count": prog.get("count") or 0,
                        "oldest_days": prog.get("oldest_days")},
        "carried": {"count": car.get("count") or 0,
                    "longest_sprints": car.get("longest_sprints") or 0},
        "backward_moves": p.get("backward_moves") or 0,
        "returns": p.get("returns") or 0,
        "median_days_in_progress": p.get("median_days_in_progress"),
        "days_nothing_in_progress": p.get("days_nothing_in_progress") or 0.0,
        "todo_assigned_max": p.get("todo_assigned_max") or 0,
        "last_board_change": p.get("last_board_change"),
        "flags": list(p.get("flags") or []),
        "context": list(p.get("context") or []),
        "backfilled": bool(ts["metrics"].get("backfilled")),
    }


def _ticket_list(picked: List[Dict], pid: str, kind: str) -> List[Dict]:
    out = []
    for ts in picked:
        tickets = ts["metrics"].get("tickets", {})
        for tid in ts["ledger"].get("people.%s.%s" % (pid, kind)) or []:
            t = tickets.get(str(tid)) or {}
            out.append({"id": int(tid), "title": t.get("title") or "",
                        "type": t.get("type") or "", "team": ts["team"],
                        "sprint": ts["metrics"]["sprint"]["name"],
                        "days": (t.get("workers") or {}).get(pid)})
    return out


def build_person_brief(home: Home, cfg: Config, roster: Roster, pid: str,
                       sprints: int = 6, as_of: Optional[date] = None
                       ) -> Dict[str, Any]:
    ids = [p.id for p in roster.people]
    if pid not in ids:
        raise LedgerError("Unknown person %r." % pid,
                          "Use a roster id: %s." % ", ".join(sorted(ids)))
    person = roster.person(pid)
    found = []
    for team in cfg.teams:
        for ts in _team_sprints(home, team.name, as_of):
            if pid in (ts["metrics"].get("people") or {}):
                found.append(ts)
    found.sort(key=lambda ts: (ts["metrics"]["sprint"]["finish"][:10],
                               ts["team"]))
    # N sprints means N sprint periods: a person on two teams has two
    # rows for one period
    def period(ts: Dict) -> Tuple[str, str]:
        s = ts["metrics"]["sprint"]
        return (s["finish"][:10], s["start"][:10])

    keep = set(sorted({period(ts) for ts in found})[-max(1, sprints):])
    picked = [ts for ts in found if period(ts) in keep]
    if not picked:
        raise LedgerError("No analysed sprint includes %r." % pid, _ANALYZE)
    rows = [_person_row(ts, ts["metrics"]["people"][pid]) for ts in picked]
    latest = picked[-1]
    now = []
    for tid, t in sorted(latest["metrics"].get("tickets", {}).items(),
                         key=lambda kv: int(kv[0])):
        if t.get("outcome") in _NOW and t.get("holder_at_end") == pid:
            a = _carry_answer(latest, tid)
            now.append({"id": int(tid), "title": t.get("title") or "",
                        "outcome": t["outcome"],
                        "sprints_carried": t.get("sprints_carried") or 0,
                        "carried_since": t.get("carried_since"),
                        "answer": {k: a.get(k) for k in
                                   ("agreed", "reason", "note")} if a
                        else None})
    flagged: Dict[str, Tuple] = {}
    for ts in picked:
        for tid, t in ts["metrics"].get("tickets", {}).items():
            if pid not in (t.get("workers") or {}) \
                    and t.get("holder_at_end") != pid:
                continue
            reasons = flag_reasons(t, cfg)
            if reasons and on_card(t, pid):
                flagged[tid] = (ts, t, reasons)  # later sprints win
    brief = _base(cfg, picked)
    brief.update({
        "kind": "person",
        "person": {"name": person.display_name, "assignments": [
            {"team": a.team, "from": a.start.isoformat(),
             "to": a.end.isoformat() if a.end else None,
             "allocation": a.allocation} for a in person.assignments]},
        "rows": rows,
        "totals": {
            "delivered_worked_on": sum(r["delivered_worked_on"] for r in rows),
            "handed_off": sum(r["handed_off"] for r in rows),
            "available_days": sum(r["available_days"] for r in rows),
            "nominal_days": sum(r["nominal_days"] for r in rows),
            "days_nothing_in_progress": sum(r["days_nothing_in_progress"]
                                            for r in rows)},
        "flags_by_sprint": [{"team": r["team"], "sprint": r["sprint"],
                             "flags": r["flags"], "context": r["context"]}
                            for r in rows],
        "delivered": _ticket_list(picked, pid, "delivered_worked_on"),
        "handed_off": _ticket_list(picked, pid, "handed_off"),
        "in_progress_now": now,
        "cards": _cards(home, cfg, roster, flagged),
        "context": [{"team": ts["team"],
                     "sprint": ts["metrics"]["sprint"]["name"],
                     "team_time_split": dict(ts["metrics"]["figures"].get(
                         "time_split") or {}),
                     "team_received": dict(ts["metrics"]["figures"].get(
                         "received") or {})} for ts in picked],
    })
    return brief


# ---- team brief ------------------------------------------------------------

def _team_row(ts: Dict) -> Dict[str, Any]:
    m = ts["metrics"]
    f, s = m["figures"], m["sprint"]
    agreed = {"yes": 0, "no": 0, "unanswered": 0}
    for tid, t in m.get("tickets", {}).items():
        if t.get("outcome") in _CARRIED and t.get("held_by_vendor_at_end") \
                and not t.get("outside_teams"):
            a = _carry_answer(ts, tid).get("agreed")
            agreed[a if a in ("yes", "no") else "unanswered"] += 1
    status = m.get("status") or {}
    return {
        "sprint": s["name"], "start": s["start"][:10],
        "finish": s["finish"][:10], "delivered": f["delivered"],
        "squad_delivered": f["squad_delivered"], "planned": f["planned"],
        "awaiting": f["awaiting_acceptance"]["count"],
        "awaiting_longest_days": f["awaiting_acceptance"].get(
            "longest_wait_days"),
        "carried": f["carried"]["in_progress"] + f["carried"]["not_started"],
        "carried_agreed": agreed["yes"], "carried_not_agreed": agreed["no"],
        "carried_unanswered": agreed["unanswered"],
        "with_other_party": f["carried"]["with_other_party"],
        "returns": f["returns"],
        "returns_reason_text": returns_reason_text(ts),
        "median_days_with_vendor": f.get("median_days_with_vendor"),
        "median_elapsed_days": f.get("median_elapsed_days"),
        "time_split": dict(f.get("time_split") or {}),
        "received": dict(f.get("received") or {}),
        "available": f["capacity"]["available"],
        "nominal": f["capacity"]["nominal"],
        "status": {"label": status.get("label"),
                   "rules": list(status.get("rules") or []),
                   "override": None},
        "backfilled": bool(m.get("backfilled")),
    }


def build_team_brief(home: Home, cfg: Config, roster: Roster, team: str,
                     sprints: int = 6, executive: bool = False,
                     as_of: Optional[date] = None) -> Dict[str, Any]:
    names = [t.name for t in cfg.teams]
    if team not in names:
        raise LedgerError("Unknown team %r." % team,
                          "Use a team from config.json: %s."
                          % ", ".join(names))
    picked = _team_sprints(home, team, as_of)[-max(1, sprints):]
    if not picked:
        raise LedgerError("No analysed sprint for %s." % team, _ANALYZE)
    rows = [_team_row(ts) for ts in picked]
    latest = picked[-1]
    found: Dict[str, Tuple] = {}
    for tid, t in latest["metrics"].get("tickets", {}).items():
        reasons = flag_reasons(t, cfg)
        if reasons and not t.get("outside_teams"):
            found[tid] = (latest, t, reasons)
    flagged = [{"id": int(tid), "title": t.get("title") or "",
                "reasons": reasons,
                "sprints_carried": t.get("sprints_carried") or 0,
                "returns": len(t.get("returns") or [])}
               for tid, (_, t, reasons) in sorted(
                   found.items(), key=lambda kv: (
                       -(kv[1][1].get("sprints_carried") or 0),
                       -len(kv[1][1].get("returns") or []), int(kv[0])))]
    open_risks = [{k: r.get(k) for k in ("kind", "title", "text", "ask",
                                         "tickets")}
                  for r in risks.load(home)["risks"]
                  if r.get("status") == "open" and r.get("team") == team]
    brief = _base(cfg, picked)
    brief.update({
        "kind": "team", "team": team, "executive": executive, "rows": rows,
        "trend_delivered": [r["delivered"] for r in rows],
        "flagged": flagged, "risks": open_risks,
        "cards": [] if executive else _cards(home, cfg, roster, found),
    })
    return strip_person_keys(brief) if executive else brief


# ---- command ---------------------------------------------------------------

def _as_of(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got %r" % text)


def _evidence_cmd(args, home: Home) -> Dict[str, Any]:
    if args.executive and args.person:
        raise LedgerError("--executive is only valid with --team.",
                          "Drop --executive, or pass --team NAME instead.")
    if args.sprints < 1:
        raise LedgerError("--sprints must be 1 or more.",
                          "Pass --sprints N with N at least 1.")
    cfg, roster = load_config(home), load_roster(home)
    validate(cfg, roster)
    as_of = args.as_of or _today(cfg)
    if args.person:
        brief = build_person_brief(home, cfg, roster, args.person,
                                   args.sprints, as_of)
        html, subject = render_person_brief(brief), args.person
    else:
        brief = build_team_brief(home, cfg, roster, args.team, args.sprints,
                                 args.executive, as_of)
        html, subject = render_team_brief(brief), args.team
    name = "%s-%s%s.html" % (as_of.isoformat(), slug(subject),
                             "-executive" if args.executive else "")
    if args.executive:
        hits = names_check({name: html}, roster, [])
        if hits:
            access = for_command(args, home, cfg)
            shown = "; ".join('%s: "...%s..."' % (
                h["term"], access.body(h["context"])) for h in hits)
            raise LedgerError(
                "The executive brief would name an engineer: %s" % shown,
                "Fix the ticket text or annotation, or produce the "
                "non-executive brief (drop --executive).")
    path = _write_text(home, html, "evidence", name)
    return {"summary": "Evidence brief written to %s" % path,
            "file": str(path), "subject": subject,
            "sprints": len(brief["rows"])}


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "evidence", help="write an evidence brief for a person or a team")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--person", metavar="ID", help="roster id")
    who.add_argument("--team", metavar="NAME", help="team name")
    p.add_argument("--sprints", type=int, default=6,
                   help="how many recent sprints to include (default 6)")
    p.add_argument("--executive", action="store_true",
                   help="team brief without any person data")
    p.add_argument("--as-of", type=_as_of,
                   help="include sprints finishing on or before YYYY-MM-DD "
                        "(default: today)")
    p.set_defaults(func=_evidence_cmd)
