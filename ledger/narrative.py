"""Report narrative: the no-agent fallback (ticket groupings by parent and
a summary built from figures only), the input an agent may see, validation
of what the agent wrote, and the narrative-input / narrative-validate
commands."""
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .config import Roster
from .errors import LedgerError
from .names_check import _nfc, name_hits

BUCKETS = {
    "delivered": ("delivered",),
    "awaiting": ("awaiting_acceptance",),
    "not_delivered": ("carried_in_progress", "carried_not_started",
                      "active_outside_sprint"),
}
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")


def _short(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return "%d %s" % (d.day, _MONTHS[d.month - 1])


def fallback(metrics: Dict) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    for bucket, outcomes in BUCKETS.items():
        groups: Dict[str, List[int]] = {}
        for tid, t in metrics.get("tickets", {}).items():
            if t.get("involvement") != "worked" or t.get("outside_teams") \
                    or t.get("outcome") not in outcomes:
                continue
            groups.setdefault(t.get("parent_title") or "Other",
                              []).append(int(tid))
        out[bucket] = [{"heading": h, "text": "", "tickets": sorted(ids)}
                       for h, ids in sorted(groups.items(),
                                            key=lambda x: (-len(x[1]), x[0]))]
    return out


def _attention(team: Dict) -> str:
    st = team.get("status") or {}
    ov = st.get("override")
    label = ov["label"] if ov else st.get("label")
    if not label or label == "On track":
        return ""
    why = ("set by the delivery manager: %s" % ov["reason"] if ov
           else ", ".join(st.get("rules") or []))
    return " %s: %s (%s)." % (label, team["name"], why)


def fallback_summary(report: Dict) -> str:
    parts = []
    for g in report.get("groups", []):
        t = g["totals"]
        n = t["teams"]
        delivered = ("%d of %d squad items" % (t["delivered"],
                                               t["squad_delivered"])
                     if t["squad_delivered"] else "%d" % t["delivered"])
        s = ("Sprint ending %s: %s delivered by the %s across %d team%s; "
             "%d finished and awaiting acceptance; %d carried over "
             "(%d agreed in advance, %d not, %d not yet answered)." % (
                 _short(g["finish"]), delivered, report["vendor_label"], n,
                 "" if n == 1 else "s", t["awaiting"], t["carried"],
                 t["carried_agreed"], t["carried_not_agreed"],
                 t["carried_unanswered"]))
        s += "".join(_attention(team) for team in g["teams"])
        parts.append(s)
    return " ".join(parts)


# ---- agent input -----------------------------------------------------------

CLAIM_TERMS = ("live", "in production", "deployed", "released", "rolled out",
               "accepted", "signed off", "as planned", "because", "due to",
               "caused by")
NARRATIVE_FILE = "narrative.json"
SUMMARY_FILE = "summary.json"
DESCRIPTION_MAX = 1500
HEADING_MAX = 60
TEXT_MAX = 280
SUMMARY_MAX = 600
_MAX_SHOWN = 20
_CLAIM_RX = re.compile(r"(?<!\w)(%s)(?!\w)" % "|".join(
    r"\s+".join(re.escape(w) for w in t.split()) for t in CLAIM_TERMS),
    re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?]) +|[\r\n]+")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November",
                "December")
# "<day> <Mon>" or "<day> <Month>", optionally with a four-digit year
_DATES = re.compile(r"(?<!\w)(?<!\d\.)\d{1,2}\s+(?:%s)\b"
                    r"(?:\s+\d{4}(?!\w)(?!\.\d))?"
                    % "|".join(_MONTH_NAMES + _MONTHS), re.IGNORECASE)
_ISO_DATES = re.compile(r"(?<!\w)(?<!\d\.)\d{4}-\d{2}-\d{2}(?!\w)(?!\.\d)")
_YEAR = re.compile(r"(?<!\w)(?<!\d\.)\d{4}(?!\w)(?!\.\d)")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_NUMBER_WORDS = re.compile(r"(?<!\w)(%s)(?!\w)" % "|".join((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "dozen", "half", "third", "quarter", "double", "doubled",
    "triple", "tripled", "twice", "percent", r"per\s+cent", "percentage")),
    re.IGNORECASE)
CONFIRM_IN_FILE = ("confirmed sentences must be given with --confirm by the "
                   "delivery manager")


def bucket_ids(metrics: Dict) -> Dict[str, List[int]]:
    """The ticket ids of each bucket, exactly as fallback() lists them."""
    return {b: sorted(i for x in entries for i in x["tickets"])
            for b, entries in fallback(metrics).items()}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def agent_input(metrics: Dict, snap: Any, access: str) -> Dict[str, Any]:
    """What an agent may see for one team sprint. Ticket text comes from
    the snapshot; no person, worker, holder, annotation or roster field
    is ever included. At "none" only ids and types."""
    text = {t.id: t for t in list(snap.squad) + list(snap.tickets)}
    tickets = metrics.get("tickets", {})
    out: Dict[str, Any] = {
        "team": metrics.get("team"),
        "sprint": {k: metrics["sprint"][k]
                   for k in ("name", "start", "finish")},
    }
    ids = bucket_ids(metrics)
    for bucket, members in ids.items():
        rows = []
        for tid in members:
            row: Dict[str, Any] = {
                "id": tid, "type": tickets.get(str(tid), {}).get("type", "")}
            t = text.get(tid)
            if access in ("titles", "full"):
                row["title"] = t.title if t else ""
                row["parent_title"] = t.parent_title if t else ""
            if access == "full":
                row["description"] = _clip(t.description if t else "",
                                           DESCRIPTION_MAX)
            rows.append(row)
        out[bucket] = rows
    out["figures"] = {b: len(v) for b, v in ids.items()}
    return out


# ---- validation ------------------------------------------------------------

def _norm_sentence(text: str) -> str:
    return " ".join(_nfc(text).split()).rstrip(".!?").strip()


def _claims(text: str):
    """(sentence, claim match) for each sentence with a claim term."""
    out = []
    for sentence in _SENTENCE_SPLIT.split(_nfc(text)):
        m = _CLAIM_RX.search(sentence)
        if m:
            out.append((sentence, m))
    return out


def needed_confirmations(texts: List[str], confirmed: List[str]
                         ) -> List[str]:
    """The confirmed sentences that some claim sentence in texts uses."""
    used = {_norm_sentence(s) for t in texts for s, _ in _claims(t)}
    return [c for c in confirmed if _norm_sentence(c) in used]


def _text_problems(text: str, roster: Roster, confirmed: List[str]
                   ) -> List[str]:
    """Names, markup and unconfirmed claims in one piece of agent text."""
    out = []
    plain = _nfc(text)
    if any(unicodedata.category(c) == "Cf" or (
            unicodedata.category(c) == "Cc" and c not in "\n\t")
           for c in plain):
        out.append("contains invisible or control characters")
    for term in name_hits(plain, roster):
        out.append("names %r; the report never names an engineer" % term)
    if any(c in plain for c in ("<", ">")) or "](" in plain:
        out.append("contains markup; write plain sentences only")
    ok = {_norm_sentence(c) for c in confirmed}
    for sentence, m in _claims(plain):
        if _norm_sentence(sentence) not in ok:
            out.append("sentence %r says %r, which needs the delivery "
                       "manager's confirmation" % (sentence.strip(),
                                                   m.group(1).lower()))
    return out


def validate(narrative: Dict, metrics: Dict, roster: Roster,
             confirmed: List[str]) -> List[str]:
    """Problems with the narrative entry for metrics["team"]; empty when
    it may be used in the report."""
    team = metrics.get("team")
    problems: List[str] = []
    if not isinstance(narrative, dict):
        return ["the narrative is not a JSON object"]
    for k in narrative:
        if k not in ("teams", "confirmed"):
            problems.append("unknown top-level key %r" % k)
    entry = (narrative.get("teams") or {}).get(team)
    if not isinstance(entry, dict):
        return problems + ["no entry for team %r under \"teams\"" % team]
    for k in entry:
        if k not in BUCKETS:
            problems.append("unknown bucket %r" % k)
    for bucket, members in bucket_ids(metrics).items():
        allowed = set(members)
        cited: Set[int] = set()
        entries = entry.get(bucket, [])
        if not isinstance(entries, list):
            problems.append("%s: not a list" % bucket)
            continue
        for x in entries:
            if not isinstance(x, dict):
                problems.append("%s: an entry is not an object" % bucket)
                continue
            heading = x.get("heading")
            where = "%s, %r" % (bucket, heading)
            for k in x:
                if k not in ("heading", "text", "tickets"):
                    problems.append("%s: unknown key %r" % (where, k))
            text = x.get("text")
            if not isinstance(heading, str) or not heading.strip():
                problems.append("%s: heading is missing" % where)
                heading = ""
            if not isinstance(text, str) or not text.strip():
                problems.append("%s: text is missing" % where)
                text = ""
            if len(heading) > HEADING_MAX:
                problems.append("%s: heading is longer than %d characters"
                                % (where, HEADING_MAX))
            if len(text) > TEXT_MAX:
                problems.append("%s: text is longer than %d characters"
                                % (where, TEXT_MAX))
            for piece in (heading, text):
                problems += ["%s: %s" % (where, p)
                             for p in _text_problems(piece, roster, confirmed)]
            ids = x.get("tickets")
            if not isinstance(ids, list) or not ids:
                problems.append("%s: no tickets cited" % where)
                continue
            for tid in ids:
                try:
                    n = int(tid)
                except (TypeError, ValueError):
                    problems.append("%s: %r is not a ticket id" % (where, tid))
                    continue
                if n not in allowed:
                    problems.append("%s: ticket %d is not in %s for this "
                                    "sprint" % (where, n, bucket))
                cited.add(n)
        for n in sorted(allowed - cited):
            problems.append("%s: ticket %d is not covered by any entry"
                            % (bucket, n))
    return problems


def _allowed_numbers(report: Dict) -> Set[float]:
    from .report import _totals  # report imports this module
    keys = ("delivered", "squad_delivered", "awaiting", "carried",
            "carried_agreed", "carried_not_agreed", "carried_unanswered",
            "teams")
    out: Set[float] = set()
    for g in report.get("groups", []):
        for totals in [g["totals"]] + [_totals([t]) for t in g["teams"]]:
            out.update(float(totals[k]) for k in keys)
    return out


def validate_summary(summary: Any, report: Dict, roster: Roster,
                     confirmed: List[str]) -> List[str]:
    """Problems with an agent-written report summary paragraph. Every
    number in it must be one the tool computed for this report."""
    if not isinstance(summary, str) or not summary.strip():
        return ["summary: missing or not text"]
    problems = []
    if len(summary) > SUMMARY_MAX:
        problems.append("summary: longer than %d characters" % SUMMARY_MAX)
    problems += ["summary: %s" % p
                 for p in _text_problems(summary, roster, confirmed)]
    if "%" in summary:
        problems.append("summary: percentages are not allowed; state the "
                        "counts the report shows")
    for m in _NUMBER_WORDS.finditer(summary):
        problems.append("summary: %r states a number or proportion in "
                        "words; use the figures the report shows"
                        % m.group(1))
    text = summary
    years = {report["as_of"][:4]}
    names = set()
    for g in report.get("groups", []):
        years.update((g["start"][:4], g["finish"][:4]))
        for t in g["teams"]:
            name = (t.get("sprint") or {}).get("name")
            if name:
                names.add(name)
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(r"(?<!\w)(?<!\d\.)%s(?!\w)(?!\.\d)"
                      % re.escape(name), " ", text)
    text = _DATES.sub(" ", text)
    text = _ISO_DATES.sub(" ", text)
    text = _YEAR.sub(lambda m: " " if m.group(0) in years else m.group(0),
                     text)
    allowed = _allowed_numbers(report)
    for n in _NUMBER.findall(text):
        if float(n) not in allowed:
            problems.append("summary: the number %s is not a figure in the "
                            "report" % n)
    return problems


# ---- report hooks ----------------------------------------------------------

def stored(doc: Dict, team: str, metrics: Dict,
           roster: Roster) -> Optional[Dict]:
    """The team's stored narrative entry if it still validates, else
    None (the report then lists ticket titles)."""
    if metrics.get("backfilled") or validate(
            doc, metrics, roster, list(doc.get("confirmed") or [])):
        return None
    return doc["teams"][team]


def apply_summary(home: Any, report: Dict, roster: Roster) -> None:
    """Set report["summary"]: the stored summary.json paragraph when it
    still validates, else the figures-only fallback. summary_fallback
    records that a stored summary existed but was not used."""
    doc = home.read_json("reports", report["as_of"], SUMMARY_FILE,
                         default=None)
    report["summary_fallback"] = False
    if doc is not None:
        if isinstance(doc, dict) and not validate_summary(
                doc.get("summary"), report, roster,
                list(doc.get("confirmed") or [])):
            report["summary"] = doc["summary"]
            return
        report["summary_fallback"] = True
    report["summary"] = fallback_summary(report)


# ---- commands --------------------------------------------------------------

def _setup(home: Any):
    from .config import load_config, load_roster, validate as check
    cfg, roster = load_config(home), load_roster(home)
    check(cfg, roster)
    return cfg, roster


def _sprint_metrics(home: Any, cfg: Any, team: str, sprint_id: str
                    ) -> Dict:
    if team not in [t.name for t in cfg.teams]:
        raise LedgerError("Unknown team %r." % team,
                          "Name a team from config.json.")
    d = home.team_sprint_dir(team, sprint_id)
    m = home.read_json(*d.relative_to(home.root).parts, "metrics.json")
    if m is None:
        raise LedgerError("No analysed sprint %s for team %s."
                          % (sprint_id, team),
                          "Run `ledger analyze --team %s` first." % team)
    if m.get("backfilled"):
        raise LedgerError(
            "Sprint %s of %s is backfilled; backfilled sprints have no "
            "narrative." % (sprint_id, team),
            "Write a narrative for the team's latest analysed sprint.")
    return m


def _input_cmd(args, home: Any) -> Dict[str, Any]:
    from .analyze import _load_snapshot
    cfg, _ = _setup(home)
    m = _sprint_metrics(home, cfg, args.team, args.sprint)
    access = cfg.agent_text_access
    if access == "none":
        if not args.force_ids:
            raise LedgerError(
                "agent_text_access is \"none\": ticket text may not be "
                "given to an agent under the current setting.",
                "The report will list ticket titles itself. Pass "
                "--force-ids for ticket ids and types only.")
    snap, _ = _load_snapshot(home, args.team, args.sprint)
    inp = agent_input(m, snap, access)
    return {"summary": json.dumps(inp, indent=2, ensure_ascii=False),
            "input": inp}


def _load(path: str) -> Any:
    try:
        raw = sys.stdin.read() if path == "-" else \
            Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise LedgerError("Cannot read %s: %s." % (path, e.strerror),
                          "Pass --file with the narrative JSON file.")
    try:
        return json.loads(raw)
    except ValueError as e:
        raise LedgerError("%s is not valid JSON: %s." % (path, e),
                          "Fix the file and validate it again.")


def _refuse(what: str, problems: List[str]) -> LedgerError:
    shown = problems[:_MAX_SHOWN]
    more = len(problems) - len(shown)
    return LedgerError(
        "%s has %d problem(s):\n%s%s" % (
            what, len(problems), "\n".join("  - %s" % p for p in shown),
            "\n  ... and %d more" % more if more else ""),
        "Fix the file, or pass --confirm \"SENTENCE\" for each sentence the "
        "delivery manager has confirmed.")


def _validate_cmd(args, home: Any) -> Dict[str, Any]:
    cfg, roster = _setup(home)
    doc = _load(args.file)
    if not isinstance(doc, dict):
        raise LedgerError("%s is not a JSON object." % args.file,
                          "See the narrative.json shape in the skill.")
    confirmed = list(args.confirm)
    own = [CONFIRM_IN_FILE] if "confirmed" in doc else []
    if args.summary:
        if args.as_of is None:
            raise LedgerError("--summary needs --as-of.",
                              "Pass the report date YYYY-MM-DD.")
        from .report import build_report  # report imports this module
        report = build_report(home, cfg, roster, args.as_of)
        problems = own + validate_summary(doc.get("summary"), report, roster,
                                          confirmed)
        problems += ["unknown top-level key %r" % k for k in doc
                     if k not in ("summary", "confirmed")]
        if problems:
            raise _refuse("The summary", problems)
        out = {"summary": doc["summary"], "confirmed": needed_confirmations(
            [doc["summary"]], confirmed)}
        home.write_json(out, "reports", args.as_of.isoformat(), SUMMARY_FILE)
        return {"summary": "Summary for %s validated and stored."
                % args.as_of.isoformat(), "ok": True}
    if not args.team or not args.sprint:
        raise LedgerError("narrative-validate needs --team and --sprint "
                          "(or --summary --as-of).",
                          "Name the team sprint the narrative is for.")
    from .annotations import require_not_issued
    require_not_issued(home, args.team, args.sprint, "its narrative")
    m = _sprint_metrics(home, cfg, args.team, args.sprint)
    entry = (doc.get("teams") or {}).get(args.team) \
        if isinstance(doc.get("teams"), dict) else None
    check = {k: v for k, v in doc.items() if k != "teams"}
    check["teams"] = {args.team: entry} if entry is not None else {}
    check.pop("confirmed", None)
    problems = own + validate(check, m, roster, confirmed)
    if problems:
        raise _refuse("The narrative for %s" % args.team, problems)
    texts = [x.get(k, "") for b in BUCKETS for x in entry.get(b, [])
             for k in ("heading", "text")]
    d = home.team_sprint_dir(args.team, args.sprint)
    home.write_json({"teams": {args.team: entry},
                     "confirmed": needed_confirmations(texts, confirmed)},
                    *d.relative_to(home.root).parts, NARRATIVE_FILE)
    return {"summary": "Narrative for %s, %s validated and stored."
            % (args.team, m["sprint"]["name"]), "ok": True}


def add_parser(subparsers) -> None:
    from .names_check import resolve_as_of
    p = subparsers.add_parser(
        "narrative-input",
        help="print the ticket input an agent may use for a narrative")
    p.add_argument("--team", required=True)
    p.add_argument("--sprint", required=True, metavar="ID")
    p.add_argument("--force-ids", action="store_true",
                   help="ids and types only, when ticket text is not allowed")
    p.set_defaults(func=_input_cmd)
    p = subparsers.add_parser(
        "narrative-validate",
        help="validate an agent-written narrative and store it")
    p.add_argument("--team")
    p.add_argument("--sprint", metavar="ID")
    p.add_argument("--summary", action="store_true",
                   help="validate a report summary paragraph instead")
    p.add_argument("--as-of", type=resolve_as_of,
                   help="report date YYYY-MM-DD (with --summary)")
    p.add_argument("--file", required=True, metavar="PATH",
                   help="narrative JSON file, or - for stdin")
    p.add_argument("--confirm", action="append", default=[],
                   metavar="SENTENCE",
                   help="a sentence the delivery manager has confirmed")
    p.set_defaults(func=_validate_cmd)
