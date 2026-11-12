"""Evidence for flagged tickets in the delivery manager report: a holder
timeline, the dated change list and whole-life time split. Every identity
is resolved here to a roster display name or a neutral word, so no
pseudonym or roster id reaches the rendered file."""
from typing import Any, Dict, List, Optional

from ..analyze import trace_data
from ..config import Config, Roster
from ..history import BACKWARD, CARRIED, RETURNED
from ..home import Home
from .fmt import bucket_label, waiting_days

MAX_CARDS = 10
_PHASE = {"todo": "to do", "doing": "in progress", "done": "done",
          "removed": "removed"}


class _Names:
    def __init__(self, cfg: Config, roster: Roster):
        self.people = {p.id: p.display_name for p in roster.people}
        self.services = set(cfg.service_identities)

    def __call__(self, actor: Optional[str], none: str) -> str:
        if actor is None:
            return none
        if actor.startswith("p:") and actor[2:] in self.people:
            return self.people[actor[2:]]
        if actor.startswith("s:") or actor in self.services:
            return "automation"
        return "another party"


def _reasons(t: Dict, th: Dict[str, float]) -> List[str]:
    out = []
    if t.get("outcome") == "carried_in_progress" \
            and t.get("held_by_vendor_at_end") \
            and (t.get("sprints_carried") or 0) >= th["carry_flag_sprints"]:
        out.append(CARRIED)
    if len(t.get("returns") or []) >= th["returns_flag"]:
        out.append(RETURNED)
    if (t.get("backward_moves") or 0) >= th["backward_flag"]:
        out.append(BACKWARD)
    return out


def _phase(i: Dict) -> str:
    if i["phase"] == "handoff":
        return bucket_label("waiting:" + (i.get("wait_label") or "other"))
    return _PHASE.get(i["phase"], i["phase"])


def _kind(i: Dict, holder: str, names: _Names) -> str:
    if i["phase"] == "handoff":
        return "wait"
    if i["phase"] == "doing":
        return "ours" if holder in names.people.values() else "other"
    if i["phase"] == "done":
        return "done"
    return "idle"


def _segments(data: Dict, names: _Names) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for i in data["intervals"]:
        # while a ticket waits on a hand-off, whoever is still assigned is
        # not the one being waited on: name nobody
        holder = "" if i["phase"] == "handoff" \
            else names(i["holder"], "unassigned")
        phase = _phase(i)
        if out and out[-1]["holder"] == holder and out[-1]["phase"] == phase:
            out[-1]["days"] += i["days"]
            continue
        out.append({"holder": holder, "phase": phase, "days": i["days"],
                    "kind": _kind(i, holder, names), "start": i["start"]})
    while out and out[-1]["kind"] == "done":
        out.pop()  # time after the last close says nothing about the work
    return [s for s in out if s["days"] > 0]


def _what(c: Dict, names: _Names) -> str:
    f = c["field"]
    if f == "created":
        return "Created as %s" % c["to"]
    if f == "state":
        return "%s → %s" % (c["from"], c["to"])
    if f == "assignee":
        return ("Assigned to %s" % names(c["to"], "")) if c["to"] \
            else "Unassigned"
    if f == "sprint":
        if c["from"] is None:
            return "In %s" % c["to"]
        return "Moved from %s to %s" % (c["from"], c["to"])
    if f == "description":
        return "Description edited"
    return "Acceptance criteria edited"


def _changes(data: Dict, names: _Names) -> List[Dict[str, str]]:
    return [{"date": ch["date"],
             "what": "; ".join(_what(c, names) for c in ch["changes"]),
             "by": names(ch["by"], "unknown")} for ch in data["changes"]]


def _card(home: Home, cfg: Config, roster: Roster, team: str, tid: str,
          t: Dict, reasons: List[str], names: _Names) -> Dict[str, Any]:
    data = trace_data(home, cfg, roster, int(tid), team)
    split = data["time_split"]
    return {"ticket": int(tid), "title": t.get("title") or data["title"],
            "reasons": reasons, "sprints_carried": t.get("sprints_carried"),
            "returns_before_vendor": t.get("returns_before_vendor") or 0,
            "outside": bool(t.get("outside_teams")),
            "vendor_days": split.get("vendor", 0.0),
            "waiting_days": waiting_days(split),
            "segments": _segments(data, names),
            "changes": _changes(data, names)}


def on_card(t: Dict, pid: Optional[str] = None) -> bool:
    """A ticket outside the configured teams gets a card only while a
    roster person (pid, when given) holds it at the sprint's end."""
    if not t.get("outside_teams"):
        return True
    who = t.get("holder_at_end")
    return who not in (None, "other") and (pid is None or who == pid)


def flag_reasons(t: Dict, cfg: Config) -> List[str]:
    """The flag labels a ticket meets (carried, returned, moving backward)."""
    return _reasons(t, cfg.thresholds)


def ticket_card(home: Home, cfg: Config, roster: Roster, team: str,
                tid: str, t: Dict, reasons: List[str]) -> Dict[str, Any]:
    """One evidence card: holder timeline, dated changes, time split."""
    return _card(home, cfg, roster, team, tid, t, reasons,
                 _Names(cfg, roster))


def evidence_cards(home: Home, cfg: Config, roster: Roster,
                   report: Dict) -> Dict[str, List[Dict[str, Any]]]:
    """Cards per team for tickets meeting a flag threshold, most sprints
    carried first, at most MAX_CARDS per team."""
    th = cfg.thresholds
    names = _Names(cfg, roster)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for g in report.get("groups", []):
        for team in g["teams"]:
            found = []
            for tid, t in team["metrics"].get("tickets", {}).items():
                reasons = _reasons(t, th)
                if reasons and on_card(t):
                    found.append((tid, t, reasons))
            found.sort(key=lambda x: (-(x[1].get("sprints_carried") or 0),
                                      int(x[0])))
            out[team["name"]] = [
                _card(home, cfg, roster, team["name"], tid, t, reasons,
                      names)
                for tid, t, reasons in found[:MAX_CARDS]]
    return out
