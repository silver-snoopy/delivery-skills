"""The collect command: discover, fetch and pseudonymise one team's tickets.

The only part of the tool that reads Azure DevOps on a real run. Collection is
resumable: a run file in the target sprint's snapshot directory records the
cut-off, the finished discovery queries and the finished tickets.
"""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit

from .. import __version__
from ..config import Config, Roster, Team, load_config, load_roster, validate
from ..errors import LedgerError
from ..home import Home, slug
from ..model import Sprint, Snapshot, Ticket, _dt_in
from ..workcal import WorkCalendar
from .client import (AdoClient, Malformed, NotFound, Unreadable,
                     clause_area_under,
                     clause_changed_since, clause_ever_assigned,
                     clause_states_not_in, clause_types, where_all, where_any)
from .pseudonym import (Pseudonymizer, load_or_create_key, ticket_text,
                        to_revision, to_squad_revision)

RUN_FILE = "collect-run.json"
PARTIAL = {"tickets": "tickets.partial.jsonl", "squad": "squad.partial.jsonl"}
TEST_TRANSPORT_ENV = "LEDGER_TEST_TRANSPORT"


# ---- sprint calendar -------------------------------------------------------

def sprint_calendar(client: AdoClient, team: Team, cal: WorkCalendar,
                    warnings: List[str]) -> List[Sprint]:
    dated = []
    for it in client.iterations(team.ado_team):
        if it["start"] is None or it["finish"] is None:
            warnings.append("Sprint %s has no start or finish date; skipped."
                            % (it.get("path") or it["name"]))
            continue
        dated.append(it)
    excluded = {x.strip().lower(): x for x in team.exclude_iterations}
    matched = set()
    spans = []
    for it in sorted(dated, key=lambda x: (x["start"], x["finish"])):
        path = it.get("path") or it["name"]
        hit = {path.lower(), it["name"].lower()} & set(excluded)
        if hit:
            matched |= hit
            continue
        start, end = cal.sprint_bounds(it["start"], it["finish"])
        spans.append((it["name"], path, start, end, it["start"],
                      it["finish"]))
    for key in sorted(set(excluded) - matched):
        warnings.append(
            "\"exclude_iterations\" of team %s lists %r, which matches no "
            "dated iteration of the team (by name or full path); the entry "
            "had no effect." % (team.name, excluded[key]))
    # An iteration that wholly contains two or more others is a release or
    # planning level, not a sprint.
    kept = []
    for x in spans:
        inside = [y for y in spans if y is not x
                  and x[2] <= y[2] and y[3] <= x[3]]
        if len(inside) >= 2:
            warnings.append(
                "Iteration %s (%s) contains %d other iterations of team %s, "
                "so it was not treated as a sprint. List it under "
                "\"exclude_iterations\" for that team in config.json to "
                "silence this warning." % (x[0], x[1], len(inside),
                                           team.name))
        else:
            kept.append(x)
    # Two sprints sharing one calendar day (one finishes on the day the
    # next starts) are consecutive: the shared day belongs to the later.
    kept = [list(x) for x in kept]
    pairs = []
    for i, a in enumerate(kept):
        for b in kept[i + 1:]:
            if not (a[2] < b[3] and b[2] < a[3]):
                continue
            shared = (min(a[5], b[5]) - max(a[4], b[4])).days + 1
            # only where the earlier sprint's end touches the later one's
            # start; an iteration lying inside another is not a boundary
            if shared <= 1 and a[4] < b[4] and a[5] <= b[5]:
                a[3] = b[2]
                warnings.append(
                    "Sprints %s and %s of team %s share the day %s; it was "
                    "counted in %s, and %s was taken to end when %s starts."
                    % (a[0], b[0], team.name, b[4].isoformat(), b[0], a[0],
                       b[0]))
            else:
                pairs.append("%s (%s) and %s (%s)" % (a[0], a[1], b[0],
                                                      b[1]))
    if pairs:
        raise LedgerError(
            "Iterations of team %s overlap in time, so sprint periods "
            "cannot be told apart: %s." % (team.name, "; ".join(pairs)),
            "List the iteration(s) that are not this team's sprints under "
            "\"exclude_iterations\" for the team in config.json (names or "
            "full paths), then run collect again.")
    out: List[Sprint] = []
    for name, path, start, end, _first, _last in kept:
        # the latest earlier end, never after the sprint's own start
        earlier = max((s.end for s in out), default=start)
        out.append(Sprint(team=team.name, id=slug(path), name=name,
                          path=path, start=start, end=end,
                          period_start=min(earlier, start)))
    return out


def target_and_window(sprints: List[Sprint], cutoff: datetime, n: int,
                      cal: WorkCalendar, team: str
                      ) -> Tuple[Sprint, List[Sprint]]:
    ended = [s for s in sprints if s.end <= cutoff]
    if not ended:
        later = sorted(s.end for s in sprints if s.end > cutoff)
        hint = ("The next sprint ends at %s." % later[0].astimezone(cal.tz)
                .strftime("%Y-%m-%d %H:%M") if later
                else "No dated sprint was found for this team.")
        raise LedgerError("no sprint of %s has ended yet" % team, hint)
    target = max(ended, key=lambda s: s.end)
    i = sprints.index(target)
    return target, sprints[max(0, i - (n - 1)):i + 1]


# ---- run file ----------------------------------------------------------------

class _Run:
    """The resumable state of one collection, saved after every step."""

    def __init__(self, home: Home, snap_dir: Path, data: Dict[str, Any]):
        self.home, self.dir, self.data = home, snap_dir, data

    def save(self) -> None:
        self.home.write_json(self.data, *(self.dir / RUN_FILE)
                             .relative_to(self.home.root).parts)

    def done(self, tid: int) -> None:
        self.data["done"].append(tid)
        self.save()

    def gap(self, kind: str, tid: int) -> None:
        self.data["coverage"].setdefault(kind, []).append(tid)
        self.done(tid)


def _existing_runs(home: Home, team: str) -> List[Path]:
    root = home.path("teams", slug(team), "sprints")
    return sorted(p.parent for p in root.glob("*/snapshot/" + RUN_FILE))


def _discard(snap_dir: Path) -> None:
    for name in [RUN_FILE] + list(PARTIAL.values()):
        try:
            os.unlink(str(snap_dir / name))
        except FileNotFoundError:
            pass


def _keep_finished_lines(snap_dir: Path, done: Set[int]) -> None:
    """Drop partial lines of unfinished tickets (and duplicates, torn lines)."""
    for name in PARTIAL.values():
        path = snap_dir / name
        if not path.exists():
            continue
        seen: Set[int] = set()
        keep = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                tid = json.loads(line)["id"]
            except (ValueError, KeyError, TypeError):
                continue
            if tid in done and tid not in seen:
                seen.add(tid)
                keep.append(line + "\n")
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(keep)
        os.replace(str(tmp), str(path))


def _append(snap_dir: Path, kind: str, ticket: Ticket) -> None:
    fd = os.open(str(snap_dir / PARTIAL[kind]),
                 os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(ticket.to_json(), ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())  # durable before the run file marks it done


def _read_partial(snap_dir: Path, kind: str) -> List[Ticket]:
    path = snap_dir / PARTIAL[kind]
    if not path.exists():
        return []
    return [Ticket.from_json(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ---- discovery ---------------------------------------------------------------

def done_and_removed_states(team: Team) -> List[str]:
    phases: Dict[str, Set[str]] = {}
    for rule in team.phases:
        phases.setdefault(rule.state, set()).add(rule.phase)
    return sorted(s for s, p in phases.items() if p <= {"done", "removed"})


def discovery_queries(cfg: Config, roster: Roster, team: Team,
                      window_start, closed: List[str],
                      warnings: List[str]) -> Tuple[Dict[str, str],
                                                    Dict[str, str]]:
    T = clause_types(cfg.unit_types)
    since = clause_changed_since(window_start)
    open_ = [clause_states_not_in(closed)] if closed else []
    if not closed:
        warnings.append("No finished state is configured for team %s (no "
                        "state maps only to done or removed), so every "
                        "ticket ever assigned will be fetched." % team.name)
    vendor: Dict[str, str] = {}
    for p in roster.people:
        if not any(a.team == team.name for a in p.assignments):
            continue
        if not p.aliases:
            warnings.append("Roster person %s has no aliases; their tickets "
                            "cannot be discovered." % p.id)
        for alias in p.aliases:
            ever = clause_ever_assigned(alias)
            vendor["unfinished:" + alias] = where_all(T, ever, *open_)
            vendor["recent:" + alias] = where_all(T, ever, since)
    squad: Dict[str, str] = {}
    for area in team.area_paths:
        recent = where_any(open_[0], since) if open_ else None
        squad["squad:" + area] = where_all(
            T, clause_area_under(area), *([recent] if recent else []))
    return vendor, squad


def run_query(client: AdoClient, run: _Run, key: str, where: str) -> Set[int]:
    parts = run.data["partitions"]
    if key in run.data["queries_done"]:
        return {i for ids in parts[key].values() for i in ids}
    parts[key] = {}  # an unfinished query is re-run in full

    def checkpoint(rng: Tuple[int, int], ids: List[int]) -> None:
        parts[key]["%d-%d" % rng] = ids
        run.save()

    found = set(client.wiql_ids(where, on_partition=checkpoint))
    run.data["queries_done"].append(key)
    run.save()
    return found


# ---- fetching ----------------------------------------------------------------

def _in_effect(raws: List[Dict], revs: list, at: datetime) -> Dict:
    before = [raw for raw, r in zip(raws, revs) if r.changed <= at]
    return before[-1] if before else raws[0]


_UNPARSABLE = (KeyError, ValueError, TypeError, AttributeError, LedgerError)
ALARM_SHARE = 0.2
ALARM_MIN = 5


def _reason(e: Exception) -> str:
    """Why a revision could not be parsed, without any ticket content."""
    if isinstance(e, KeyError):
        return "a field is missing"
    return type(e).__name__


class _Fetcher:
    def __init__(self, client: AdoClient, team: Team, pseud: Pseudonymizer,
                 cutoff: datetime, text_at: datetime):
        self.client, self.team, self.pseud = client, team, pseud
        self.cutoff, self.text_at = cutoff, text_at
        self._parent_titles: Dict[int, str] = {}

    def history(self, tid: int) -> Tuple[List[Dict], list]:
        """Raw revisions up to the cut-off, with their structural form."""
        raws = self.client.revisions(tid)
        try:
            pairs = [(raw, to_squad_revision(raw, self.team))
                     for raw in raws]
        except _UNPARSABLE as e:
            raise Malformed(tid, "a revision could not be parsed (%s)"
                            % _reason(e))
        kept = [(raw, r) for raw, r in pairs if r.changed <= self.cutoff]
        return [p[0] for p in kept], [p[1] for p in kept]

    def parent_title(self, pid: Optional[int]) -> str:
        if pid is None:
            return ""
        if pid not in self._parent_titles:
            try:
                raws, revs = self.history(pid)
                title = (ticket_text(_in_effect(raws, revs, self.text_at))
                         ["title"] if raws else "")
            except (NotFound, Unreadable, Malformed):
                title = ""
            self._parent_titles[pid] = title
        return self._parent_titles[pid]

    def vendor_ticket(self, tid: int, raws: List[Dict], revs: list) -> Ticket:
        try:
            raw = _in_effect(raws, revs, self.text_at)
            parent = raw["fields"].get("System.Parent")
            parent = None if parent is None else int(parent)
            revisions = [to_revision(r, self.pseud, self.team) for r in raws]
            text = ticket_text(raw)
        except _UNPARSABLE as e:
            raise Malformed(tid, "a revision could not be parsed (%s)"
                            % _reason(e))
        return Ticket(id=tid, revisions=revisions,
                      parent_title=self.parent_title(parent), **text)


def fetch_all(fetcher: _Fetcher, run: _Run, vendor: List[int],
              squad: List[int], warnings: Optional[List[str]] = None) -> int:
    """Fetch and store every ticket; returns how many were attempted in
    this and earlier runs of the same collection."""
    vendor_set, squad_set = set(vendor), set(squad)
    finished = set(run.data["done"])
    order = vendor + [i for i in squad if i not in vendor_set]
    for tid in order:
        if tid in finished:
            continue
        try:
            raws, revs = fetcher.history(tid)
            ticket = fetcher.vendor_ticket(tid, raws, revs) \
                if raws and tid in vendor_set else None
        except NotFound:
            run.gap("missing", tid)
            continue
        except Unreadable:
            run.gap("unreadable", tid)
            continue
        except Malformed as e:
            if warnings is not None:
                warnings.append("%s It is listed under coverage as "
                                "malformed and is in no figure." % e.message)
            run.gap("malformed", tid)
            continue
        if raws:
            if ticket is not None:
                _append(run.dir, "tickets", ticket)
            if tid in squad_set:
                _append(run.dir, "squad", Ticket(id=tid, revisions=revs))
        run.done(tid)
    return len(order)


def coverage_alarm(coverage: Dict[str, List[int]], attempted: int) -> bool:
    """True when more than a fifth of the tickets (and at least five)
    could not be collected: the figures would rest on a partial board."""
    gaps = sum(len(coverage.get(k) or []) for k in (
        "missing", "unreadable", "malformed"))
    return gaps >= ALARM_MIN and attempted > 0 \
        and gaps / attempted > ALARM_SHARE


# ---- finishing -----------------------------------------------------------------

def finish(home: Home, run: _Run, team: Team, sprints: List[Sprint],
           closed: List[str], warnings: List[str],
           alarm: bool = False) -> Snapshot:
    d = run.data
    d["coverage"].setdefault("malformed", [])
    meta = {"cutoff": d["cutoff"], "account": d["account"]["name"],
            "auth_mode": d["auth_mode"], "coverage": d["coverage"],
            "coverage_alarm": alarm,
            "tool_version": __version__, "team": team.name,
            "warnings": warnings}
    snap = Snapshot(tickets=_read_partial(run.dir, "tickets"),
                    squad=_read_partial(run.dir, "squad"),
                    sprints={team.name: sprints}, meta=meta)
    snap.save(run.dir)
    inventory = home.read_json("inventory.json", default={}) or {}
    inventory[team.name] = sorted(t.id for t in snap.tickets
                                  if t.revisions[-1].state not in closed)
    home.write_json(inventory, "inventory.json")
    _discard(run.dir)
    return snap


# ---- the procedure ---------------------------------------------------------------

def _open_run(home: Home, client: AdoClient, team: Team, now: datetime,
              refresh: bool, auth_mode: str) -> Tuple[Dict[str, Any], bool]:
    """Run-file data to continue (resumed) or a fresh one; before targeting."""
    existing = _existing_runs(home, team.name)
    account = client.connection()
    if refresh:
        for d in existing:
            _discard(d)
        existing = []
    if len(existing) > 1:
        raise LedgerError(
            "There are %d unfinished collections of %s."
            % (len(existing), team.name),
            "Unfinished runs in sprint folders: %s. Start over with "
            "--refresh." % ", ".join(d.parent.name for d in existing))
    if existing:
        data = json.loads((existing[0] / RUN_FILE).read_text("utf-8"))
        if data["account"]["id"] != account["id"]:
            raise LedgerError(
                "An unfinished collection of %s was started with a different "
                "account (%s)." % (team.name, data["account"]["name"]),
                "Finish it with the original account, or start over with "
                "--refresh.")
        return data, True
    return {"cutoff": now.astimezone(timezone.utc).isoformat(),
            "account": {"id": account["id"], "name": account["name"]},
            "auth_mode": auth_mode, "partitions": {}, "queries_done": [],
            "done": [], "coverage": {"missing": [], "unreadable": [],
                                     "malformed": []}}, False


def _warn_if_stale(sprints: List[Sprint], now: datetime, target: Sprint,
                   local_cutoff: str, cfg: Config, cal: WorkCalendar,
                   warnings: List[str]) -> None:
    """A resumed run keeps its cut-off; say so when a newer sprint has ended."""
    try:
        latest, _ = target_and_window(sprints, now, 1, cal, target.team)
    except LedgerError:
        return
    if latest.end > target.end:
        warnings.append(
            "This resumed collection finished sprint %s with its original "
            "cut-off %s; sprint %s has ended since. Run `collect` again to "
            "collect %s." % (target.name, local_cutoff, latest.name,
                             latest.name))


def _warn_reopened_elsewhere(home: Home, sprints: List[Sprint],
                             target: Sprint, warnings: List[str]) -> None:
    """collect always targets the latest ended sprint; a reopened older one
    can be re-analysed but not re-collected."""
    from ..reopen import pending
    for s in sprints:
        if s.id != target.id and \
                pending(home.team_sprint_dir(s.team, s.id), home) is not None:
            warnings.append(
                "Sprint %s is reopened but is not the latest ended sprint "
                "(%s); collect only collects the latest, so %s can be "
                "re-analysed with `ledger analyze --team %s` but not "
                "re-collected." % (s.name, target.name, s.name, s.team))


def collect_team(home: Home, cfg: Config, roster: Roster, client: AdoClient,
                 team: str, now: datetime, refresh: bool = False,
                 auth_mode: str = "unknown") -> Dict[str, Any]:
    started = time.monotonic()
    tm = cfg.team(team)
    cal = WorkCalendar(cfg.timezone, cfg.holidays)
    warnings: List[str] = []
    sprints = sprint_calendar(client, tm, cal, warnings)
    data, resumed = _open_run(home, client, tm, now, refresh, auth_mode)
    cutoff = _dt_in(data["cutoff"])
    target, window = target_and_window(
        sprints, cutoff, int(cfg.thresholds["window_sprints"]), cal, team)
    snap_dir = home.team_sprint_dir(team, target.id) / "snapshot"
    if resumed and not (snap_dir / RUN_FILE).exists():
        raise LedgerError(
            "The unfinished collection of %s no longer matches the team's "
            "sprints in Azure DevOps." % team, "Start over with --refresh.")
    local_cutoff = cutoff.astimezone(cal.tz).strftime("%Y-%m-%d %H:%M")
    issued = home.read_json(*snap_dir.parent.relative_to(home.root).parts,
                            "issued.json")
    _warn_reopened_elsewhere(home, sprints, target, warnings)
    if issued is not None:
        # An issued edition holds hashes of this snapshot; never write there.
        if resumed:
            warnings.append(
                "An unfinished collection run file %s was left in issued "
                "sprint %s; it was not resumed. Discard it with `collect "
                "--team %s --refresh`." % (snap_dir / RUN_FILE, target.name,
                                           team))
        summary = ("%s: sprint %s was issued in edition %03d of %s; its "
                   "snapshot is kept and nothing was collected. An issued "
                   "sprint can be corrected with `ledger reopen`."
                   % (team, target.name, int(issued.get("edition", 0)),
                      issued.get("as_of", "?")))
        return {"summary": "\n".join([summary] + ["  warning: " + w
                                                   for w in warnings]),
                "team": team,
                "sprint": {"id": target.id, "name": target.name},
                "skipped": "issued", "vendor_tickets": 0, "squad_tickets": 0,
                "missing": 0, "unreadable": 0, "malformed": 0,
                "coverage_alarm": False, "warnings": warnings,
                "resumed": False, "elapsed_seconds":
                    round(time.monotonic() - started, 1)}
    if resumed:
        _warn_if_stale(sprints, now, target, local_cutoff, cfg, cal, warnings)
    run = _Run(home, snap_dir, data)
    if not resumed:
        _discard(snap_dir)
    run.save()
    _keep_finished_lines(snap_dir, set(data["done"]))

    closed = done_and_removed_states(tm)
    window_start = cal.local_date(window[0].start)
    vq, sq = discovery_queries(cfg, roster, tm, window_start, closed,
                               warnings)
    vendor: Set[int] = set((home.read_json("inventory.json", default={})
                            or {}).get(team, []))
    for key, where in vq.items():
        vendor |= run_query(client, run, key, where)
    squad: Set[int] = set()
    for key, where in sq.items():
        squad |= run_query(client, run, key, where)

    pseud = Pseudonymizer(load_or_create_key(home), roster,
                          cfg.service_identities)
    attempted = fetch_all(_Fetcher(client, tm, pseud, cutoff, target.end),
                          run, sorted(vendor), sorted(squad), warnings)
    cov = data["coverage"]
    cov.setdefault("malformed", [])
    alarm = coverage_alarm(cov, attempted)
    gaps = len(cov["missing"]) + len(cov["unreadable"]) + len(cov["malformed"])
    alarm_line = ("COVERAGE ALARM: %d of %d tickets of %s could not be "
                  "collected (%d missing, %d unreadable, %d malformed). The "
                  "figures for this team would rest on a partial board; "
                  "check the signed-in account's access before analysing."
                  % (gaps, attempted, team, len(cov["missing"]),
                     len(cov["unreadable"]), len(cov["malformed"])))
    if alarm:
        warnings.insert(0, alarm_line)
    snap = finish(home, run, tm, sprints, closed, warnings, alarm)

    elapsed = time.monotonic() - started
    summary = ("%s: collected sprint %s as of %s%s; %d vendor ticket(s), %d squad "
               "ticket(s); %d missing, %d unreadable, %d malformed; "
               "%d warning(s); %.1f s"
               % (team, target.name, local_cutoff, " (resumed)" if resumed else "",
                  len(snap.tickets), len(snap.squad), len(cov["missing"]),
                  len(cov["unreadable"]), len(cov["malformed"]),
                  len(warnings), elapsed))
    return {"summary": "\n".join([summary] + ["  warning: " + w
                                               for w in warnings]),
            "team": team, "sprint": {"id": target.id, "name": target.name},
            "vendor_tickets": len(snap.tickets),
            "squad_tickets": len(snap.squad),
            "missing": len(cov["missing"]),
            "unreadable": len(cov["unreadable"]),
            "malformed": len(cov["malformed"]),
            "coverage_alarm": alarm,
            "warnings": warnings, "resumed": resumed,
            "elapsed_seconds": round(elapsed, 1)}


# ---- command -----------------------------------------------------------------

def build_transport(args, org_url: str) -> Tuple[Any, str]:
    name = os.environ.get(TEST_TRANSPORT_ENV)
    if name:
        from ..testing import fake_ado
        if name not in fake_ado.REGISTRY:
            raise LedgerError("Unknown test transport %r." % name,
                              "Unset %s." % TEST_TRANSPORT_ENV)
        return fake_ado.REGISTRY[name], "test"
    from .auth import AzTokenAuth, PatAuth
    from .transport import AzRestTransport, PacedTransport, UrllibTransport
    host = urlsplit(org_url).hostname or ""
    if args.transport == "az-rest":
        return PacedTransport(AzRestTransport(host)), "az"
    if args.auth == "pat":
        pat = os.environ.get("AZURE_DEVOPS_EXT_PAT")
        if not pat:
            raise LedgerError("No personal access token found.",
                              "Set AZURE_DEVOPS_EXT_PAT, or use --auth az.")
        auth = PatAuth(pat)
    else:
        auth = AzTokenAuth()
    return PacedTransport(UrllibTransport(auth, host)), args.auth


def _as_of(text: Optional[str]) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise LedgerError("--as-of %r is not an ISO date-time." % text,
                          "Use for example 2026-11-24T08:00:00Z.")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _collect_cmd(args, home: Home) -> Dict[str, Any]:
    cfg, roster = load_config(home), load_roster(home)
    validate(cfg, roster)
    if not args.all and not args.team:
        raise LedgerError("collect needs --team or --all.",
                          "Name a team from config.json, or pass --all.")
    now = _as_of(args.as_of)
    transport, mode = build_transport(args, cfg.org_url)
    client = AdoClient(transport, cfg.org_url, cfg.project)
    names = [t.name for t in cfg.teams] if args.all else [args.team]
    results: List[Dict[str, Any]] = []
    for name in names:
        try:
            results.append(collect_team(home, cfg, roster, client, name, now,
                                        refresh=args.refresh,
                                        auth_mode=mode))
        except LedgerError as e:
            if not args.all:
                raise
            done = ", ".join(r["team"] for r in results) or "none"
            raise LedgerError("Collecting %s failed: %s (completed: %s)"
                              % (name, e.message, done), e.hint)
    if not args.all:
        return results[0]
    return {"summary": "\n".join(r["summary"] for r in results),
            "teams": results,
            "coverage_alarm": any(r.get("coverage_alarm") for r in results)}


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("collect", help="read ticket history from ADO")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--team")
    group.add_argument("--all", action="store_true", help="every team")
    p.add_argument("--refresh", action="store_true",
                   help="discard an unfinished collection and start over")
    p.add_argument("--transport", choices=("urllib", "az-rest"),
                   default="urllib")
    p.add_argument("--auth", choices=("az", "pat"), default="az")
    p.add_argument("--as-of", dest="as_of",
                   help="collect as of this ISO date-time (default: now)")
    p.set_defaults(func=_collect_cmd)
