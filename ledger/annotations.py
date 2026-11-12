"""DM annotations: the delivery manager's answers about carryovers and returns.

Annotations are shown beside figures; they never change a computed figure,
flag or status. The store is append-only: a new answer is a new version.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .errors import LedgerError
from .home import Home, slug
from .textaccess import TextAccess, for_command

REASONS = ("dependency", "waiting on customer", "requirement changed",
           "unplanned work took priority", "planned leave",
           "larger than expected", "other")
RETURN_REASONS = ("defect", "requirement changed", "other")
AGREED = ("yes", "no", "unknown")
CARRY_OUTCOMES = ("carried_in_progress", "carried_not_started")
FILE = "annotations.json"
TITLE_WIDTH = 70


# ---- store -----------------------------------------------------------------

def load(home: Home, team: str, sprint_id: str) -> List[Dict]:
    d = home.team_sprint_dir(team, sprint_id)
    return home.read_json(*d.relative_to(home.root).parts, FILE, default=[]) or []


def current(entries: List[Dict]) -> Dict[str, Dict]:
    """Latest version per key."""
    out: Dict[str, Dict] = {}
    for e in entries:
        if e["key"] not in out or e["version"] >= out[e["key"]]["version"]:
            out[e["key"]] = e
    return out


def _items(metrics: Dict) -> List[Dict]:
    sprint = metrics["sprint"]
    start = date.fromisoformat(sprint["start"][:10])
    end = date.fromisoformat(sprint["finish"][:10]) + timedelta(days=1)
    carries, returns = [], []
    for tid, t in metrics["tickets"].items():
        ticket = int(tid)
        if t["outcome"] in CARRY_OUTCOMES:
            carries.append({"key": "carry:%d:%s" % (ticket, sprint["id"]),
                            "ticket": ticket, "title": t.get("title", ""),
                            "kind": "carry",
                            "sprints_carried": t["sprints_carried"]})
        for r in t.get("returns", []):
            at = datetime.fromisoformat(r["at"])
            day = (at.astimezone(timezone.utc) if at.tzinfo else at).date()
            # metrics mark the returns of the sprint's own period; sprints
            # analysed before that mark fall back to the sprint's dates
            if r["in_sprint"] if "in_sprint" in r else start <= day < end:
                returns.append({"key": "return:%d:%s" % (ticket, r["rev"]),
                                "ticket": ticket, "title": t.get("title", ""),
                                "kind": "return", "sprints_carried": None,
                                "_rev": r["rev"]})
    carries.sort(key=lambda i: (-i["sprints_carried"], i["ticket"]))
    returns.sort(key=lambda i: (i["ticket"], i["_rev"]))
    for i in returns:
        del i["_rev"]
    return carries + returns


def pending(metrics: Dict, entries: List[Dict]) -> List[Dict]:
    """Unanswered items. A carried ticket another party holds at the
    sprint's end is not asked about: it is not in the carried-over table."""
    done = current(entries)
    tickets = metrics["tickets"]
    return [i for i in _items(metrics) if i["key"] not in done
            and (i["kind"] != "carry" or tickets[str(i["ticket"])].get(
                "held_by_vendor_at_end", True))]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _metrics(home: Home, team: str, sprint_id: str) -> Dict:
    d = home.team_sprint_dir(team, sprint_id)
    m = home.read_json(*d.relative_to(home.root).parts, "metrics.json")
    if m is None:
        raise LedgerError(
            "No metrics for team %s sprint %s." % (team, sprint_id),
            "Run `ledger analyze --team %s --sprint %s` first." % (team, sprint_id))
    return m


def require_not_issued(home: Home, team: str, sprint_id: str,
                       what: str) -> None:
    """Refuse a write into a sprint an issued edition points to."""
    d = home.team_sprint_dir(team, sprint_id)
    issued = home.read_json(*d.relative_to(home.root).parts, "issued.json")
    if issued is None:
        return
    edition = issued.get("edition") if isinstance(issued, dict) else None
    raise LedgerError(
        "Sprint %s of %s has been issued%s; %s cannot be changed on an "
        "issued sprint." % (
            sprint_id, team,
            " in edition %03d" % int(edition) if edition is not None else "",
            what),
        "Run `ledger reopen --team \"%s\" --sprint %s --note \"WHAT AND "
        "WHY\"` first. It keeps the issued copy and marks the next edition "
        "as corrected." % (team, sprint_id))


def record(home: Home, team: str, sprint_id: str, key: str,
           agreed: Optional[str] = None, reason: Optional[str] = None,
           note: str = "", now: Optional[str] = None) -> Dict:
    require_not_issued(home, team, sprint_id, "an answer")
    metrics = _metrics(home, team, sprint_id)
    item = next((i for i in _items(metrics) if i["key"] == key), None)
    if item is None:
        raise LedgerError("Unknown annotation key %s." % key,
                          "Run `ledger annotate --list` to see valid keys.")
    kind = item["kind"]
    if kind == "carry":
        if agreed is not None and agreed not in AGREED:
            raise LedgerError("Invalid agreed value %r." % agreed,
                              "Use one of: %s." % ", ".join(AGREED))
        allowed = REASONS
    else:
        if agreed is not None:
            raise LedgerError("`agreed` applies only to carryovers.",
                              "Omit --agreed for a return.")
        allowed = RETURN_REASONS
    if reason is not None and reason not in allowed:
        raise LedgerError("Invalid reason %r for a %s." % (reason, kind),
                          "Use one of: %s." % ", ".join(allowed))
    entries = load(home, team, sprint_id)
    version = 1 + max([e["version"] for e in entries if e["key"] == key] or [0])
    entry = {"key": key, "kind": kind, "ticket": item["ticket"],
             "agreed": agreed, "reason": reason, "note": (note or "").strip(),
             "entered": now or _now(), "version": version}
    d = home.team_sprint_dir(team, sprint_id)
    home.write_json(entries + [entry], *d.relative_to(home.root).parts, FILE)
    return entry


def carried_forward(home: Home, team: str, ticket: int) -> List[Dict]:
    """Current carry answers for this ticket from the team's sprints."""
    root = home.path("teams", slug(team), "sprints")
    found: List[Dict] = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                rel = d.relative_to(home.root).parts
                for e in current(home.read_json(*rel, FILE, default=[]) or []).values():
                    if e["kind"] == "carry" and e["ticket"] == ticket:
                        found.append(e)
    return sorted(found, key=lambda e: e["entered"])


# ---- command ---------------------------------------------------------------

def _clip(text: str) -> str:
    return text if len(text) <= TITLE_WIDTH else text[:TITLE_WIDTH - 1] + "…"


def _line(item: Dict) -> str:
    s = "%s  #%d  %s" % (item["key"], item["ticket"], _clip(item["title"]))
    if item["kind"] == "carry":
        s += "  (carried %d sprint%s)" % (
            item["sprints_carried"], "" if item["sprints_carried"] == 1 else "s")
    return s


def _earlier(home: Home, team: str, sprint_id: str, item: Dict,
             access: TextAccess) -> List[str]:
    own = "carry:%d:%s" % (item["ticket"], sprint_id)
    out = []
    for e in carried_forward(home, team, item["ticket"]):
        if e["key"] == own:
            continue
        out.append("    earlier (%s): agreed=%s reason=%s note=%s" % (
            e["key"].split(":")[2], e["agreed"] or "-", e["reason"] or "-",
            access.body(e["note"]) or "-"))
    return out


def _shown(items: List[Dict], access: TextAccess) -> List[Dict]:
    return [dict(i, title=access.title(i["title"])) for i in items]


def _list(home: Home, team: str, sprint_id: str,
          access: TextAccess) -> Dict[str, Any]:
    items = _shown(pending(_metrics(home, team, sprint_id),
                           load(home, team, sprint_id)), access)
    reasons = {"carry": list(REASONS), "return": list(RETURN_REASONS)}
    if not items:
        return {"summary": "Nothing pending.", "pending": [],
                "reasons": reasons}
    lines = []
    for i in items:
        lines.append(_line(i))
        if i["kind"] == "carry":
            lines.extend(_earlier(home, team, sprint_id, i, access))
    return {"summary": "\n".join(lines), "pending": items,
            "reasons": reasons}


def _ask(prompt: str) -> Optional[str]:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.readline()
    return None if line == "" else line.strip()


def _choose(prompt: str, valid, convert):
    """Ask twice at most; None for end of input, False for invalid twice."""
    for _ in range(2):
        a = _ask(prompt)
        if a is None:
            return None
        ok, value = convert(a)
        if ok:
            return (value,)
        sys.stdout.write("Not understood.\n")
    return (False,)


def _agreed(a: str):
    m = {"y": "yes", "n": "no", "u": "unknown", "": "unknown"}
    return (a.lower() in m, m.get(a.lower()))


def _number(options):
    def convert(a: str):
        if a == "":
            return True, None
        if a.isdigit() and 1 <= int(a) <= len(options):
            return True, options[int(a) - 1]
        return False, None
    return convert


def _interactive(home: Home, team: str, sprint_id: str,
                 access: TextAccess) -> Dict[str, Any]:
    require_not_issued(home, team, sprint_id, "an answer")
    items = _shown(pending(_metrics(home, team, sprint_id),
                           load(home, team, sprint_id)), access)
    recorded = 0
    for item in items:
        sys.stdout.write("\n#%d  %s\n" % (item["ticket"], item["title"]))
        agreed = None
        if item["kind"] == "carry":
            r = _choose("Agreed with the PO and communicated in advance? [y/n/u] ",
                        None, _agreed)
            if r is None:
                break
            agreed = "unknown" if r[0] is False else r[0]
            options = REASONS
        else:
            options = RETURN_REASONS
        for n, name in enumerate(options, 1):
            sys.stdout.write("  %d. %s\n" % (n, name))
        r = _choose("Reason number (empty for none): ", None, _number(options))
        if r is None:
            break
        reason = None if r[0] is False else r[0]
        note = _ask("Note (optional): ")
        if note is None:
            break
        record(home, team, sprint_id, item["key"], agreed=agreed,
               reason=reason, note=note)
        recorded += 1
    remaining = len(items) - recorded
    return {"summary": "Recorded %d annotation%s, %d remaining." % (
        recorded, "" if recorded == 1 else "s", remaining),
        "recorded": recorded, "remaining": remaining}


def _annotate_cmd(args, home: Home) -> Dict[str, Any]:
    access = for_command(args, home)
    if args.list:
        return _list(home, args.team, args.sprint, access)
    if args.interactive:
        return _interactive(home, args.team, args.sprint, access)
    e = record(home, args.team, args.sprint, args.set, agreed=args.agreed,
               reason=args.reason, note=args.note or "")
    return {"summary": "Recorded %s (version %d)." % (e["key"], e["version"]),
            "entry": dict(e, note=access.body(e["note"]))}


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "annotate", help="record the DM's answers about carryovers and returns")
    p.add_argument("--team", required=True)
    p.add_argument("--sprint", required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="show pending items")
    mode.add_argument("--set", metavar="KEY", help="answer one item")
    mode.add_argument("--interactive", action="store_true",
                      help="be asked about each pending item")
    p.add_argument("--agreed", choices=AGREED)
    p.add_argument("--reason")
    p.add_argument("--note")
    p.set_defaults(func=_annotate_cmd)
