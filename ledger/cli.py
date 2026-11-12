"""Command-line shell: argument parsing, dispatch and error reporting."""
import argparse
import json
import os
import sys
from typing import List, Optional

from . import (analyze, annotations, evidence, issue, names_check, narrative, render,
               reopen, risks, setup_check, status)
from .ado import collect, probe
from .testing import portfolio
from .errors import LedgerError
from .home import Home


COMMAND_MODULES = [analyze, annotations, collect, evidence, issue, names_check, narrative, portfolio,
                   probe, render, reopen, risks, setup_check]  # modules exposing add_parser(subparsers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger", description="Delivery Ledger")
    parser.add_argument("--home", help="data home directory (default: ~/delivery-ledger-data)")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    parser.add_argument("--show-text", dest="show_text", action="store_true",
                        help="print ticket text whatever agent_text_access says; for the "
                             "delivery manager's own terminal, never for an agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status.add_parser(subparsers)
    for module in COMMAND_MODULES:
        module.add_parser(subparsers)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        home = Home.resolve(args.home, os.environ)
        result = args.func(args, home)
    except LedgerError as e:
        print("error: %s" % e.message, file=sys.stderr)
        if e.hint:
            print("next: %s" % e.hint, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result["summary"])
    return 0
