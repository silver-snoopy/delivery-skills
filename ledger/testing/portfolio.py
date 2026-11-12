"""The golden demo portfolio and the `demo` command.

Three invented teams, eight invented people and six sprints of ticket
history each, run through the whole pipeline (analyze, sample DM
annotations, report) so anyone can see real reports without Azure DevOps
access. Each team's story lives in its own module."""
import argparse
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import analyze, annotations
from ..config import load_config, load_roster, validate
from ..errors import LedgerError
from ..home import DEFAULT_DIRNAME, Home
from ..render import _report_cmd
from .builder import PortfolioBuilder
from .portfolio_claims import claims_intake
from .portfolio_common import (AS_OF, CLAIMS, CUTOFF, PLATFORM, PRICING,
                               TZ)
from .portfolio_platform import data_platform
from .portfolio_pricing import pricing_models

__all__ = ["AS_OF", "CLAIMS", "CUTOFF", "PLATFORM", "PRICING", "build_demo",
           "run_demo", "add_parser"]

# When the DM "answered", fixed so the demo output is reproducible.
_ANSWERED = "2026-11-07T09:30:00+00:00"

# Sample DM answers, by (team, ticket, kind). Everything else stays
# pending, so "Not yet answered" shows in the reports. Notes name nobody.
_ANSWERS = {
    (CLAIMS, 40197, "carry"): dict(
        agreed="yes", reason="dependency",
        note="Waiting for the address lookup service to be released "
             "upstream; agreed with the product owner at planning."),
    (PRICING, 40655, "carry"): dict(
        agreed="no", reason="requirement changed",
        note="Acceptance criteria rewritten after each review; a "
             "refinement session is booked before the next sprint."),
    (PRICING, 40655, "return"): dict(reason="requirement changed"),
    (PLATFORM, 40951, "return"): dict(
        reason="defect", note="Retry limit was not applied to weekend "
                              "loads."),
}


def build_demo(b: Optional[PortfolioBuilder] = None) -> PortfolioBuilder:
    if b is None:
        b = PortfolioBuilder(tz=TZ, not_delivered_reasons=["Duplicate"])
    claims_intake(b)
    pricing_models(b)
    data_platform(b)
    b.backlog_role("po", "Product owner", ["product owner"])
    return b


def _record_sample_answers(home: Home, team: str, sprint_id: str) -> None:
    d = home.team_sprint_dir(team, sprint_id)
    metrics = home.read_json(*d.relative_to(home.root).parts, "metrics.json")
    for item in annotations.pending(metrics,
                                    annotations.load(home, team, sprint_id)):
        answer = _ANSWERS.get((team, item["ticket"], item["kind"]))
        if answer is not None:
            annotations.record(home, team, sprint_id, item["key"],
                               now=_ANSWERED, **answer)


def run_demo(home: Home) -> Dict[str, Any]:
    default = Path.home() / DEFAULT_DIRNAME
    if os.path.realpath(str(home.root.expanduser())) == \
            os.path.realpath(str(default)):
        raise LedgerError(
            "The demo will not write into the default data home %s."
            % default,
            "Give a new directory, such as --home /tmp/ledger-demo.")
    if home.root.exists() and (not home.root.is_dir()
                               or any(home.root.iterdir())):
        raise LedgerError(
            "The demo home %s exists and is not empty." % home.root,
            "Pass --home with a new or empty directory; the demo never "
            "writes into existing data.")
    home.ensure()
    b = build_demo()
    b.write(home)
    cfg, roster = load_config(home), load_roster(home)
    validate(cfg, roster)
    for team in cfg.teams:
        # Re-save each snapshot with the demo's fixed cut-off.
        last = b.sprints(team.name)[-1]
        b.snapshot(cutoff=CUTOFF, team=team.name).save(
            home.team_sprint_dir(team.name, last.id) / "snapshot")
        analyze.run_analyze(home, cfg, roster, team.name)
        _record_sample_answers(home, team.name, last.id)
    rep = _report_cmd(argparse.Namespace(
        as_of=date.fromisoformat(AS_OF), override=None, clear_override=None,
        reason=None), home)
    files = rep["files"]
    lines: List[str] = [
        "Demo data home written to %s." % home.root,
        "All teams, people and tickets in it are invented; no Azure DevOps "
        "access was used.",
        "Open these report drafts (as of %s):" % AS_OF,
        "  Executive report:        %s" % files["executive_html"],
        "  Executive (Markdown):    %s" % files["executive_md"],
        "  Delivery manager report: %s" % files["delivery_manager_html"],
        "No edition was issued. Try `ledger --home %s trace 40655` to see "
        "one ticket's history." % home.root,
    ]
    return {"summary": "\n".join(lines), "files": files,
            "home": str(home.root)}


def _demo_cmd(args, _home: Home) -> Dict[str, Any]:
    if not (args.demo_home or "").strip():
        raise LedgerError("demo needs a directory for --home.",
                          "Give a new directory, such as "
                          "--home /tmp/ledger-demo.")
    return run_demo(Home.resolve(args.demo_home, {}))


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "demo", help="write an invented demo portfolio and its reports")
    p.add_argument("--home", dest="demo_home", required=True, metavar="DIR",
                   help="new or empty directory to write the demo into")
    p.set_defaults(func=_demo_cmd)
