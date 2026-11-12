"""What needs earlier sprints: trends, per-person flags and context, team
status and the return cohort. Every rule stays silent on missing data."""
import statistics
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .model import _dt_in
from .workcal import WorkCalendar

CARRIED = "Carried over more than 2 sprints"
RETURNED = "Ticket returned repeatedly"
BACKWARD = "Ticket moving backward"
FEWER = "Fewer delivered than usual"
NOTHING = "Nothing delivered or handed off"
QUIET = "No recorded board changes"

CHRONIC = "Chronic carryover"
RETURNS = "Returns"
_LABELS = ("On track", "Needs attention", "At risk")


def _num(x: float) -> str:
    """2.0 -> "2", 1.5 -> "1.5"."""
    return ("%.2f" % x).rstrip("0").rstrip(".")


def fewer_than_usual(values: List[Optional[float]], w: int, r: int) -> bool:
    """Each of the last r values is strictly below the mean of the w values
    immediately before it; needs w + r values, none missing."""
    if w < 1 or r < 1 or len(values) < w + r:
        return False
    tail = values[-(w + r):]
    if any(v is None for v in tail):
        return False
    for k in range(w, w + r):
        before = tail[k - w:k]
        if not tail[k] < sum(before) / w:
            return False
    return True


def _chronic(t: Dict[str, Any], th: Dict[str, float]) -> bool:
    return (t.get("outcome") == "carried_in_progress"
            and bool(t.get("held_by_vendor_at_end"))
            and (t.get("sprints_carried") or 0) >= th["carry_flag_sprints"])


def _cohort(name: str, start: date, handed: Dict[str, Dict[str, Any]],
            records: List[Dict[str, Any]], cal: WorkCalendar
            ) -> Tuple[Dict[str, Any], List[int], List[int]]:
    """Tickets handed off in a sprint, and those of them with a return
    after that ticket's own first hand-off in the sprint, in any of the
    given ticket records; also the ticket ids behind both counts, for the
    ledger. A record without a hand-off time (analysed before it was
    kept) falls back to the sprint's start."""
    lo = cal.day_start(start)
    ids = sorted(int(tid) for tid, t in handed.items()
                 if t.get("handed_off_in_sprint"))

    def came_back(tid: int) -> bool:
        at = handed[str(tid)].get("handed_off_at")
        own = _dt_in(at) if at else None
        for rec in records:
            for e in (rec.get(str(tid)) or {}).get("returns", []):
                when = _dt_in(e["at"])
                if (when > own) if own is not None else (when >= lo):
                    return True
        return False

    back = [tid for tid in ids if came_back(tid)]
    doc = {"sprint": name, "handed_off": len(ids), "returned": len(back)}
    return doc, ids, back


def _record_cohort(returns: Dict[str, Any], ledger: Dict[str, Any],
                   key: str, cohort) -> None:
    if cohort is None:
        returns[key] = None
        return
    doc, ids, back = cohort
    returns[key] = doc
    ledger["figures.returns.%s.handed_off" % key] = ids
    ledger["figures.returns.%s.returned" % key] = back


def _working_days(m: Dict[str, Any], cal: WorkCalendar) -> int:
    s = m["sprint"]
    return len(cal.working_dates(date.fromisoformat(s["start"]),
                                 date.fromisoformat(s["finish"])))


def _person_flags(pid: str, p: Dict[str, Any], m: Dict[str, Any],
                  ledger: Dict[str, Any], hist: List[Dict[str, Any]],
                  th: Dict[str, float]) -> List[str]:
    tickets = m.get("tickets", {})

    def counted(tid) -> Optional[Dict[str, Any]]:
        t = tickets.get(str(tid))
        if t is None or t.get("outcome") == "awaiting_acceptance":
            return None
        return t

    flags = []
    if any(_chronic(t, th) and t.get("holder_at_end") == pid
           and not t.get("outside_teams") for t in tickets.values()):
        flags.append(CARRIED)
    rets = [counted(e["ticket"]) for e in
            ledger.get("people.%s.returns" % pid, [])]
    if any(t is not None and len(t.get("returns", [])) >= th["returns_flag"]
           for t in rets):
        flags.append(RETURNED)
    back = [counted(e["ticket"]) for e in
            ledger.get("people.%s.backward_moves" % pid, [])]
    if any(t is not None and (t.get("backward_moves") or 0)
           >= th["backward_flag"] for t in back):
        flags.append(BACKWARD)

    w, r = int(th["trend_window"]), int(th["trend_runs"])
    if fewer_than_usual(p["trend_delivered"], w, r):
        span = hist[-(w + r):]
        if len(span) == w + r and not any(
                (h["people"].get(pid) or {}).get("comparison_break", True)
                for h in span[1:]):
            flags.append(FEWER)

    n = int(th["nothing_sprints"])
    span = hist[-n:] if n > 0 else []
    if n > 0 and len(span) == n and all(
            _idle(h["people"].get(pid), th) for h in span):
        flags.append(NOTHING)

    if p.get("board_change_days") == 0 and \
            (p.get("available_days") or 0) >= th["quiet_days"]:
        flags.append(QUIET)
    return flags


def _idle(p: Optional[Dict[str, Any]], th: Dict[str, float]) -> bool:
    if p is None:
        return False
    nominal = p.get("nominal_days") or 0
    if nominal <= 0:
        return False
    return ((p.get("available_days") or 0) / nominal
            >= th["nothing_min_availability"]
            and (p.get("delivered_worked_on") or 0)
            + (p.get("delivered_elsewhere") or 0) == 0
            and (p.get("handed_off") or 0)
            + (p.get("handed_off_elsewhere") or 0) == 0)


def _context(pid: str, p: Dict[str, Any], m: Dict[str, Any],
             hist: List[Dict[str, Any]], th: Dict[str, float],
             workdays: int) -> List[str]:
    out = []
    nominal = p.get("nominal_days") or 0
    leave = nominal - (p.get("available_days") or 0)
    if leave > 0:
        out.append("%s days leave" % _num(leave))
    if p.get("is_joiner"):
        first = next(h for h in hist if pid in h["people"])
        out.append("Joined in %s" % first["sprint"]["name"])
    idle = p.get("days_nothing_in_progress") or 0
    if idle >= th["nothing_in_progress_days"]:
        out.append("%s days with nothing in progress" % _num(idle))
    if "allocation" in p:
        # joining or leaving inside the sprint lowers the nominal days
        # without being a part-time allocation
        if p["allocation"] is not None and 0 < p["allocation"] < 1:
            out.append("Allocation %s" % _num(p["allocation"]))
    elif workdays > 0 and 0 < nominal < workdays:
        # metrics analysed before the allocation was recorded
        out.append("Allocation %s" % _num(round(nominal / workdays, 2)))
    if p.get("comparison_break") and len(hist) > 1 and \
            pid not in hist[-2]["people"]:
        out.append("Moved team")
    return out


def sprint_load(figures: Dict, earlier: List[Dict],
                th: Dict[str, float]) -> Optional[Dict[str, Any]]:
    """Tickets with vendor engineers at the sprint's start against the
    median the team delivered in its previous sprints (up to
    window_sprints of them). None when the sprint has no scope figures
    (analysed before they were kept); median None with fewer than
    load_history_min earlier sprints."""
    at_start = ((figures.get("scope") or {}).get("at_start") or {}) \
        .get("total")
    if at_start is None:
        return None
    window = max(int(th["window_sprints"]), 1)
    past = [h["figures"]["delivered"] for h in earlier[-window:]
            if isinstance(h.get("figures", {}).get("delivered"), int)]
    if len(past) < max(int(th["load_history_min"]), 1):
        return {"at_start": at_start, "median_delivered": None,
                "sprints": len(past), "above_usual": False}
    med = float(statistics.median(past))
    return {"at_start": at_start, "median_delivered": med,
            "sprints": len(past),
            "above_usual": at_start >= med * th["load_ratio"]
            and at_start - med >= th["load_min_excess"]}


def apply(metrics: Dict, ledger: Dict, earlier: List[Dict],
          cfg: Config) -> None:
    """Add trends, flags, context, status and return cohorts to `metrics`,
    and the ledger entries behind the cohort counts to `ledger`.
    `earlier` holds the same team's previous sprints, oldest first."""
    th = cfg.thresholds
    cal = WorkCalendar(cfg.timezone, cfg.holidays)
    window = max(int(th["window_sprints"]), 1)
    hist = list(earlier[-(window - 1):] if window > 1 else []) + [metrics]
    figures = metrics["figures"]
    tickets = metrics.get("tickets", {})

    figures["trend_delivered"] = [h["figures"]["delivered"] for h in hist]
    figures["load"] = sprint_load(figures, earlier, th)
    if figures["load"] is not None:
        ledger["figures.load.at_start"] = list(
            ledger.get("figures.scope.at_start.total", []))
        names = [h["sprint"]["name"] for h in earlier[-max(
            int(th["window_sprints"]), 1):]][-figures["load"]["sprints"]:] \
            if figures["load"]["sprints"] else []
        ledger["figures.load.sprints"] = names
        ledger["figures.load.median_delivered"] = names
    w, r = int(th["trend_window"]), int(th["trend_runs"])

    prev = earlier[-1] if earlier else None
    returns = figures.setdefault("returns", {})
    _record_cohort(returns, ledger, "cohort", None if prev is None else
                   _cohort(prev["sprint"]["name"],
                           date.fromisoformat(prev["sprint"]["start"]),
                           prev.get("tickets", {}),
                           [tickets, prev.get("tickets", {})], cal))
    _record_cohort(returns, ledger, "cohort_so_far", _cohort(
        metrics["sprint"]["name"],
        date.fromisoformat(metrics["sprint"]["start"]), tickets, [tickets],
        cal))

    workdays = _working_days(metrics, cal)
    for pid, p in metrics["people"].items():
        p["trend_delivered"] = [
            (h["people"][pid].get("delivered_worked_on")
             if pid in h["people"] else None) for h in hist]
        p["flags"] = _person_flags(pid, p, metrics, ledger, hist, th)
        p["context"] = _context(pid, p, metrics, hist, th, workdays)

    rules = []
    if any(_chronic(t, th) and not t.get("outside_teams")
           for t in tickets.values()):
        rules.append(CHRONIC)
    if (returns.get("tickets") or 0) >= th["status_returned_tickets"]:
        rules.append(RETURNS)
    if fewer_than_usual(figures["trend_delivered"], w, r):
        rules.append(FEWER)
    metrics["status"] = {"label": _LABELS[min(len(rules), 2)],
                         "rules": rules, "override": None}
