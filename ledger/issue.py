"""Issuing: freeze a report draft as an immutable, numbered edition with a
manifest of hashes of everything it was built from."""
import hashlib
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__, annotations, reopen as reopening
from .ado.collect import RUN_FILE
from .config import Config, Roster, load_config, load_roster, validate
from .errors import LedgerError
from .home import Home
from .metrics import RULES_VERSION
from .names_check import (approvals, draft_texts,
                          latest_draft_date, names_check, resolve_as_of)
from .narrative import SUMMARY_FILE
from .report import OVERRIDES, build_report

INPUTS = "inputs.json"
DRAFT_FILES = ("delivery-manager.html", "executive.html", "executive.md")
_SPRINT_FILES = ("metrics.json", "ledger.json", annotations.FILE,
                 "narrative.json")
_ISO_Z = "%Y-%m-%dT%H:%M:%SZ"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def team_sprints(report: Dict) -> List[Dict[str, str]]:
    return [{"team": t["name"], "sprint_id": t["sprint"]["id"]}
            for g in report["groups"] for t in g["teams"]]


def input_hashes(home: Home, report: Dict) -> Dict[str, str]:
    """sha256 of every file the report was built from, keyed by the path
    relative to the data home."""
    files: List[Path] = []
    for ts in team_sprints(report):
        d = home.team_sprint_dir(ts["team"], ts["sprint_id"])
        files += [d / n for n in _SPRINT_FILES if (d / n).is_file()]
        snap = d / "snapshot"
        if snap.is_dir():
            files += sorted(p for p in snap.rglob("*") if p.is_file())
    files += [home.path(n) for n in ("config.json", "roster.json",
                                     "risks.json")]
    files.append(home.path("reports", report["as_of"], OVERRIDES))
    files.append(home.path("reports", report["as_of"], SUMMARY_FILE))
    return {p.relative_to(home.root).as_posix(): sha256_file(p)
            for p in files if p.is_file()}


def write_draft_inputs(home: Home, report: Dict) -> None:
    """Record what a draft was built from (called by the report command)."""
    home.write_json(
        {"built_at": datetime.now(timezone.utc).strftime(_ISO_Z),
         "inputs": input_hashes(home, report)},
        "reports", report["as_of"], "draft", INPUTS)


def _editions(home: Home, as_of: date) -> Path:
    return home.path("reports", as_of.isoformat(), "editions")


def _issued_at(home: Home) -> str:
    """Now, in the one format build_report compares; always after the
    newest existing edition so editions sort in issue order."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    latest = ""
    root = home.path("reports")
    if root.is_dir():
        for f in root.glob("*/editions/*/manifest.json"):
            m = home.read_json(*f.relative_to(home.root).parts) or {}
            latest = max(latest, str(m.get("issued_at", "")))
    if latest and now.strftime(_ISO_Z) <= latest:
        try:
            now = datetime.strptime(latest, _ISO_Z).replace(
                tzinfo=timezone.utc) + timedelta(seconds=1)
        except ValueError:
            pass
    return now.strftime(_ISO_Z)


def issue(home: Home, cfg: Config, roster: Roster, as_of: date,
          note: str = "") -> int:
    day = as_of.isoformat()
    draft = home.path("reports", day, "draft")
    if not all((draft / f).is_file() for f in DRAFT_FILES + (INPUTS,)):
        raise LedgerError("No complete report draft for %s." % day,
                          "Run `ledger report --as-of %s` first." % day)
    draft_hashes = {n: sha256_file(draft / n) for n in DRAFT_FILES + (INPUTS,)}
    approved = approvals(home, as_of)
    hits = names_check(draft_texts(home, as_of), roster, approved)
    if hits:
        raise LedgerError(
            "The executive draft contains %d name occurrence(s), first: %s."
            % (len(hits), hits[0]["term"]),
            "Run `ledger names-check --as-of %s`, then edit the text or "
            "approve each occurrence." % day)
    report = build_report(home, cfg, roster, as_of)
    unfinished = [ts for ts in team_sprints(report)
                  if (home.team_sprint_dir(ts["team"], ts["sprint_id"])
                      / "snapshot" / RUN_FILE).exists()]
    if unfinished:
        ts = unfinished[0]
        raise LedgerError(
            "Sprint %s of %s has an unfinished collection."
            % (ts["sprint_id"], ts["team"]),
            "Finish it with `ledger collect --team %s`, or discard it with "
            "`ledger collect --team %s --refresh`, then run the report "
            "again." % (ts["team"], ts["team"]))
    reopening.require_reanalysed(home, team_sprints(report))
    supersedes = []
    for ts in team_sprints(report):
        entry = reopening.pending(
            home.team_sprint_dir(ts["team"], ts["sprint_id"]), home)
        if entry is not None:
            supersedes.append(dict(ts, edition=entry.get("edition"),
                                   as_of=entry.get("as_of")))
    current = input_hashes(home, report)
    recorded = (home.read_json("reports", day, "draft", INPUTS) or {}
                ).get("inputs")
    if current != recorded:
        changed = sorted(set(current.items()) ^ set((recorded or {}).items()))
        raise LedgerError(
            "The draft for %s is out of date: %d input(s) changed since it "
            "was built (e.g. %s)." % (day, len({k for k, _ in changed}),
                                      changed[0][0] if changed else "?"),
            "Run the report again (`ledger report --as-of %s`), then issue." % day)
    root = _editions(home, as_of)
    numbers = [int(p.name) for p in root.iterdir()
               if p.name.isdigit()] if root.is_dir() else []
    if numbers:
        repaired = _repair_markers(home, root / ("%03d" % max(numbers)),
                                   recorded, supersedes)
        if repaired:
            return repaired
    if supersedes and not (note or "").strip():
        raise LedgerError(
            "The report includes %d reopened sprint(s) (first: %s of %s, "
            "issued in edition %03d of %s)."
            % (len(supersedes), supersedes[0]["sprint_id"],
               supersedes[0]["team"], int(supersedes[0]["edition"] or 0),
               supersedes[0]["as_of"]),
            "Issue with --note saying what was corrected.")
    number = max(numbers, default=0) + 1
    note = (note or "").strip()
    if number > 1 and not note:
        raise LedgerError(
            "Edition %03d of %s already exists." % (number - 1, day),
            "A later edition needs --note saying what changed.")
    target = root / ("%03d" % number)
    if target.exists():
        raise LedgerError("Edition directory %s already exists." % target,
                          "Editions are never overwritten; check the "
                          "editions folder.")
    sprints = team_sprints(report)
    issued_at = _issued_at(home)
    home._mkdirs(root)
    tmp = root / (".tmp-%03d" % number)
    try:
        if tmp.exists():
            shutil.rmtree(str(tmp))
        tmp.mkdir(mode=0o700)
        for name in DRAFT_FILES + (INPUTS,):
            shutil.copyfile(str(draft / name), str(tmp / name))
            if sha256_file(tmp / name) != draft_hashes[name]:
                raise LedgerError(
                    "The draft file %s changed while issuing." % name,
                    "Run names-check and issue again.")
        manifest: Dict[str, Any] = {
            "edition": number, "as_of": day, "issued_at": issued_at,
            "note": note, "tool_version": __version__,
            "rules_version": RULES_VERSION, "team_sprints": sprints,
            "outputs": {n: draft_hashes[n] for n in DRAFT_FILES},
            "inputs": recorded, "names_approved": approved,
        }
        if supersedes:
            manifest["supersedes"] = supersedes
        home.write_json(manifest, *tmp.relative_to(home.root).parts,
                        "manifest.json")
        if target.exists():
            raise LedgerError(
                "Edition directory %s already exists." % target,
                "Editions are never overwritten; check the editions folder.")
        os.rename(str(tmp), str(target))
    except BaseException as e:
        shutil.rmtree(str(tmp), ignore_errors=True)
        if isinstance(e, LedgerError) or not isinstance(e, Exception):
            raise
        raise LedgerError("Issuing edition %03d of %s failed: %s"
                          % (number, day, e),
                          "Nothing was issued; fix the cause and run "
                          "`ledger issue` again.") from e
    try:
        _write_markers(home, sprints, number, day, issued_at)
    finally:
        _lock(target)
    return number


def _lock(target: Path) -> None:
    for p in target.iterdir():
        os.chmod(str(p), 0o400)
    os.chmod(str(target), 0o500)


def _write_markers(home: Home, sprints: List[Dict[str, str]], number: int,
                   day: str, issued_at: str) -> None:
    """Write issued.json into each sprint; all or none for this edition."""
    marker = {"edition": number, "as_of": day, "issued_at": issued_at}
    written: List[Tuple[Path, Optional[bytes]]] = []
    try:
        for ts in sprints:
            d = home.team_sprint_dir(ts["team"], ts["sprint_id"])
            path = d / "issued.json"
            before = path.read_bytes() if path.exists() else None
            home.write_json(marker, *d.relative_to(home.root).parts,
                            "issued.json")
            written.append((path, before))
    except Exception as e:
        # a sprint issued before stays protected by its earlier marker
        for p, before in written:
            try:
                if before is None:
                    p.unlink()
                else:
                    p.write_bytes(before)
                    os.chmod(str(p), 0o600)
            except OSError:
                pass
        raise LedgerError(
            "Edition %03d of %s was saved, but marking its sprints as issued "
            "failed: %s" % (number, day, e),
            "Run `ledger issue --as-of %s` again to finish marking them."
            % day) from e


def _repair_markers(home: Home, edition: Path, recorded: Any,
                    supersedes: List[Dict[str, Any]]) -> int:
    """If the newest edition was built from these inputs (and supersedes
    the same reopened sprints) but some sprint has no issued.json, or one
    that still names an earlier edition (a failed marker write puts every
    marker back as it was), write those markers; return its number."""
    m = home.read_json(*edition.relative_to(home.root).parts,
                       "manifest.json")
    if not m or m.get("inputs") != recorded \
            or (m.get("supersedes") or []) != supersedes:
        return 0
    missing = []
    for ts in m["team_sprints"]:
        d = home.team_sprint_dir(ts["team"], ts["sprint_id"])
        try:
            old = home.read_json(*d.relative_to(home.root).parts,
                                 "issued.json")
        except LedgerError:
            old = None
        if not isinstance(old, dict) or str(old.get("issued_at", "")) \
                < str(m.get("issued_at", "")):
            missing.append(ts)
    if not missing:
        return 0
    _write_markers(home, missing, m["edition"], m["as_of"], m["issued_at"])
    return int(m["edition"])


def _issue_cmd(args, home: Home) -> Dict[str, Any]:
    cfg, roster = load_config(home), load_roster(home)
    validate(cfg, roster)
    as_of = args.as_of or latest_draft_date(home)
    n = issue(home, cfg, roster, as_of, args.note or "")
    path = _editions(home, as_of) / ("%03d" % n)
    m = home.read_json(*path.relative_to(home.root).parts, "manifest.json")
    return {"summary": "Issued edition %03d of %s (%d team sprint(s)):\n  %s"
                       % (n, as_of.isoformat(), len(m["team_sprints"]), path),
            "edition": n, "path": str(path),
            "team_sprints": len(m["team_sprints"])}


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "issue", help="freeze the report draft as a numbered edition")
    p.add_argument("--as-of", type=resolve_as_of,
                   help="report date YYYY-MM-DD (default: latest draft)")
    p.add_argument("--note", help="what changed (required after edition 001)")
    p.set_defaults(func=_issue_cmd)
