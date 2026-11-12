"""The status command: what is in the data home, read-only and fast.

Reads config, roster, snapshot meta and sprint files, metrics, issue markers
and the reports folder; never a snapshot's ticket files."""
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .ado.collect import RUN_FILE
from .config import Config, load_config, load_roster, validate
from .home import Home, slug
from .model import _dt_in
from . import reopen as reopening


def _problem(name: str, e: Exception) -> str:
    text = getattr(e, "message", None) or "%s: %s" % (type(e).__name__, e)
    return "%s: %s" % (name, " ".join(str(text).split()))


def _read(path: Path, problems: List[str], kind: type = dict) -> Any:
    """The file's JSON if it is a `kind`; else a one-line problem and None."""
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        problems.append(_problem(str(path), e))
        return None
    if not isinstance(obj, kind):
        problems.append("%s: expected a JSON %s" % (
            path, "object" if kind is dict else kind.__name__))
        return None
    return obj


def _latest_collected(sprints_dir: Path, tz: ZoneInfo,
                      problems: List[str]) -> Optional[Dict]:
    best = None
    for meta_path in sorted(sprints_dir.glob("*/snapshot/meta.json")):
        snap = meta_path.parent
        meta = _read(meta_path, problems)
        cal = _read(snap / "sprints.json", problems)
        if meta is None or cal is None:
            continue
        sprint = next((s for ss in cal.values() if isinstance(ss, list)
                       for s in ss if isinstance(s, dict)
                       and s.get("id") == snap.parent.name), None)
        try:
            if sprint is None:
                raise ValueError("no entry for sprint %s" % snap.parent.name)
            end = _dt_in(sprint["end"])
            cutoff = _dt_in(meta["cutoff"]).astimezone(tz)
        except (KeyError, TypeError, ValueError) as e:
            problems.append(_problem(str(snap / "sprints.json"), e))
            continue
        if best is None or end > best[0]:
            finish = (end.astimezone(tz) - timedelta(microseconds=1)).date()
            best = (end, {"id": sprint["id"], "name": sprint.get("name"),
                          "finish": finish.isoformat(),
                          "cutoff": cutoff.strftime("%Y-%m-%d %H:%M")})
    return best[1] if best else None


def _issued(path: Path, problems: List[str]) -> Optional[Dict]:
    if not path.exists():
        return None
    issued = _read(path, problems)
    if issued is None:
        return None
    try:
        return {"edition": int(issued["edition"]),
                "as_of": str(issued.get("as_of", ""))}
    except (KeyError, TypeError, ValueError):
        problems.append("%s: no valid edition number" % path)
        return None


def _latest_analysed(sprints_dir: Path, problems: List[str]
                     ) -> Optional[Dict]:
    best = None
    for path in sorted(sprints_dir.glob("*/metrics.json")):
        m = _read(path, problems)
        sprint = m.get("sprint") if m is not None else None
        if not isinstance(sprint, dict) or "finish" not in sprint:
            if m is not None:
                problems.append("%s: no sprint finish date" % path)
            continue
        if best is None or str(sprint["finish"]) > best[0]:
            best = (str(sprint["finish"]), path.parent,
                    {"id": sprint.get("id", path.parent.name),
                     "name": sprint.get("name"),
                     "finish": sprint["finish"]})
    if best is None:
        return None
    best[2]["issued"] = _issued(best[1] / "issued.json", problems)
    best[2]["dir"] = best[1]
    return best[2]


def _team(home: Home, cfg: Config, name: str,
          problems: List[str]) -> Dict[str, Any]:
    d = home.path("teams", slug(name), "sprints")
    analysed = _latest_analysed(d, problems) if d.is_dir() else None
    sprint_dir = analysed.pop("dir") if analysed else None
    entry = reopening.pending(sprint_dir, home) if sprint_dir else None
    return {
        "name": name,
        "latest_collected": (_latest_collected(d, ZoneInfo(cfg.timezone),
                                               problems)
                             if d.is_dir() else None),
        "latest_analysed": analysed,
        "latest_issued": analysed.pop("issued") if analysed else None,
        "reopened": ({"edition": entry.get("edition"),
                      "as_of": entry.get("as_of"),
                      "reopened_at": entry.get("reopened_at"),
                      "note": entry.get("note")} if entry else None),
        "interrupted_collection": sorted(
            p.parent.parent.name for p in d.glob("*/snapshot/" + RUN_FILE))
        if d.is_dir() else [],
    }


def _latest_draft(home: Home) -> Optional[str]:
    root = home.path("reports")
    dates = []
    if root.is_dir():
        for d in root.iterdir():
            if (d / "draft").is_dir():
                try:
                    dates.append(date.fromisoformat(d.name))
                except ValueError:
                    continue
    return max(dates).isoformat() if dates else None


def _team_line(t: Dict[str, Any]) -> str:
    c, a, i = t["latest_collected"], t["latest_analysed"], t["latest_issued"]
    parts = [("collected %s (finished %s, cut-off %s)"
              % (c["name"], c["finish"], c["cutoff"])) if c
             else "nothing collected",
             ("analysed %s" % a["name"]) if a else "nothing analysed",
             ("issued in edition %03d of %s" % (i["edition"], i["as_of"]))
             if i else "not issued"]
    r = t["reopened"]
    if r:
        parts.append("reopened from edition %03d of %s, awaiting re-issue"
                     % (int(r["edition"] or 0), r["as_of"]))
    if t["interrupted_collection"]:
        parts.append("interrupted collection waiting to be resumed (%s)"
                     % ", ".join(t["interrupted_collection"]))
    return "  %s: %s" % (t["name"], "; ".join(parts))


def status(home: Home) -> Dict[str, Any]:
    problems: List[str] = []
    cfg = roster = None
    present = {n: home.path(n).exists() for n in ("config.json",
                                                  "roster.json")}
    for name, loader in (("config.json", load_config),
                         ("roster.json", load_roster)):
        if not present[name]:
            problems.append("%s: missing" % name)
            continue
        try:
            obj = loader(home)
        except Exception as e:  # any load failure is one line, never a trace
            problems.append(_problem(name, e))
            continue
        if name == "config.json":
            cfg = obj
        else:
            roster = obj
    config_ok, roster_ok = cfg is not None, roster is not None
    if cfg is not None and roster is not None:
        try:
            validate(cfg, roster)
        except Exception as e:  # never a traceback
            problems.append(_problem("config.json/roster.json", e))
            config_ok = roster_ok = False
    teams = [_team(home, cfg, t.name, problems)
             for t in cfg.teams] if cfg else []
    result: Dict[str, Any] = {
        "home": str(home.root), "config_ok": config_ok,
        "roster_ok": roster_ok, "problems": problems,
        "agent_text_access": cfg.agent_text_access if cfg else None,
        "timezone": cfg.timezone if cfg else None, "teams": teams,
        "latest_draft": _latest_draft(home)}

    def state(name: str, ok: bool) -> str:
        return "ok" if ok else ("missing" if not present[name] else "problem")

    lines = ["data home: %s" % home.root,
             "config.json: %s" % state("config.json", config_ok),
             "roster.json: %s" % state("roster.json", roster_ok)]
    lines += ["  problem: %s" % p for p in problems
              if not p.endswith(": missing")]
    if cfg:
        lines += ["agent text access: %s" % cfg.agent_text_access,
                  "timezone: %s" % cfg.timezone, "teams:"]
        lines += [_team_line(t) for t in teams] or ["  none configured"]
    lines.append("latest report draft: %s" % (result["latest_draft"] or "none"))
    result["summary"] = "\n".join(lines)
    return result


def _status_cmd(args, home: Home) -> Dict[str, Any]:
    return status(home)


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("status", help="show what exists in the data "
                              "home")
    p.set_defaults(func=_status_cmd)
