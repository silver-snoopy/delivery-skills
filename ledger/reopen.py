"""Reopening an issued sprint for a deliberate, recorded correction.

Only the team's latest sprint, collected when it ended, can be reopened.
The issued edition under reports/ is never touched. The sprint's issued
data is kept, read-only, under superseded/<NNN>/ in the sprint folder, the
reopen is logged in reopened.json, and issued.json is removed so analyze
(and, for the team's latest ended sprint, collect) work on it again."""
import hashlib
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import LedgerError
from .home import Home

REOPENED = "reopened.json"
SUPERSEDED = "superseded"
_COPIED = ("metrics.json", "ledger.json", "annotations.json",
           "narrative.json")


def _entries(sprint_dir: Path, home: Home) -> List[Dict[str, Any]]:
    try:
        entries = home.read_json(*sprint_dir.relative_to(home.root).parts,
                                 REOPENED)
    except LedgerError:
        return []
    return [e for e in entries if isinstance(e, dict)] \
        if isinstance(entries, list) else []


def _metrics_block(sprint_dir: Path, home: Home) -> Optional[Dict[str, Any]]:
    try:
        m = home.read_json(*sprint_dir.relative_to(home.root).parts,
                           "metrics.json")
    except LedgerError:
        return None
    block = m.get("reopened") if isinstance(m, dict) else None
    return block if isinstance(block, dict) else None


def pending(sprint_dir: Path, home: Home) -> Optional[Dict[str, Any]]:
    """The reopen of a sprint awaiting re-issue, else None.

    The latest reopened.json entry when the sprint has no issued.json; if
    that file is missing or unreadable, the `reopened` block analyze wrote
    into metrics.json, so removing reopened.json by hand cannot drop the
    correction from the next issue."""
    if (sprint_dir / "issued.json").exists():
        return None
    entries = _entries(sprint_dir, home)
    if entries:
        return entries[-1]
    block = _metrics_block(sprint_dir, home)
    if block is None:
        return None
    return {k: block.get(k) for k in ("edition", "as_of", "note",
                                      "reopened_at", "superseded")
            if block.get(k) is not None}


def analysed_since_reopen(sprint_dir: Path, home: Home,
                          entry: Dict[str, Any]) -> bool:
    block = _metrics_block(sprint_dir, home) or {}
    return (block.get("edition") == entry.get("edition")
            and block.get("as_of") == entry.get("as_of"))


def require_reanalysed(home: Home, team_sprints: List[Dict[str, str]]
                       ) -> None:
    """Refuse a report or issue that includes a reopened sprint whose
    metrics were not recomputed since the reopen."""
    for ts in team_sprints:
        d = home.team_sprint_dir(ts["team"], ts["sprint_id"])
        entry = pending(d, home)
        if entry is not None and not analysed_since_reopen(d, home, entry):
            raise LedgerError(
                "Sprint %s of %s was reopened but not analysed since."
                % (ts["sprint_id"], ts["team"]),
                "Run `ledger analyze --team %s` first." % ts["team"])


def superseded_dir(sprint_dir: Path, entry: Dict[str, Any]) -> Path:
    return sprint_dir / SUPERSEDED / entry.get("superseded", "%03d" % int(
        entry.get("edition") or 0))


def _lock(root: Path) -> None:
    for d, dirs, files in os.walk(str(root), topdown=False):
        for f in files:
            os.chmod(os.path.join(d, f), 0o400)
        os.chmod(d, 0o500)


def reopen(home: Home, team: str, sprint_id: str, note: str) -> Dict[str, Any]:
    d = home.team_sprint_dir(team, sprint_id)
    parts = d.relative_to(home.root).parts
    note = (note or "").strip()
    if not note:
        raise LedgerError("reopen needs a note saying what is being "
                          "corrected and why.",
                          "Pass --note \"WHAT AND WHY\".")
    issued = home.read_json(*parts, "issued.json")
    if not isinstance(issued, dict) or issued.get("edition") is None:
        raise LedgerError(
            "Sprint %s of %s has not been issued." % (sprint_id, team),
            "Only an issued sprint is reopened; run `ledger analyze` to "
            "recompute a sprint that was never issued.")
    _require_latest_collected(home, team, sprint_id, d)
    edition = int(issued["edition"])
    as_of = str(issued.get("as_of", ""))
    sources = _sources(d)
    want = {rel: _sha(p) for rel, p in sources.items()}
    taken = {e.get("superseded") or "%03d" % int(e.get("edition") or 0)
             for e in _entries(d, home)}
    target = None
    for name in ("%03d" % edition, "%s-%03d" % (as_of, edition)):
        cand = d / SUPERSEDED / name
        if not cand.exists():
            target = target or cand
            continue
        if name not in taken and _tree_hashes(cand) == want:
            target = cand  # left by a reopen that failed after copying
            break
    if target is None:
        raise LedgerError("Superseded copies of edition %03d already exist "
                          "in %s." % (edition, d / SUPERSEDED),
                          "Superseded copies are never overwritten; check "
                          "the sprint folder.")
    name = target.name
    if not target.exists():
        home._mkdirs(target.parent)
        _copy(d, target, sources, want, sprint_id, team)
    entries: List[Dict] = _entries(d, home)
    entry = {"edition": edition, "as_of": as_of,
             "reopened_at": datetime.now(timezone.utc).strftime(
                 "%Y-%m-%dT%H:%M:%SZ"),
             "note": note}
    if name != "%03d" % edition:
        entry["superseded"] = name
    entries.append(entry)
    try:
        home.write_json(entries, *parts, REOPENED)
    except OSError as e:
        raise LedgerError(
            "Reopening %s of %s failed while writing %s: %s"
            % (sprint_id, team, REOPENED, e),
            "The sprint is still issued. Fix the cause and run `ledger "
            "reopen` again; the superseded copy is reused.") from e
    os.unlink(str(d / "issued.json"))
    return dict(entry, superseded_path=str(target))


def _finish(sprint_dir: Path) -> Optional[str]:
    """The sprint's finish date from its metrics, else from its own
    snapshot's sprint calendar; None when neither is readable."""
    import json
    try:
        m = json.loads((sprint_dir / "metrics.json").read_text("utf-8"))
        return str(m["sprint"]["finish"])[:10]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        cal = json.loads((sprint_dir / "snapshot" / "sprints.json")
                         .read_text("utf-8"))
        for sprints in cal.values():
            for s in sprints:
                if s.get("id") == sprint_dir.name:
                    return str(s["end"])[:10]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def _require_latest_collected(home: Home, team: str, sprint_id: str,
                              d: Path) -> None:
    """Only a sprint that was collected itself, and that no newer sprint
    of the team has followed, can be reopened: re-analysing an older
    sprint would recompute it from a later snapshot as a backfilled one."""
    own = _finish(d)
    newer = []
    for other in sorted(d.parent.iterdir()):
        if other == d or not other.is_dir():
            continue
        if not ((other / "metrics.json").is_file()
                or (other / "snapshot" / "sprints.json").is_file()):
            continue
        fin = _finish(other)
        if own is None or fin is None or fin > own:
            newer.append(other.name if fin is None else _name(other))
    if newer:
        raise LedgerError(
            "Sprint %s of %s is not the team's latest sprint: %s has been "
            "collected or analysed since. Corrections to older sprints are "
            "not supported in this version."
            % (sprint_id, team, ", ".join(newer)),
            "Nothing was changed. Correct the latest sprint if the same "
            "mistake affects it; the issued edition of the older sprint "
            "stays as it is.")
    if not (d / "snapshot" / "sprints.json").is_file():
        raise LedgerError(
            "Sprint %s of %s has no collected snapshot of its own (it was "
            "computed from a later collection), so it cannot be reopened."
            % (sprint_id, team),
            "Nothing was changed. Only a sprint that was collected when it "
            "ended can be corrected.")


def _name(sprint_dir: Path) -> str:
    import json
    try:
        return str(json.loads((sprint_dir / "metrics.json").read_text(
            "utf-8"))["sprint"]["name"])
    except (OSError, ValueError, KeyError, TypeError):
        return sprint_dir.name


def _sources(d: Path) -> Dict[str, Path]:
    out = {f: d / f for f in _COPIED if (d / f).is_file()}
    snap = d / "snapshot"
    if snap.is_dir():
        for p in sorted(snap.rglob("*")):
            if p.is_file():
                out[p.relative_to(d).as_posix()] = p
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hashes(root: Path) -> Dict[str, str]:
    return {p.relative_to(root).as_posix(): _sha(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


def _copy(d: Path, target: Path, sources: Dict[str, Path],
          want: Dict[str, str], sprint_id: str, team: str) -> None:
    """Copy the sources into target via a temp folder; verify every file's
    hash against its source before the copy is put in place."""
    tmp = target.parent / (".tmp-" + target.name)
    _unlock_remove(tmp)
    try:
        tmp.mkdir(mode=0o700)
        for rel, src in sources.items():
            (tmp / rel).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(str(src), str(tmp / rel))
        if _tree_hashes(tmp) != want:
            _unlock_remove(tmp)
            raise LedgerError(
                "The superseded copy of %s of %s did not match its source "
                "files; it was removed." % (sprint_id, team),
                "Nothing was changed; run `ledger reopen` again.")
        _lock(tmp)
        os.rename(str(tmp), str(target))
    except OSError as e:
        _unlock_remove(tmp)
        raise LedgerError("Reopening %s of %s failed: %s"
                          % (sprint_id, team, e),
                          "Nothing was changed; fix the cause and run "
                          "`ledger reopen` again.") from e


def _unlock_remove(path: Path) -> None:
    if path.exists():
        for d, dirs, _ in os.walk(str(path)):
            os.chmod(d, 0o700)
        shutil.rmtree(str(path), ignore_errors=True)


def figure_changes(old: Any, new: Any, prefix: str = "figures"
                   ) -> List[Dict[str, Any]]:
    """Dotted keys whose values differ between two figures dicts."""
    if isinstance(old, dict) and isinstance(new, dict):
        out: List[Dict[str, Any]] = []
        for k in sorted(set(old) | set(new), key=str):
            out += figure_changes(old.get(k), new.get(k),
                                  "%s.%s" % (prefix, k))
        return out
    return [] if old == new else [{"key": prefix, "old": old, "new": new}]


def _reopen_cmd(args, home: Home) -> Dict[str, Any]:
    from .config import load_config
    cfg = load_config(home)
    cfg.team(args.team)
    r = reopen(home, args.team, args.sprint, args.note or "")
    return {"summary": "Reopened sprint %s of %s (issued in edition %03d of "
                       "%s). The issued data is kept in %s; the edition is "
                       "untouched.\nNext: correct the config or roster or "
                       "record the late answers, run `ledger analyze --team "
                       "%s` (or `ledger collect --team %s --refresh` to read "
                       "the board again), then report and issue with --note."
                       % (args.sprint, args.team, r["edition"], r["as_of"],
                          r["superseded_path"], args.team, args.team),
            "team": args.team, "sprint_id": args.sprint,
            "edition": r["edition"], "as_of": r["as_of"],
            "reopened_at": r["reopened_at"],
            "superseded": r["superseded_path"]}


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "reopen", help="reopen an issued sprint for a recorded correction")
    p.add_argument("--team", required=True)
    p.add_argument("--sprint", required=True, help="sprint id")
    p.add_argument("--note", help="what is corrected and why (required)")
    p.set_defaults(func=_reopen_cmd)
