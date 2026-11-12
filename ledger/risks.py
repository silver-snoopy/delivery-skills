"""Risk candidates and the risk register.

Rule-based detectors turn a report's figures into candidate risks and
issues, each with ticket evidence and one concrete ask to the customer
side. The delivery manager accepts, edits, drops or closes them with the
`risks` command; accepted open risks are attached to every later report
under "Risks and asks" until closed.

Candidate titles, texts and asks come from fixed templates holding counts,
ticket ids, team names, sprint names and parent feature titles only: never
a person's name or id. Roster ids involved are kept in `people`, for the
delivery manager's eyes only (the executive projection drops it, and the
register never stores it). Every detector stays silent on missing data."""
import argparse
import hashlib
from datetime import date, datetime
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo

from .config import Config
from .errors import LedgerError
from .home import Home
from .textaccess import TextAccess, for_command

REGISTER = "risks.json"
STALE_NOTE = "The board no longer shows the evidence for this item."
DETECTORS = ("chronic_carryover", "repeated_returns", "long_wait",
             "unready_work", "midsprint_additions", "load_above_usual",
             "one_vendor_contributor", "fewer_delivered",
             "nothing_in_progress", "low_capacity")
_ISSUES = ("chronic_carryover", "repeated_returns")
_FEWER = "Fewer delivered than usual"


def _num(x: Any) -> Optional[float]:
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) \
        else None


def _days(x: float) -> str:
    return ("%.1f" % (round(x * 2) / 2)).rstrip("0").rstrip(".")


def plural(n: float, noun: str) -> str:
    """'1 ticket', '2 tickets', '1.5 working days' (days to the nearest 0.5)."""
    shown = _days(n) if isinstance(n, float) else str(n)
    return "%s %s%s" % (shown, noun, "" if shown == "1" else "s")


def _candidate(detector: str, team: str, tickets: List[str], key: str,
               figures: Dict, title: str, text: str, ask: str,
               people: List[str]) -> Dict[str, Any]:
    """One candidate. The id is sha256 of `detector|team|basis`, first 12
    hex; basis is `key` when given, else the ticket ids joined by "," in
    numeric-like order (shorter ids first, then by value)."""
    tickets = sorted(tickets, key=lambda t: (len(t), t))
    basis = ",".join(tickets) if key is None else key
    cid = hashlib.sha256(("%s|%s|%s" % (detector, team, basis))
                         .encode("utf-8")).hexdigest()[:12]
    return {"id": cid, "detector": detector,
            "kind": "Issue" if detector in _ISSUES else "Risk",
            "team": team, "title": title, "text": text, "ask": ask,
            "tickets": tickets, "figures": figures,
            "people": sorted(people)}


def _mine(m: Dict) -> Iterator:
    for tid, t in sorted((m.get("tickets") or {}).items()):
        if isinstance(t, dict) and not t.get("outside_teams"):
            yield str(tid), t


def _team_candidates(team: str, m: Dict, th: Dict) -> List[Dict]:
    out: List[Dict] = []
    sprint = (m.get("sprint") or {}).get("name") or "the sprint"
    figs = m.get("figures") or {}

    for tid, t in _mine(m):
        workers = sorted(t.get("workers") or {})
        n = _num(t.get("sprints_carried"))
        if (t.get("outcome") == "carried_in_progress"
                and t.get("held_by_vendor_at_end")
                and n is not None and n >= th["carry_flag_sprints"]):
            out.append(_candidate(
                "chronic_carryover", team, [tid], None,
                {"sprints_carried": n},
                "#%s carried over in %s" % (tid, plural(int(n), "sprint")),
                "%s: #%s was still in progress at the end of %s and has "
                "been carried over in %s."
                % (team, tid, sprint, plural(int(n), "sprint")),
                "Agree what is blocking #%s and who decides, before the "
                "next sprint starts." % tid, workers))
        rets = t.get("returns")
        if isinstance(rets, list) and len(rets) >= th["returns_flag"]:
            changes = _num((t.get("received") or {})
                           .get("text_changes_after"))
            text = "%s: #%s has come back %s after being handed off." % (
                team, tid, plural(len(rets), "time"))
            if changes is not None and changes > 0:
                text += (" Its text was edited %s after the work was "
                         "received." % plural(int(changes), "time"))
            elif changes is not None:
                text += (" Its text was not edited after the work was "
                         "received.")
            out.append(_candidate(
                "repeated_returns", team, [tid], None,
                {"returns": len(rets), "text_changes_after": changes},
                "#%s returned %s" % (tid, plural(len(rets), "time")),
                text,
                "Confirm the acceptance criteria for #%s with the reviewer "
                "before it is handed off again." % tid, workers))
        dw, sw = _num(t.get("days_waiting")), _num(t.get("sprints_waiting"))
        if t.get("outcome") == "awaiting_acceptance" and (
                (dw is not None and dw > th["long_wait_days"])
                or (sw is not None and sw >= th["long_wait_sprint_ends"])):
            # the wait as the board names it (a configured label), never
            # more than the board recorded
            label = (t.get("wait_label") or "").strip()
            wait = label or "acceptance"
            out.append(_candidate(
                "long_wait", team, [tid], None,
                {"days_waiting": dw, "sprints_waiting": sw},
                "#%s waiting for %s" % (tid, wait),
                "%s: #%s was finished and has waited for %s%s."
                % (team, tid, wait, ",".join(part for part in (
                    " for %s" % plural(float(dw), "working day")
                    if dw is not None else "",
                    " across %s" % plural(int(sw), "sprint end")
                    if sw is not None and sw >= 1 else "") if part)),
                ("Name who completes the %s of #%s and by when."
                 % (label, tid)) if label
                else "Name who accepts #%s and by when." % tid, workers))

    rec = figs.get("received") or {}
    got, nc = _num(rec.get("received")), _num(rec.get("no_criteria"))
    if got and nc is not None and nc >= th["unready_min"] \
            and nc / got >= th["unready_share"]:
        tids = [tid for tid, t in _mine(m)
                if (t.get("received") or {}).get("no_criteria")]
        out.append(_candidate(
            "unready_work", team, tids, "-",
            {"no_criteria": nc, "received": got},
            "Work received without acceptance criteria",
            "%s: %d of %s received in %s had no acceptance "
            "criteria and a short description."
            % (team, nc, plural(int(got), "ticket"), sprint),
            "Agree that tickets enter %s's sprints only with acceptance "
            "criteria written." % team, []))
    mid = _num(rec.get("added_mid_sprint"))
    if mid is not None and mid >= th["midsprint_min"]:
        tids = [tid for tid, t in _mine(m)
                if (t.get("received") or {}).get("added_mid_sprint")]
        out.append(_candidate(
            "midsprint_additions", team, tids, "-",
            {"added_mid_sprint": mid},
            "Tickets added after the sprint started",
            "%s: %s added to %s after it started."
            % (team, plural(int(mid), "ticket") +
               (" was" if int(mid) == 1 else " were"), sprint),
            "Agree with the product owner how work is added to %s once a "
            "sprint has started." % team, []))

    load = figs.get("load") or {}
    if load.get("above_usual") and _num(load.get("at_start")) is not None \
            and _num(load.get("median_delivered")) is not None:
        tids = [tid for tid, t in _mine(m) if t.get("in_sprint_at_start")]
        out.append(_candidate(
            "load_above_usual", team, tids, "-",
            {"at_start": load["at_start"],
             "median_delivered": load["median_delivered"],
             "sprints": load["sprints"]},
            "Sprint started with more work than the team usually delivers",
            "%s: %s with vendor engineers at the start of %s; the team "
            "delivered a median of %s in its previous %s."
            % (team, plural(int(load["at_start"]), "ticket") +
               (" was" if int(load["at_start"]) == 1 else " were"), sprint,
               _days(float(load["median_delivered"])),
               plural(int(load["sprints"]), "sprint")),
            "Agree the size of %s's next sprint with the product owner "
            "against what the team has delivered." % team, []))

    features: Dict[str, List] = {}
    for tid, t in _mine(m):
        if (t.get("parent_title") or "").strip() and t.get("workers"):
            features.setdefault(t["parent_title"], []).append(
                (tid, set(t["workers"])))
    for feature, rows in sorted(features.items()):
        who = set().union(*(w for _, w in rows))
        if len(rows) >= th["one_contributor_min_tickets"] and len(who) == 1:
            tids = [tid for tid, _ in rows]
            out.append(_candidate(
                "one_vendor_contributor", team, tids, feature,
                {"feature": feature, "tickets": len(tids)},
                "One recorded vendor contributor on '%s' in %s"
                % (feature, sprint),
                "In %s, the %d tickets under '%s' that had recorded vendor "
                "work were all held by the same engineer."
                % (sprint, len(tids), feature),
                "Consider whether a second person should take part in this "
                "feature next sprint.", sorted(who)))

    rules = (m.get("status") or {}).get("rules") or []
    if _FEWER in rules:
        out.append(_candidate(
            "fewer_delivered", team, [], "-", {},
            "Fewer delivered than usual",
            "%s: fewer tickets were delivered in %s than in the team's "
            "recent sprints." % (team, sprint),
            "Review the scope of %s's next sprint with the product owner "
            "against what was delivered in %s." % (team, sprint), []))

    idle = sorted(pid for pid, p in (m.get("people") or {}).items()
                  if isinstance(p, dict)
                  and _num(p.get("days_nothing_in_progress")) is not None
                  and p["days_nothing_in_progress"]
                  >= th["nothing_in_progress_days"])
    if idle:
        out.append(_candidate(
            "nothing_in_progress", team, [], "-", {"people": len(idle)},
            "Engineers with no ticket in progress",
            "%s: %s had %s or more in %s with no ticket in progress."
            % (team, plural(len(idle), "engineer"),
               plural(float(th["nothing_in_progress_days"]), "working day"),
               sprint),
            "Review the board's ready queue for %s before the next sprint."
            % team, idle))

    cap = figs.get("capacity") or {}
    av, nom = _num(cap.get("available")), _num(cap.get("nominal"))
    if av is not None and nom and nom > 0 and av / nom < th["capacity_share"]:
        out.append(_candidate(
            "low_capacity", team, [], "-",
            {"available": av, "nominal": nom},
            "Lower capacity than nominal",
            "%s: %s of %s nominal engineer days were available in %s."
            % (team, _days(av), _days(nom), sprint),
            "Ask the product owner whether the scope of %s's next sprint "
            "should be reduced to match the available days." % team, []))
    return out


def detect(report: Dict, cfg: Config) -> List[Dict]:
    """Candidates for every team in every date group of the full report.

    `one_vendor_contributor` looks at one sprint's tickets only: a
    single-sprint approximation of the design's rule over the window."""
    th = cfg.thresholds
    out: List[Dict] = []
    for g in report.get("groups") or []:
        for team in g.get("teams") or []:
            m = team.get("metrics")
            if isinstance(m, dict):
                out += _team_candidates(team["name"], m, th)
    order = {d: i for i, d in enumerate(DETECTORS)}
    out.sort(key=lambda c: (c["team"], order[c["detector"]], c["id"]))
    return out


# ---- register --------------------------------------------------------------

def _bad(why: str) -> LedgerError:
    return LedgerError("%s in the data home is not a valid risk register: "
                       "%s." % (REGISTER, why),
                       "Fix or remove %s; it holds {\"risks\": [...], "
                       "\"dropped\": [...]} and each risk needs an \"id\"."
                       % REGISTER)


def _read(home: Home) -> Optional[Dict[str, Any]]:
    reg = home.read_json(REGISTER, default=None)
    if reg is None:
        return None
    if not isinstance(reg, dict):
        raise _bad("the top level is not an object")
    if not isinstance(reg.get("risks", []), list) or \
            not isinstance(reg.get("dropped", []), list):
        raise _bad("\"risks\" and \"dropped\" must be lists")
    for r in reg.get("risks", []):
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            raise _bad("a risk entry has no \"id\"")
    return reg


def load(home: Home) -> Dict[str, Any]:
    reg = _read(home) or {}
    reg.setdefault("risks", [])
    reg.setdefault("dropped", [])
    return reg


def _save(home: Home, reg: Dict) -> None:
    home.write_json(reg, REGISTER)


def _sort_key(r: Dict):
    return (0 if r.get("kind") == "Issue" else 1, r.get("team") or "",
            r.get("id") or "")


def attach(home: Home, cfg: Config, report: Dict) -> List[Dict]:
    """Set report["risks"] to the register's open risks; return candidates.

    Each open risk carries `evidence_ok`: whether its id is still among
    the current candidates. The register is rewritten only on a change."""
    cands = detect(report, cfg)
    by_id = {c["id"]: c for c in cands}
    reg = _read(home)
    risks: List[Dict] = []
    if reg:
        changed = False
        for r in reg.get("risks", []):
            if r.get("status") != "open":
                continue
            ok = r["id"] in by_id
            if r.get("evidence_ok") != ok:
                r["evidence_ok"] = ok
                changed = True
            item = dict(r)
            item["people"] = by_id[r["id"]]["people"] if ok else []
            risks.append(item)
        if changed:
            _save(home, reg)
    report["risks"] = sorted(risks, key=_sort_key)
    return cands


def stale(report: Dict) -> List[str]:
    return [r["id"] for r in report.get("risks") or []
            if isinstance(r, dict) and r.get("evidence_ok") is False]


# ---- command ---------------------------------------------------------------

def _public(c: Dict) -> Dict:
    return {k: c[k] for k in ("id", "detector", "kind", "team", "title",
                              "text", "ask", "tickets")}


_FEATURE = "one_vendor_contributor"


def _shown(r: Dict, access: TextAccess, feature: Optional[str] = None
           ) -> Dict:
    """A candidate or register entry as a command may print it. Template
    texts hold counts, ticket ids, team and sprint names only, except the
    feature title of one detector; a text the delivery manager edited may
    hold anything."""
    out = dict(r)
    if r.get("edited") and not access.full:
        for k in ("title", "text", "ask"):
            out[k] = access.body(r.get(k))
    elif r.get("detector") == _FEATURE and not access.titles:
        for k in ("title", "text"):
            out[k] = (r[k].replace("'%s'" % feature,
                                   "'%s'" % access.title(feature))
                      if feature and "'%s'" % feature in (r.get(k) or "")
                      else access.title(r.get(k)))
    return out


def _line(r: Dict) -> str:
    return "  %s  %s  %s: %s" % (r["id"], r["kind"], r["team"], r["title"])


def _today(cfg: Config) -> date:
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def _risks_cmd(args, home: Home) -> Dict[str, Any]:
    from .config import load_config, load_roster, validate
    from .report import build_report
    cfg, roster = load_config(home), load_roster(home)
    validate(cfg, roster)
    as_of = args.as_of or _today(cfg)
    reg = load(home)
    access = for_command(args, home, cfg)
    if args.list:
        listed = [_shown(r, access) for r in reg["risks"]]
        lines = ["Risk register:"] + [
            "%s  [%s]" % (_line(r), r["status"]) for r in listed]
        if not listed:
            lines.append("  (empty)")
        return {"summary": "\n".join(lines), "risks": listed}
    if args.close:
        entry = _entry(reg, args.close)
        if entry is None:
            raise LedgerError("No risk %r in the register." % args.close,
                              "Run `ledger risks --list` to see the ids.")
        entry.update(status="closed", closed=_today(cfg).isoformat())
        _save(home, reg)
        return {"summary": "Closed %s." % args.close, "id": args.close}

    report = build_report(home, cfg, roster, as_of)
    reg = load(home)  # build_report may have refreshed evidence_ok
    cands = {c["id"]: c for c in detect(report, cfg)}
    if args.candidates:
        known = {r["id"] for r in reg["risks"]} | set(reg["dropped"])
        new = [_shown(_public(c), access,
                      (c.get("figures") or {}).get("feature"))
               for c in cands.values() if c["id"] not in known]
        opened = [_shown(r, access) for r in reg["risks"]
                  if r.get("status") == "open"]
        lines = ["Risk candidates for %s:" % as_of.isoformat()]
        lines += [_line(c) for c in new] or ["  (none)"]
        lines.append("Open in the register:")
        lines += ["%s%s" % (_line(r), "" if r.get("evidence_ok", True)
                            else "  (evidence no longer shown)")
                  for r in opened] or ["  (none)"]
        return {"summary": "\n".join(lines), "candidates": new,
                "open": opened}
    if args.drop:
        if args.drop not in cands and _entry(reg, args.drop) is None:
            raise LedgerError("No candidate %r for %s."
                              % (args.drop, as_of.isoformat()),
                              "Run `ledger risks --candidates` first.")
        if args.drop not in reg["dropped"]:
            reg["dropped"].append(args.drop)
        _save(home, reg)
        return {"summary": "Dropped %s; it will not be offered again."
                % args.drop, "id": args.drop}
    # accept
    cid = args.accept
    entry, cand = _entry(reg, cid), cands.get(cid)
    if entry is None and cand is None:
        raise LedgerError("No candidate or register entry %r for %s."
                          % (cid, as_of.isoformat()),
                          "Run `ledger risks --candidates` to see the ids.")
    if entry is None:
        entry = dict(_public(cand), status="open",
                     opened=as_of.isoformat(), closed=None,
                     evidence_ok=True, edited=False)
        reg["risks"].append(entry)
    entry.update(status="open", closed=None, evidence_ok=cand is not None)
    for field in ("title", "text", "ask"):
        value = getattr(args, field)
        if value is not None and value.strip():
            entry[field] = value.strip()
            entry["edited"] = True
    if cid in reg["dropped"]:
        reg["dropped"].remove(cid)
    _save(home, reg)
    shown = _shown(entry, access)
    return {"summary": "Accepted %s: %s" % (cid, shown["title"]),
            "risk": shown}


def _entry(reg: Dict, cid: str) -> Optional[Dict]:
    for r in reg["risks"]:
        if r["id"] == cid:
            return r
    return None


def _as_of(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got %r" % text)


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "risks", help="review risk candidates and the risk register")
    p.add_argument("--as-of", type=_as_of,
                   help="report date YYYY-MM-DD (default: today)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--candidates", action="store_true",
                   help="list new candidates and the open risks")
    g.add_argument("--accept", metavar="ID", help="accept or reopen a risk")
    g.add_argument("--drop", metavar="ID",
                   help="drop a candidate so it is not offered again")
    g.add_argument("--close", metavar="ID", help="close an open risk")
    g.add_argument("--list", action="store_true", help="show the register")
    p.add_argument("--title", help="with --accept: replace the title")
    p.add_argument("--text", help="with --accept: replace the text")
    p.add_argument("--ask", help="with --accept: replace the ask")
    p.set_defaults(func=_risks_cmd)
