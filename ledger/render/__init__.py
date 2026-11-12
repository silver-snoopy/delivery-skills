"""Rendered reports and the `report` command."""
import argparse
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

from ..config import load_config, load_roster, validate
from ..errors import LedgerError
from ..home import Home
from ..issue import team_sprints, write_draft_inputs
from .. import reopen as reopening
from ..risks import stale
from ..report import build_report, clear_override, exec_projection, \
    set_override
from .evidence_cards import evidence_cards
from .html import render_html
from .markdown import render_markdown

__all__ = ["render_html", "render_markdown", "add_parser"]


def _write_text(home: Home, text: str, *parts: str) -> Path:
    target = home.path(*parts)
    home._mkdirs(target.parent)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(target))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return target


def _as_of(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD, got %r" % text)


def _report_cmd(args, home: Home) -> Dict[str, Any]:
    cfg, roster = load_config(home), load_roster(home)
    validate(cfg, roster)
    as_of = args.as_of or datetime.now(ZoneInfo(cfg.timezone)).date()
    if args.override:
        team, label = args.override
        set_override(home, cfg, as_of, team, label, args.reason or "")
    if args.clear_override:
        clear_override(home, as_of, args.clear_override)
    report = build_report(home, cfg, roster, as_of)
    reopening.require_reanalysed(home, team_sprints(report))
    if not report["groups"]:
        raise LedgerError(
            "No team has an analysed sprint ending on or before %s."
            % as_of.isoformat(),
            "Run `ledger collect` and `ledger analyze --all` first.")
    report["evidence"] = evidence_cards(home, cfg, roster, report)
    proj = exec_projection(report)
    draft = ("reports", as_of.isoformat(), "draft")
    files = {
        "executive_html": _write_text(home, render_html(proj, "exec"),
                                      *draft, "executive.html"),
        "executive_md": _write_text(home, render_markdown(proj),
                                    *draft, "executive.md"),
        "delivery_manager_html": _write_text(
            home, render_html(report, "dm"), *draft,
            "delivery-manager.html"),
    }
    write_draft_inputs(home, report)
    lines = ["Report drafts for %s:" % as_of.isoformat()]
    lines += ["  %s" % p for p in files.values()]
    if report["missing_teams"]:
        lines.append("No analysed sprint for: %s"
                     % ", ".join(report["missing_teams"]))
    if report.get("narrative_fallbacks"):
        lines.append("Stored narrative not used (it no longer validates; "
                     "ticket titles listed instead): %s"
                     % ", ".join(report["narrative_fallbacks"]))
    if report.get("summary_fallback"):
        lines.append("The stored summary was not used (it no longer matches "
                     "the figures); the report gives the figures-only "
                     "summary.")
    corrected = ["%s (%s, reopened from edition %03d; %d figure(s) moved)"
                 % (t["name"], t["sprint"]["name"],
                    int(t["corrected"]["edition"] or 0),
                    t["corrected"]["moved"])
                 for g in report["groups"] for t in g["teams"]
                 if (t.get("corrected") or {}).get("pending")]
    if corrected:
        lines.append("Corrected since an issued edition: %s. Issuing needs "
                     "--note." % "; ".join(corrected))
    alarmed = [t["name"] for g in report["groups"] for t in g["teams"]
               if (t["metrics"].get("coverage") or {}).get("alarm")]
    if alarmed:
        lines.append("COVERAGE ALARM: more than a fifth of the tickets "
                     "could not be collected for: %s. Their figures rest on "
                     "a partial board; do not issue before checking the "
                     "collection." % ", ".join(alarmed))
    risks_stale = stale(report)
    if risks_stale:
        lines.append("Open risks whose evidence the board no longer shows: "
                     "%s (close them with `ledger risks --close ID`)."
                     % ", ".join(risks_stale))
    return {"summary": "\n".join(lines), "as_of": as_of.isoformat(),
            "files": {k: str(v) for k, v in files.items()},
            "missing_teams": report["missing_teams"],
            "coverage_alarm": alarmed,
            "risks_stale": risks_stale,
            "narrative_fallbacks": report.get("narrative_fallbacks", []),
            "summary_fallback": report.get("summary_fallback", False),
            "groups": len(report["groups"])}


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "report", help="write the executive and delivery manager reports")
    p.add_argument("--as-of", type=_as_of,
                   help="report date YYYY-MM-DD (default: today)")
    p.add_argument("--override", nargs=2, metavar=("TEAM", "LABEL"),
                   help="set a team's status label for this report")
    p.add_argument("--reason", help="why the status was overridden")
    p.add_argument("--clear-override", metavar="TEAM",
                   help="remove a team's status override")
    p.set_defaults(func=_report_cmd)
