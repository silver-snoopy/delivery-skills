"""Command line: `ledger [--dir DIR] COMMAND`."""
import argparse
import os
import sys
from typing import List, Optional

from ledger.ado import AdoError

DEFAULT_DIR = os.path.join(".", ".delivery-skills")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ledger")
    p.add_argument("--dir", default=DEFAULT_DIR, help="working directory (default ./.delivery-skills)")
    sub = p.add_subparsers(dest="command", required=True)
    i = sub.add_parser("init", help="set up from the query, staff list and team calendar")
    i.add_argument("--query", required=True)
    i.add_argument("--staff", required=True)
    i.add_argument("--vacations", required=True)
    i.add_argument("--yes", action="store_true", help="accept every suggestion")
    i.add_argument("--org")
    i.add_argument("--project")
    i.add_argument("--timezone")
    r = sub.add_parser("report", help="write the manager and DM reports for one sprint")
    r.add_argument("--sprint", help="sprint name (default: the most recently ended sprint)")
    pp = sub.add_parser("person", help="one engineer over the team's last 6 sprints")
    pp.add_argument("email")
    for s in (r, pp):
        s.add_argument("--offline", action="store_true", help="use the cached board history")
        s.add_argument("--today", help="YYYY-MM-DD (default: today)")
    return p


def _ask_for(args, out):
    if args.yes:
        return lambda prompt, default: default
    if not sys.stdin.isatty():
        print("stdin is not a terminal: suggestions were accepted", file=out)
        return lambda prompt, default: default

    def ask(prompt: str, default: str) -> str:
        answer = input("%s [%s] " % (prompt, default)).strip()
        return answer or default
    return ask


def main(argv: Optional[List[str]] = None, client=None, out=None) -> int:
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    try:
        if args.command == "init":
            from ledger.setup import run_init
            return run_init(args, client, _ask_for(args, out), out)
        from ledger.render import run_person, run_report
        return (run_report if args.command == "report" else run_person)(args, client, out)
    except (AdoError, ValueError) as e:
        print("error: %s" % e, file=out)
        if isinstance(e, AdoError) and e.next_step:
            print("next: %s" % e.next_step, file=out)
        return 1
    return 0
