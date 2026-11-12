"""Probe: check on the customer machine the ADO assumptions the tool relies on.

`run_probe` runs one function per check and writes a full local report.
The shareable export is built by ledger.ado.probe_export from an allowlist.
"""
import argparse
import os
import re
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from .. import __version__
from ..errors import LedgerError
from ..home import Home
from .probe_export import (CHECK_IDS, DETAIL_CODES, STATUSES,  # noqa: F401
                           export, export_markdown)
from .client import (AdoClient, NotFound, Unreadable, clause_area_under,
                     clause_changed_since, clause_ever_assigned,
                     clause_states_not_in, clause_types, where_all,
                     where_any)
from .transport import TransportError

SAMPLE_CANDIDATES = 20
SAMPLE_TICKETS = 3
A4_SAMPLE = 25
WINDOW_SPRINTS = 6
WINDOW_DAYS = 84
DEFAULT_TYPES = ("User Story", "Product Backlog Item", "Bug")
DESCRIPTION = "System.Description"
ACCEPTANCE = "Microsoft.VSTS.Common.AcceptanceCriteria"


class _Recorder:
    """Passes requests through and remembers response headers (for A10)."""

    def __init__(self, inner):
        self.inner = inner
        self.headers: List[Dict[str, str]] = []

    def request(self, method, url, body=None):
        resp = self.inner.request(method, url, body)
        self.headers.append({str(k).lower(): v for k, v in
                             (resp.headers or {}).items()})
        return resp


def _result(status: str, code: str, detail: str, sample: int = 0) -> Dict:
    return {"status": status, "code": code, "detail": detail,
            "sample": sample}


def _ok(detail: str, sample: int = 0) -> Dict:
    return _result("observed", "ok", detail, sample)


def _fail(code: str, detail: str, sample: int = 0) -> Dict:
    return _result("failed", code, detail, sample)


def _skip(code: str, detail: str) -> Dict:
    return _result("not checked", code, detail)


def _exception_code(exc: BaseException) -> str:
    if isinstance(exc, TransportError):
        return "transport_error"
    if isinstance(exc, (NotFound, Unreadable)):
        return "http_error"
    if isinstance(exc, LedgerError):
        return "http_error" if re.search(r"HTTP \d+", exc.message) \
            else "ado_error"
    return "exception"


def _load_zone(name: Optional[str]) -> Dict:
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(name or "")
    except Exception as exc:
        return _fail("unsupported", "timezone %r did not load: %s"
                     % (name, exc))
    return _ok("timezone %s loads" % name)


def _field_values(ctx, name: str) -> List[Any]:
    return [r.get("fields", {}).get(name) for revs in ctx["revs"].values()
            for r in revs if name in r.get("fields", {})]


# ---------------------------------------------------------------- checks

def check_env_python(ctx) -> Dict:
    v = sys.version_info
    text = "Python %d.%d.%d" % (v[0], v[1], v[2])
    return _ok(text) if v >= (3, 9) else _fail("unsupported", text + " < 3.9")


def check_env_timezone(ctx) -> Dict:
    return _load_zone(ctx["timezone"])


def check_env_home(ctx) -> Dict:
    home: Home = ctx["home"]
    real = os.path.realpath(str(home.root))
    if real == "/mnt" or real.startswith("/mnt/"):
        return _fail("unsupported", "data home %s is on a Windows mount" % real)
    probe_dir = home.path("probe")
    probe_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = probe_dir / (".mode-check-%d" % os.getpid())
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        os.chmod(str(tmp), 0o600)
        mode = os.stat(str(tmp)).st_mode & 0o777
    finally:
        if tmp.exists():
            tmp.unlink()
    wsl = "WSL" if ctx["env"].get("WSL_DISTRO_NAME") else "not WSL"
    if mode != 0o600:
        return _fail("unsupported", "file mode reads back as %o (%s)"
                     % (mode, wsl))
    return _ok("data home %s, mode 0600 effective (%s)" % (real, wsl))


def _find_auth(transport) -> Any:
    for _ in range(6):
        if transport is None:
            return None
        if getattr(transport, "auth", None) is not None:
            return transport.auth
        transport = getattr(transport, "inner", None)
    return None


def check_a1(ctx) -> Dict:
    try:
        client = ctx["client_for"]("urllib")
    except LedgerError as exc:
        return _skip("unsupported", "urllib transport unavailable: %s"
                     % exc.message)
    auth = _find_auth(client.transport)
    if auth is None or getattr(auth, "mode", None) != "az":
        return _skip("unsupported", "no az token authentication in use")
    present = auth.expiry_fields()
    if not present:
        return _fail("missing_field", "token response has no expiry field")
    return _ok("token issued; expiry fields: %s" % ", ".join(present), 1)


def check_a2(ctx) -> Dict:
    notes, ok = [], {}
    for kind in ("urllib", "az-rest"):
        try:
            client = ctx["client_for"](kind)
            client.connection()
            ok[kind] = True
            notes.append("%s: reached connectionData" % kind)
            if "client" not in ctx:
                ctx["recorder"] = _Recorder(client.transport)
                client.transport = ctx["recorder"]
                ctx["client"] = client
        except Exception as exc:
            ok[kind] = False
            notes.append("%s: %s" % (kind, getattr(exc, "message", exc)))
    ctx["counts"]["transport_urllib_ok"] = ok["urllib"]
    ctx["counts"]["transport_az_rest_ok"] = ok["az-rest"]
    detail = "; ".join(notes)
    if any(ok.values()):
        return _ok(detail, 2)
    return _fail("transport_error", detail, 2)


def check_a3(ctx) -> Dict:
    if not ctx["alias"]:
        return _skip("no_sample", "no --alias given")
    ids = ctx["client"].wiql_ids(clause_ever_assigned(ctx["alias"]))
    ctx["a3_ids"] = ids
    ctx["counts"]["wiql_ids"] = len(ids)
    if not ids:
        return _fail("empty", "EVER query for %s returned no ids"
                     % ctx["alias"])
    return _ok("EVER query for %s returned %d ids" % (ctx["alias"], len(ids)),
               len(ids))


def check_a4(ctx) -> Dict:
    """Partitioned against unpartitioned results, on a sample: splitting
    down to two ids costs about three queries per ticket, so the whole
    result is never re-enumerated."""
    where = clause_ever_assigned(ctx["alias"])
    sample = sorted(ctx["a3_ids"])[:A4_SAMPLE]
    done = {"n": 0, "ids": 0}

    def on_partition(rng, ids) -> None:
        done["n"] += 1
        done["ids"] += len(ids)
        if done["n"] % 10 == 0:
            ctx["progress"]("A4: %d partition(s) checked, %d of %d id(s) "
                            "found so far" % (done["n"], done["ids"],
                                              len(sample)))

    ctx["progress"]("A4: comparing partitioned queries on a sample of %d "
                    "id(s)" % len(sample))
    small = ctx["client"].wiql_ids_in(where, (sample[0], sample[-1] + 1),
                                      batch_limit=2,
                                      on_partition=on_partition)
    if small != sample:
        return _fail("mismatch", "partitioned query returned %d ids, "
                     "unpartitioned %d (sample of %d)"
                     % (len(small), len(sample), len(sample)), len(small))
    return _ok("partitioned (batch_limit=2) and unpartitioned results agree "
               "on a sample of %d ids" % len(sample), len(small))


def _candidates(ctx) -> List[int]:
    return list(ctx["ticket_ids"]) or list(ctx.get("a3_ids") or [])


def check_a5(ctx) -> Dict:
    client: AdoClient = ctx["client"]
    candidates = _candidates(ctx)
    if not candidates:
        return _skip("no_sample", "no tickets given or found")
    full: Dict[int, List[Dict]] = {}
    skipped = []
    for tid in candidates[:SAMPLE_CANDIDATES]:
        try:
            full[tid] = client.revisions(tid)
        except (NotFound, Unreadable) as exc:
            skipped.append(exc.message)
    chosen = sorted(full, key=lambda t: -len(full[t]))
    if not ctx["ticket_ids"]:
        chosen = chosen[:SAMPLE_TICKETS]
    if not chosen:
        return _fail("no_sample", "no readable ticket: %s" % "; ".join(skipped))
    for tid in chosen:
        try:
            paged = client.revisions(tid, page=2)
        except LedgerError as exc:
            return _fail("not_contiguous", exc.message, len(chosen))
        if [r.get("rev") for r in paged] != [r.get("rev") for r in full[tid]]:
            return _fail("not_contiguous", "ticket %d: page size 2 returned "
                         "different revisions" % tid, len(chosen))
    ctx["revs"] = {tid: full[tid] for tid in chosen}
    ctx["counts"]["tickets_sampled"] = len(chosen)
    ctx["counts"]["revisions_sampled"] = sum(len(v) for v in
                                             ctx["revs"].values())
    for tid, revs in ctx["revs"].items():
        ctx["shapes"]["revisions"].update(
            k for r in revs for k in r.get("fields", {}))
    missing = [f for f in ("System.State", "System.IterationPath",
                           "System.AssignedTo", "System.BoardColumn")
               if not _field_values(ctx, f)]
    if missing:
        return _fail("missing_field", "absent in every revision: %s"
                     % ", ".join(missing), len(chosen))
    return _ok("tickets %s contiguous with page size 2"
               % ", ".join(str(t) for t in chosen), len(chosen))


def check_a6(ctx) -> Dict:
    idents = [v for f in ("System.AssignedTo", "System.ChangedBy")
              for v in _field_values(ctx, f)]
    ctx["counts"]["identities"] = len(idents)
    if not idents:
        return _fail("empty", "no identity values in the sample")
    bad = [v for v in idents if not (isinstance(v, dict) and v.get("id"))]
    if bad:
        return _fail("missing_field", "%d of %d identity values lack an id"
                     % (len(bad), len(idents)), len(idents))
    return _ok("all %d identity values carry an id" % len(idents), len(idents))


def check_a7(ctx) -> Dict:
    its = ctx["client"].iterations(ctx["team"])
    ctx["iterations"] = its
    dated = [i for i in its if i["start"] and i["finish"]]
    ctx["counts"]["iterations_total"] = len(its)
    ctx["counts"]["iterations_dated"] = len(dated)
    detail = "%d of %d iterations have start and finish dates" % (
        len(dated), len(its))
    return _ok(detail, len(its)) if dated else _fail("empty", detail, len(its))


def check_a8(ctx) -> Dict:
    multi = [tid for tid, revs in ctx["revs"].items()
             if len({r["fields"].get("System.BoardColumn") for r in revs
                     if r.get("fields", {}).get("System.BoardColumn")}) > 1]
    if not multi:
        return _fail("empty", "no sampled ticket changed board column",
                     len(ctx["revs"]))
    return _ok("tickets with column changes: %s"
               % ", ".join(str(t) for t in multi), len(ctx["revs"]))


def check_a9(ctx) -> Dict:
    missing = [f for f in (DESCRIPTION, ACCEPTANCE)
               if not _field_values(ctx, f)]
    if missing:
        return _fail("missing_field", "absent: %s" % ", ".join(missing),
                     len(ctx["revs"]))
    return _ok("description and acceptance criteria present",
               len(ctx["revs"]))


def check_a10(ctx) -> Dict:
    candidates = _candidates(ctx)
    if not candidates:
        return _skip("no_sample", "no tickets given or found")
    n = min(10, len(candidates))
    rec: _Recorder = ctx["recorder"]
    before = len(rec.headers)
    start = ctx["now"]()
    for tid in candidates[:n]:
        try:
            ctx["client"].revisions(tid)
        except (NotFound, Unreadable):
            pass
    seconds = ctx["now"]() - start
    seen = rec.headers[before:]
    names = [k for h in seen for k in h
             if k.startswith("x-ratelimit") or k == "retry-after"]
    ctx["timing"].update(revision_fetches=n, seconds=round(seconds, 3),
                         retry_after_seen="retry-after" in names,
                         rate_limit_headers=len(names))
    return _ok("%d revision fetches in %.3f s; rate-limit headers: %s"
               % (n, seconds, ", ".join(sorted(set(names))) or "none"), n)


def check_a11(ctx) -> Dict:
    client: AdoClient = ctx["client"]
    wits = sorted({v for v in _field_values(ctx, "System.WorkItemType")
                   if isinstance(v, str)} if "revs" in ctx else set()) \
        or ["User Story"]
    states = {w: client.states(w) for w in wits}
    ctx["states"] = states
    boards = client.board_columns(ctx["team"])
    ctx["board_columns"] = boards
    for b in boards:
        ctx["shapes"]["boards"].update(
            f for f in (b["column_field"], b["done_field"]) if f)
    ncols = sum(len(b["columns"]) for b in boards)
    ctx["counts"]["board_columns"] = ncols
    nstates = sum(len(v) for v in states.values())
    if not nstates or not ncols:
        return _fail("empty", "%d states, %d board columns" % (nstates, ncols),
                     nstates)
    return _ok("%d states over %s; %d board columns"
               % (nstates, ", ".join(wits), ncols), nstates)


def check_a12(ctx) -> Dict:
    parents = _field_values(ctx, "System.Parent")
    if not parents:
        return _fail("missing_field", "System.Parent absent in the sample",
                     len(ctx["revs"]))
    return _ok("System.Parent present in %d revisions" % len(parents),
               len(ctx["revs"]))


def check_a13(ctx) -> Dict:
    if "states" not in ctx:
        return _skip("dependency_failed", "work item states were not read")
    done = {s["name"] for v in ctx["states"].values() for s in v
            if s.get("category") == "Completed"}
    revs = [r["fields"] for v in ctx["revs"].values() for r in v
            if r.get("fields", {}).get("System.State") in done]
    reasons = sorted({str(f.get("System.Reason")) for f in revs
                      if f.get("System.Reason")})
    ctx["reasons"] = reasons
    ctx["counts"]["done_reasons"] = len(reasons)
    if not revs:
        return _fail("no_sample", "no done-state revisions in the sample")
    return _ok("reasons on done states: %s" % (", ".join(reasons) or "none"),
               len(revs))


def check_a14(ctx) -> Dict:
    return _load_zone(ctx["timezone"])


def check_a15(ctx) -> Dict:
    """Sprint membership compares a revision's System.IterationPath with
    the path the team settings give for an iteration; they must be the
    same string."""
    team_paths = [i["path"] for i in ctx.get("iterations") or []
                  if i.get("path")]
    seen = [v for v in _field_values(ctx, "System.IterationPath")
            if isinstance(v, str) and v]
    same = sorted(set(team_paths) & set(seen))
    if same:
        return _ok("%d team iteration path(s) appear as System.IterationPath "
                   "in the sampled revisions" % len(same), len(seen))
    return _fail(
        "mismatch", "no sampled revision's System.IterationPath equals a "
        "team iteration path; team settings give for example \"%s\", "
        "revisions give for example \"%s\""
        % (team_paths[0] if team_paths else "(none)",
           seen[0] if seen else "(none)"), len(seen))


def _window_start(ctx):
    from datetime import timedelta
    today = ctx["today"]
    ended = sorted(i["start"] for i in ctx.get("iterations") or []
                   if i.get("start") and i.get("finish")
                   and i["finish"] < today)
    if ended:
        return ended[-WINDOW_SPRINTS:][0]
    return today - timedelta(days=WINDOW_DAYS)


def check_a16(ctx) -> Dict:
    """The discovery queries collection will send, counted and timed;
    no ticket history is fetched."""
    if not ctx["alias"]:
        return _skip("no_sample", "no --alias given")
    client: AdoClient = ctx["client"]
    closed = ctx["closed_states"]
    if closed is None:
        closed = sorted({s["name"] for v in (ctx.get("states") or {}).values()
                         for s in v
                         if s.get("category") in ("Completed", "Removed")})
    types = clause_types(ctx["unit_types"])
    ever = clause_ever_assigned(ctx["alias"])
    since = clause_changed_since(_window_start(ctx))
    open_ = [clause_states_not_in(closed)] if closed else []
    queries = [("vendor_unfinished", where_all(types, ever, *open_)),
               ("vendor_recent", where_all(types, ever, since))]
    if ctx["area"]:
        recent = where_any(open_[0], since) if open_ else None
        queries.append(("squad", where_all(
            types, clause_area_under(ctx["area"]),
            *([recent] if recent else []))))
    parts = []
    start = ctx["now"]()
    for name, where in queries:
        ctx["progress"]("A16: running the %s query" % name.replace("_", " "))
        n = len(client.wiql_ids(where))
        ctx["counts"]["discovery_" + name] = n
        parts.append("%s %d id(s)" % (name.replace("_", " "), n))
    seconds = ctx["now"]() - start
    ctx["timing"]["discovery_seconds"] = round(seconds, 3)
    if not ctx["area"]:
        parts.append("squad query not run (no area path known; pass --area "
                     "or set up config.json)")
    return _ok("%s; %.3f s" % ("; ".join(parts), seconds), len(queries))


# id, function name (looked up at run time), checks that must be observed
CHECKS = [
    ("ENV-python", "check_env_python", ()),
    ("ENV-timezone", "check_env_timezone", ()),
    ("ENV-home", "check_env_home", ()),
    ("A1", "check_a1", ()),
    ("A2", "check_a2", ()),
    ("A3", "check_a3", ("A2",)),
    ("A4", "check_a4", ("A3",)),
    ("A5", "check_a5", ("A2",)),
    ("A6", "check_a6", ("A5",)),
    ("A7", "check_a7", ("A2",)),
    ("A8", "check_a8", ("A5",)),
    ("A9", "check_a9", ("A5",)),
    ("A10", "check_a10", ("A2",)),
    ("A11", "check_a11", ("A2",)),
    ("A12", "check_a12", ("A5",)),
    ("A13", "check_a13", ("A5",)),
    ("A14", "check_a14", ()),
    ("A15", "check_a15", ("A5", "A7")),
    ("A16", "check_a16", ("A2",)),
]


def _run_check(cid: str, fn_name: str, deps: Sequence[str], ctx,
               done: Dict[str, str]) -> Dict:
    unmet = [d for d in deps if done.get(d) != "observed"]
    if unmet:
        res = _skip("dependency_failed", "depends on %s" % ", ".join(unmet))
    else:
        try:
            res = globals()[fn_name](ctx)
        except Exception as exc:
            res = _fail(_exception_code(exc), "%s: %s" % (
                type(exc).__name__, getattr(exc, "message", None) or exc))
    res["id"] = cid
    return res


def run_probe(home: Home, client_for: Callable[[str], AdoClient],
              org_url: str, project: str, team: str,
              alias: Optional[str] = None, ticket_ids: Sequence[int] = (),
              timezone: Optional[str] = None, env=None,
              now: Optional[Callable[[], float]] = None,
              config_note: Optional[str] = None, area: Optional[str] = None,
              unit_types: Optional[Sequence[str]] = None,
              closed_states: Optional[Sequence[str]] = None,
              progress: Optional[Callable[[str], None]] = None,
              today=None) -> Dict:
    from datetime import date as _date
    ctx: Dict[str, Any] = {
        "home": home, "client_for": client_for, "team": team, "alias": alias,
        "ticket_ids": [int(t) for t in ticket_ids],
        "timezone": timezone or "UTC",
        "env": os.environ if env is None else env,
        "now": now or time.perf_counter, "counts": {}, "timing": {},
        "area": area, "unit_types": tuple(unit_types or DEFAULT_TYPES),
        "closed_states": None if closed_states is None
        else list(closed_states),
        "progress": progress or _stderr_line,
        "today": today or _date.today(),
        "shapes": {"revisions": set(), "boards": set()}}
    checks, done = [], {}
    for cid, fn_name, deps in CHECKS:
        res = _run_check(cid, fn_name, deps, ctx, done)
        done[cid] = res["status"]
        checks.append(res)
    report = {
        "tool_version": __version__, "org_url": org_url, "project": project,
        "team": team, "timezone": ctx["timezone"], "checks": checks,
        "timing": ctx["timing"], "counts": ctx["counts"],
        "shapes": {k: sorted(v) for k, v in ctx["shapes"].items()},
        "states": ctx.get("states", {}),
        "board_columns": ctx.get("board_columns", []),
        "reasons": ctx.get("reasons", [])}
    if config_note:
        report["config_note"] = config_note  # local only; never exported
    home.write_json(report, "probe", "probe-report.json")
    _write_text(home, report_markdown(report), "probe", "probe-report.md")
    home.write_json(export(report), "probe", "probe-export.json")
    _write_text(home, export_markdown(report), "probe", "probe-export.md")
    return report


def _stderr_line(text: str) -> None:
    print("probe %s" % text, file=sys.stderr, flush=True)


def _write_text(home: Home, text: str, *parts: str) -> None:
    target = home.path(*parts)
    tmp = target.with_name(".tmp-" + target.name)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(target))


def _md(text: Any) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>])", r"\\\1", str(text))


def report_markdown(report: Dict) -> str:
    lines = ["# Delivery Ledger probe report (local, do not share)", "",
             "| Check | Status | Sample | Detail |", "|---|---|---|---|"]
    lines += ["| %s | %s | %d | %s |" % (c["id"], c["status"], c["sample"],
                                         _md(c["detail"]))
              for c in report["checks"]]
    lines += ["", "## States", ""]
    for wit, states in report["states"].items():
        lines.append("- %s: %s" % (_md(wit), ", ".join(
            "%s (%s)" % (_md(s["name"]), _md(s.get("category")))
            for s in states)))
    lines += ["", "## Reasons on done states", ""]
    lines += ["- %s" % _md(r) for r in report["reasons"]] or ["- none"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- command

def _probe_cmd(args, home: Home) -> Dict[str, Any]:
    from .collect import build_transport
    from ..config import load_config
    cfg, note = None, None
    if home.path("config.json").exists():
        try:
            cfg = load_config(home)
        except (LedgerError, ValueError, TypeError, AttributeError,
                KeyError) as e:
            note = "config.json could not be used for defaults: %s" % (
                getattr(e, "message", None) or e)
    org = args.org or (cfg.org_url if cfg else None)
    project = args.project or (cfg.project if cfg else None)
    team = args.team or (cfg.teams[0].ado_team if cfg and cfg.teams else None)
    if not (org and project and team):
        raise LedgerError("probe needs --org, --project and --team."
                          + (" (%s)" % note if note else ""),
                          "Pass --org, --project and --team, or fix "
                          "config.json.")
    tz = args.timezone or (cfg.timezone if cfg else "UTC")

    def client_for(kind: str) -> AdoClient:
        opts = argparse.Namespace(transport=kind, auth=args.auth)
        return AdoClient(build_transport(opts, org)[0], org, project)

    area, types, closed = args.area, None, None
    if cfg:
        types = cfg.unit_types
        tm = next((t for t in cfg.teams if team in (t.ado_team, t.name)),
                  None)
        if tm is not None:
            from .collect import done_and_removed_states
            area = area or (tm.area_paths[0] if tm.area_paths else None)
            closed = done_and_removed_states(tm) or None
    report = run_probe(home, client_for, org, project, team, alias=args.alias,
                       ticket_ids=args.ticket or (), timezone=tz,
                       config_note=note, area=area, unit_types=types,
                       closed_states=closed)
    lines = ["%-12s %s" % (c["id"], c["status"]) for c in report["checks"]]
    lines.append("Reports written to %s" % home.path("probe"))
    return {"summary": "\n".join(lines), "report": report}


def add_parser(subparsers) -> None:
    p = subparsers.add_parser("probe", help="check ADO assumptions on this "
                              "machine and write a shareable export")
    p.add_argument("--org")
    p.add_argument("--project")
    p.add_argument("--team")
    p.add_argument("--alias")
    p.add_argument("--ticket", type=int, action="append")
    p.add_argument("--area", help="an area path of the team, to size the "
                                  "squad query (default: from config.json)")
    p.add_argument("--timezone")
    p.add_argument("--auth", choices=("az", "pat"), default="az")
    p.set_defaults(func=_probe_cmd)
