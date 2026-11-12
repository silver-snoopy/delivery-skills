"""setup-check: report problems in config, roster and the data home.

Problems are information, not failures: the command always exits 0.
"""
import os
from typing import Any, Dict, List

from .config import load_config, load_roster, validate
from .errors import LedgerError
from .home import Home

BAD_FILE = (LedgerError, ValueError, TypeError, AttributeError, KeyError)


def _why(e: Exception) -> str:
    return getattr(e, "message", None) or "%s: %s" % (type(e).__name__, e)


def _probe_states(home: Home) -> Any:
    """Work item type -> state names the probe recorded, or None."""
    report = home.read_json("probe", "probe-report.json")
    if report is None:
        return None
    return {wit: [s["name"] for s in states]
            for wit, states in report["states"].items()}


def setup_check(home: Home) -> Dict[str, Any]:
    problems: List[str] = []
    cfg = roster = None
    try:
        cfg = load_config(home)
    except BAD_FILE as e:
        problems.append("config.json: %s" % _why(e))
    try:
        roster = load_roster(home)
    except BAD_FILE as e:
        problems.append("roster.json: %s" % _why(e))
    if cfg is not None and roster is not None:
        try:
            validate(cfg, roster)
        except BAD_FILE as e:
            problems.append("validation: %s" % _why(e))
    if cfg is not None:
        try:
            known = _probe_states(home)
        except BAD_FILE as e:
            known = None
            problems.append("probe/probe-report.json: %s" % _why(e))
        if known is not None:
            every = {n for names in known.values() for n in names}
            for team in cfg.teams:
                for rule in team.phases:
                    if rule.state not in every:
                        problems.append(
                            "team %s: state %r is not among the states the "
                            "probe recorded" % (team.name, rule.state))
                ruled = {r.state.strip().lower() for r in team.phases}
                for wit in sorted(known):
                    if wit not in cfg.unit_types:
                        continue
                    for name in sorted(set(known[wit])):
                        if name.strip().lower() not in ruled:
                            problems.append(
                                "team %s: state %r (%s) was recorded by the "
                                "probe and has no phase rule"
                                % (team.name, name, wit))
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(cfg.timezone)
        except Exception:
            problems.append("timezone %r does not load" % cfg.timezone)
    if roster is not None:
        for p in roster.people:
            if not p.identity_ids and not p.aliases:
                problems.append("person %s has no identity id or alias" % p.id)
    real = os.path.realpath(str(home.root))
    if real == "/mnt" or real.startswith("/mnt/"):
        problems.append("data home %s is on a Windows mount" % real)
    head = "OK" if not problems else "%d problem(s)" % len(problems)
    summary = "\n".join([head] + ["- " + p for p in problems])
    return {"summary": summary, "problems": problems, "ok": not problems}


def _setup_check_cmd(args, home: Home) -> Dict[str, Any]:
    return setup_check(home)


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("setup-check",
                              help="check config, roster and data home")
    p.set_defaults(func=_setup_check_cmd)
