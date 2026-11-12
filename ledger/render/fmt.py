"""Shared formatting for the rendered reports: numbers, dates, labels,
ticket links and the checks that keep person data out of the executive
dataset."""
import math
from collections import Counter
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

DASH = "–"
ELLIPSIS = "…"
TITLE_WIDTH = 90
LIST_TITLE_WIDTH = 110
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")
OUTCOMES = {
    "delivered": "Delivered",
    "closed_not_delivered": "Closed, not delivered",
    "awaiting_acceptance": "Finished, awaiting acceptance",
    "carried_in_progress": "Carried over, in progress",
    "carried_not_started": "Carried over, not started",
    "reclosed": "Re-closed",
    "active_outside_sprint": "Active outside a sprint",
    "assigned_outside_sprint": "Assigned, not started",
}
AGREED = {"yes": "Yes", "no": "No", "unknown": "Unknown"}
NOT_ANSWERED = "Not yet answered"
STATUS_CLASS = {"On track": "good", "Needs attention": "warn",
                "At risk": "crit"}
CARRIED = ("carried_in_progress", "carried_not_started")
NAMES_NOTE = "Two people can both count the same delivered ticket."


def ratio(n: int, m: int) -> str:
    """"n of m"; with a percentage only when the denominator is 5 or more."""
    if m < 5:
        return "%d of %d" % (n, m)
    return "%d of %d (%d%%)" % (n, m, round(100.0 * n / m))


def days(x: Optional[float]) -> str:
    """Working days to the nearest 0.5; "–" when unknown."""
    if x is None:
        return DASH
    x = math.floor(x * 2 + 0.5) / 2
    return ("%.1f" % x).rstrip("0").rstrip(".")


def person_days(available: float, nominal: float) -> str:
    """Available of nominal person-days; a ratio when both are whole."""
    if float(available).is_integer() and float(nominal).is_integer():
        return ratio(int(available), int(nominal))
    return "%s of %s" % (days(available), days(nominal))


def clip(text: str, width: int = TITLE_WIDTH) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[:width - 1] + ELLIPSIS


def fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return DASH
    d = date.fromisoformat(iso[:10])
    return "%d %s %d" % (d.day, _MONTHS[d.month - 1], d.year)


def date_range(start: str, finish: str) -> str:
    return "%s – %s" % (fmt_date(start), fmt_date(finish))


def bucket_label(key: str) -> str:
    if key.startswith("waiting:"):
        return "Waiting: " + key.split(":", 1)[1]
    return {"vendor": "In progress with a vendor engineer",
            "other_party": "In progress with another party",
            "unassigned": "Unassigned", "parked": "Parked"}.get(key, key)


def bucket_kind(key: str) -> str:
    if key.startswith("waiting:"):
        return "wait"
    return {"vendor": "ours", "other_party": "other",
            "unassigned": "idle", "parked": "parked"}.get(key, "idle")


def split_order(split: Dict[str, float]) -> List[Tuple[str, float]]:
    rank = {"ours": 0, "other": 1, "wait": 2, "idle": 3, "parked": 4}
    return sorted(((k, v) for k, v in split.items() if v),
                  key=lambda kv: (rank[bucket_kind(kv[0])], kv[0]))


def waiting_days(split: Dict[str, float]) -> float:
    return sum(v for k, v in split.items() if k.startswith("waiting:"))


def ticket_url(report: Dict, tid: Any) -> str:
    return "%s/%s/_workitems/edit/%s" % (
        report["org_url"].rstrip("/"), quote(report["project"], safe=""),
        int(tid))


def effective_label(status: Dict) -> Optional[str]:
    ov = status.get("override")
    return ov["label"] if ov else status.get("label")


def status_detail(status: Dict) -> str:
    """The reason beside the label: the rules that fired, or the DM's
    override with the computed label beside it."""
    rules = ", ".join(status.get("rules") or [])
    ov = status.get("override")
    if ov:
        computed = "computed: %s" % (status.get("label") or DASH)
        if rules:
            computed += " (%s)" % rules
        return "set by the delivery manager: %s; %s" % (ov["reason"],
                                                         computed)
    return rules


def team_markers(team: Dict) -> List[str]:
    out = []
    if team.get("unchanged"):
        out.append("unchanged since the last report")
    if team.get("backfilled"):
        out.append("backfilled from history")
    corrected = team.get("corrected")
    if corrected:
        out.append("corrected since edition %03d: %s" % (
            int(corrected.get("edition") or 0), corrected.get("note", "")))
    return out


def planned_text(planned: Dict) -> str:
    """"carried" is vendor-held only; tickets carried with another party
    have their own number (absent in sprints analysed before the split)."""
    other = ", %d with another party" % planned["with_other_party"] \
        if "with_other_party" in planned else ""
    return "%d planned: %d delivered, %d awaiting, %d carried%s, %d left, " \
           "%d not delivered" % (
               planned.get("total", 0), planned.get("delivered", 0),
               planned.get("awaiting", 0), planned.get("carried", 0), other,
               planned.get("left", 0), planned.get("not_delivered", 0))


RETURN_REASONS = ("defect", "requirement changed", "other")
NOT_RECORDED = "not recorded"


def returns_by_reason(team: Dict) -> Optional[Dict[str, int]]:
    """The sprint's return events by the reason the delivery manager
    recorded for each; None when the metrics do not mark which returns
    fall in the sprint (analysed before that was kept)."""
    out = {k: 0 for k in RETURN_REASONS + (NOT_RECORDED,)}
    notes = team.get("annotations") or {}
    marked = False
    for tid, t in team["metrics"].get("tickets", {}).items():
        if t.get("outside_teams") or t.get("involvement") != "worked":
            continue
        for r in t.get("returns") or []:
            if "in_sprint" not in r:
                continue
            marked = True
            if not r["in_sprint"]:
                continue
            reason = (notes.get("return:%s:%s" % (tid, r.get("rev")))
                      or {}).get("reason")
            out[reason if reason in RETURN_REASONS else NOT_RECORDED] += 1
    if not marked and (team["metrics"]["figures"].get("returns")
                       or {}).get("events"):
        return None
    return out


def returns_reason_text(team: Dict) -> str:
    """One line, or "" when the sprint had no return."""
    split = returns_by_reason(team)
    if not split or not sum(split.values()):
        return ""
    return "Returns in the sprint by recorded reason: %s" % ", ".join(
        "%d %s" % (split[k], k) for k in RETURN_REASONS + (NOT_RECORDED,))


def not_delivered_label(t: Dict) -> str:
    """A carried ticket another party holds is never called carried over."""
    if t.get("outcome") in CARRIED and not t.get("held_by_vendor_at_end"):
        return bucket_label("other_party")
    return OUTCOMES[t["outcome"]]


def returns_text(returns: Dict) -> str:
    s = "%d" % (returns.get("tickets") or 0)
    c = returns.get("cohort")
    if c and c.get("handed_off"):
        s += "; %d of %d handed off in %s returned" % (
            c["returned"], c["handed_off"], c["sprint"])
    return s


def received_text(r: Dict) -> str:
    return ("%d received: %d with no criteria at receipt, %d with text "
            "changed after receipt, %d added mid-sprint, %d assigned late"
            % (r.get("received", 0), r.get("no_criteria", 0),
               r.get("text_changed", 0), r.get("added_mid_sprint", 0),
               r.get("assigned_late", 0)))


def carried_split(t: Dict) -> str:
    return "%d agreed in advance, %d not, %d not yet answered" % (
        t["carried_agreed"], t["carried_not_agreed"],
        t["carried_unanswered"])


def carry_answer(team: Dict, tid: str) -> Dict:
    key = "carry:%s:%s" % (tid, team["metrics"]["sprint"]["id"])
    return (team.get("annotations") or {}).get(key) or {}


def agreed_text(answer: Dict) -> str:
    return AGREED.get(answer.get("agreed") or "", NOT_ANSWERED) \
        if answer else NOT_ANSWERED


def own_tickets(team: Dict, pred) -> List[Tuple[str, Dict]]:
    items = [(tid, t) for tid, t in team["metrics"].get("tickets",
                                                       {}).items()
             if not t.get("outside_teams") and pred(t)]
    return sorted(items, key=lambda x: (-(x[1].get("sprints_carried") or 0),
                                        int(x[0])))


def carried_rows(team: Dict) -> List[Tuple[str, Dict]]:
    return own_tickets(team, lambda t: t.get("outcome") in CARRIED
                       and t.get("held_by_vendor_at_end"))


def other_party_rows(team: Dict) -> List[Tuple[str, Dict]]:
    return own_tickets(team, lambda t: t.get("outcome") in CARRIED
                       and not t.get("held_by_vendor_at_end"))


def outside_rows(team: Dict) -> List[Tuple[str, Dict]]:
    return own_tickets(team,
                       lambda t: t.get("outcome") == "active_outside_sprint")


def outside_team_rows(team: Dict) -> List[Tuple[str, Dict]]:
    """Tickets under an area no configured team owns (DM report only)."""
    items = [(tid, t) for tid, t in team["metrics"].get("tickets",
                                                       {}).items()
             if t.get("outside_teams")]
    return sorted(items, key=lambda x: int(x[0]))


def with_elsewhere(n: int, elsewhere: int) -> str:
    """A person's count on the team's tickets, with the count on tickets
    outside the team's own beside it when there is one."""
    return "%d" % n if not elsewhere else "%d (+%d elsewhere)" % (
        n, elsewhere)


def teams(report: Dict) -> Iterable[Dict]:
    for g in report.get("groups", []):
        for t in g.get("teams", []):
            yield t


def require_exec(report: Dict) -> None:
    """Refuse anything but the executive projection."""
    if report.get("variant") != "exec":
        raise ValueError("the executive report needs exec_projection(report)")
    for key in ("people_names", "evidence"):
        if key in report:
            raise ValueError("executive dataset carries %r" % key)
    for team in teams(report):
        m = team.get("metrics", {})
        if "people" in m:
            raise ValueError("executive dataset carries people for team %s"
                             % team.get("name"))
        for t in m.get("tickets", {}).values():
            if "workers" in t or "holder_at_end" in t:
                raise ValueError("executive dataset carries ticket holders")


def require_dm(report: Dict) -> None:
    if report.get("variant") != "dm":
        raise ValueError("the delivery manager report needs the full report")


def list_lines(team: Dict, key: str, entry: Dict
               ) -> List[Tuple[int, Optional[str], str]]:
    """For a narrative entry with no text: (ticket id, clipped title or
    None when the ticket is unknown, plain-text suffix) per ticket. The
    suffix names the type when it is not the list's most common type, the
    wait for awaiting tickets and the outcome for not-delivered ones."""
    tickets = team["metrics"].get("tickets", {})
    ids = [int(t) for t in entry.get("tickets") or []]
    types = Counter(tickets[str(t)].get("type") for t in ids
                    if str(t) in tickets)
    common = types.most_common(1)[0][0] if types else None
    out = []
    for tid in ids:
        t = tickets.get(str(tid))
        if t is None:
            out.append((tid, None, ""))
            continue
        bits = []
        if t.get("type") and t.get("type") != common:
            bits.append("(%s)" % t["type"])
        if key == "awaiting":
            wait = "waiting %s working days" % days(t.get("days_waiting"))
            if t.get("wait_label"):
                wait += " · %s" % t["wait_label"]
            bits.append(wait)
        elif key == "not_delivered" and t.get("outcome") in OUTCOMES:
            bits.append(not_delivered_label(t))
        out.append((tid, clip(t.get("title") or "", LIST_TITLE_WIDTH),
                    " ".join(bits)))
    return out
