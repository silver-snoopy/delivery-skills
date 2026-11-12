"""Report assembly: one dataset per report date, teams grouped by identical
sprint dates. Totals are summed within a date group only, never across."""
import copy
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import annotations, narrative, reopen as reopening, risks
from .config import Config, Roster
from .errors import LedgerError
from .home import Home, slug

FOOTNOTE = ("Figures count tickets and working days taken from the board's "
            "change history. Tickets are not sized, so counts are compared "
            "only with the same team's earlier sprints. Durations reflect "
            "when the board was updated, not hours worked. Splitting or "
            "cloning a ticket cannot be detected. Sprint dates are as the "
            "board shows them today.")
LABELS = ("On track", "Needs attention", "At risk")
OVERRIDES = "overrides.json"
_CARRIED = ("carried_in_progress", "carried_not_started")
_PERSON_KEYS = ("owner", "by", "holder")
_RISK_PERSON_KEYS = ("people", "person", "persons", "context")


# ---- overrides -------------------------------------------------------------

def _overrides(home: Home, as_of: date) -> Dict[str, Dict]:
    return home.read_json("reports", as_of.isoformat(), OVERRIDES,
                          default={}) or {}


def set_override(home: Home, cfg: Config, as_of: date, team: str,
                 label: str, reason: str) -> None:
    if team not in [t.name for t in cfg.teams]:
        raise LedgerError("Unknown team %r." % team,
                          "Name a team from config.json.")
    if label not in LABELS:
        raise LedgerError("Invalid status label %r." % label,
                          "Use one of: %s." % ", ".join(LABELS))
    reason = (reason or "").strip()
    if not reason:
        raise LedgerError("A status override needs a reason.",
                          "Pass --reason with a short explanation.")
    cur = _overrides(home, as_of)
    cur[team] = {"label": label, "reason": reason}
    home.write_json(cur, "reports", as_of.isoformat(), OVERRIDES)


def clear_override(home: Home, as_of: date, team: str) -> None:
    cur = _overrides(home, as_of)
    if cur.pop(team, None) is not None:
        home.write_json(cur, "reports", as_of.isoformat(), OVERRIDES)


# ---- inputs ----------------------------------------------------------------

def _latest_sprint(home: Home, team: str, as_of: date
                   ) -> Optional[Tuple[Dict, tuple]]:
    root = home.path("teams", slug(team), "sprints")
    best = None
    if not root.is_dir():
        return None
    for d in sorted(root.iterdir()):
        if not (d / "metrics.json").is_file():
            continue
        parts = d.relative_to(home.root).parts
        m = home.read_json(*parts, "metrics.json")
        finish = m["sprint"]["finish"][:10]
        if finish <= as_of.isoformat() and (
                best is None or finish > best[0]["sprint"]["finish"][:10]):
            best = (m, parts)
    return best


def _latest_manifest(home: Home) -> Optional[Dict]:
    root = home.path("reports")
    best = None
    if root.is_dir():
        for f in root.glob("*/editions/*/manifest.json"):
            m = home.read_json(*f.relative_to(home.root).parts)
            if m and (best is None or m["issued_at"] > best["issued_at"]):
                best = m
    return best


def _narrative(home: Home, parts: tuple, team: str, metrics: Dict,
               roster: Roster, fallbacks: List[str]) -> Dict:
    if not metrics.get("backfilled"):
        doc = home.read_json(*parts, "narrative.json", default=None) or {}
        if isinstance(doc, dict) and team in (doc.get("teams") or {}):
            entry = narrative.stored(doc, team, metrics, roster)
            if entry is not None:
                return entry
            fallbacks.append(team)  # no longer validates: list titles
    return narrative.fallback(metrics)


# ---- assembly --------------------------------------------------------------

def _carried_ids(metrics: Dict) -> List[str]:
    return [tid for tid, t in metrics["tickets"].items()
            if t.get("outcome") in _CARRIED and t.get("held_by_vendor_at_end")
            and not t.get("outside_teams")]


def _totals(teams: List[Dict]) -> Dict[str, Any]:
    t: Dict[str, Any] = {k: 0 for k in (
        "teams", "engineers", "delivered", "squad_delivered", "awaiting",
        "carried", "carried_with_other_party", "carried_agreed",
        "carried_not_agreed", "carried_unanswered")}
    t.update(available=0.0, nominal=0.0, time_split={})
    for team in teams:
        m, f = team["metrics"], team["metrics"]["figures"]
        t["teams"] += 1
        t["engineers"] += team["engineers"]
        t["delivered"] += f["delivered"]
        t["squad_delivered"] += f["squad_delivered"]
        t["awaiting"] += f["awaiting_acceptance"]["count"]
        t["carried"] += f["carried"]["in_progress"] + f["carried"]["not_started"]
        t["carried_with_other_party"] += f["carried"]["with_other_party"]
        for tid in _carried_ids(m):
            key = "carry:%s:%s" % (tid, m["sprint"]["id"])
            agreed = (team["annotations"].get(key) or {}).get("agreed")
            t["carried_agreed" if agreed == "yes" else
              "carried_not_agreed" if agreed == "no" else
              "carried_unanswered"] += 1
        t["available"] += f["capacity"]["available"]
        t["nominal"] += f["capacity"]["nominal"]
        for k, v in f.get("time_split", {}).items():
            t["time_split"][k] = t["time_split"].get(k, 0.0) + v
    return t


def build_report(home: Home, cfg: Config, roster: Roster,
                 as_of: date) -> Dict[str, Any]:
    overrides = _overrides(home, as_of)
    manifest = _latest_manifest(home) or {}
    issued = {(x["team"], x["sprint_id"])
              for x in manifest.get("team_sprints", [])}
    groups: Dict[Tuple[str, str], List[Dict]] = {}
    missing: List[str] = []
    fallbacks: List[str] = []
    for team_cfg in cfg.teams:
        name = team_cfg.name
        found = _latest_sprint(home, name, as_of)
        if found is None:
            missing.append(name)
            continue
        m, parts = found
        status = dict(m.get("status") or {"label": None, "rules": [],
                                          "override": None})
        status["override"] = None
        if name in overrides:
            o = overrides[name]
            status["override"] = {"label": o["label"], "reason": o["reason"]}
        entry = {
            "name": name, "sprint": m["sprint"],
            "engineers": len(m.get("people", {})),
            "unchanged": ((name, m["sprint"]["id"]) in issued
                          and reopening.pending(home.path(*parts), home)
                          is None),
            # The corrected marker is part of the sprint's record: it shows
            # on every report that includes this sprint from the reissue on,
            # and goes only when a newer sprint of the team replaces it.
            "corrected": ({"edition": m["reopened"].get("edition"),
                           "note": m["reopened"].get("note", ""),
                           "moved": len(m["reopened"].get("moved") or []),
                           "pending": not home.path(*parts,
                                                    "issued.json").exists()}
                          if isinstance(m.get("reopened"), dict) else None),
            "backfilled": bool(m.get("backfilled")), "metrics": m,
            "annotations": annotations.current(
                home.read_json(*parts, annotations.FILE, default=[]) or []),
            "narrative": _narrative(home, parts, name, m, roster,
                                    fallbacks),
            "status": status,
        }
        key = (m["sprint"]["start"][:10], m["sprint"]["finish"][:10])
        groups.setdefault(key, []).append(entry)
    ordered = sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0]),
                     reverse=True)
    report: Dict[str, Any] = {
        "variant": "dm", "as_of": as_of.isoformat(),
        "generated": datetime.now(timezone.utc).replace(
            microsecond=0).isoformat(),
        "vendor_label": cfg.vendor_label, "org_url": cfg.org_url,
        "project": cfg.project, "footnote": FOOTNOTE, "summary": "",
        "risks": [],
        "groups": [{"start": s, "finish": f, "teams": teams,
                    "totals": _totals(teams)}
                   for (s, f), teams in ordered],
        "missing_teams": missing,
        "narrative_fallbacks": fallbacks,
        "people_names": {p.id: p.display_name for p in roster.people},
    }
    report["rules_note"] = rules_note(report)
    risks.attach(home, cfg, report)
    narrative.apply_summary(home, report, roster)
    return report


def rules_note(report: Dict) -> str:
    """The sentence a report carries when a sprint in it, or a sprint in a
    team's trend, was computed under other calculation rules than the
    current ones; "" otherwise."""
    from .metrics import RULES_VERSION
    out = []
    for g in report.get("groups", []):
        for team in g.get("teams", []):
            m = team["metrics"]
            v = m.get("rules_version")
            if v != RULES_VERSION:
                out.append(
                    "%s (%s) was computed under earlier calculation rules "
                    "(version %s; current version %s). Its figures are "
                    "shown as computed then; re-analyse and issue a new "
                    "edition to compare like with like."
                    % (team["name"], m["sprint"]["name"], v, RULES_VERSION))
            earlier = m.get("earlier_rules_sprints") or []
            if earlier:
                out.append(
                    "%s: the earlier sprints %s in its trend were computed "
                    "under earlier calculation rules."
                    % (team["name"], ", ".join(earlier)))
    return " ".join(out)


# ---- executive projection --------------------------------------------------

def _strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if k not in _PERSON_KEYS}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def strip_person_keys(obj: Any) -> Any:
    """A copy of obj without the person keys exec_projection removes
    (owner, by, holder) at any depth."""
    return _strip(copy.deepcopy(obj))


def exec_projection(report: Dict) -> Dict:
    """The report without any person-level field, for the executive file."""
    out = copy.deepcopy(report)
    out.pop("people_names", None)
    out.pop("evidence", None)
    for g in out.get("groups", []):
        for team in g.get("teams", []):
            m = team.get("metrics", {})
            for k in ("people", "coverage", "warnings", "collected_as"):
                m.pop(k, None)
            # tickets outside the configured teams are person context only
            m["tickets"] = {tid: t for tid, t in m.get("tickets", {}).items()
                            if not t.get("outside_teams")}
            for t in m.get("tickets", {}).values():
                t.pop("workers", None)
                t.pop("holder_at_end", None)
    out["risks"] = [{k: v for k, v in r.items()
                     if k not in _RISK_PERSON_KEYS}
                    if isinstance(r, dict) else r
                    for r in out.get("risks", [])]
    out = _strip(out)
    out["variant"] = "exec"
    return out
