"""In-memory Azure DevOps: serves real-looking REST payloads from a PortfolioBuilder.

Implements the Transport interface (`request(method, url, body)`), evaluates
exactly the WIQL clause forms produced by the helpers in ledger.ado.client,
and supports failure injection for collector tests.
"""
import hashlib
import re
import urllib.parse
import uuid
from datetime import date, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from ledger.ado.transport import Response, TransportError

from .builder import PortfolioBuilder, TicketBuilder

CATEGORY = {"todo": "Proposed", "doing": "InProgress", "handoff": "InProgress",
            "done": "Completed", "removed": "Removed"}
SQUAD_ACTOR = PortfolioBuilder.other("fake-squad-member")
COLUMN_FIELD = "WEF_FAKE_Kanban.Column"

# Transports the collect command may use when LEDGER_TEST_TRANSPORT names one.
REGISTRY: Dict[str, Any] = {}

_LIT = r"'(?:[^']|'')*'"
_LITS = r"\(\s*(%s(?:\s*,\s*%s)*)\s*\)" % (_LIT, _LIT)


def _unquote(lit: str) -> str:
    return lit[1:-1].replace("''", "'")


def _lits(group: str) -> List[str]:
    return [_unquote(m) for m in re.findall(_LIT, group)]


def _uuid(text: str) -> str:
    return str(uuid.UUID(hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]))


def _iso(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _err(status: int, message: str) -> Response:
    return Response(status, {}, {"message": message, "typeKey": "FakeAdoError"})


def _under(path: str, root: str) -> bool:
    path, root = path.lower(), root.lower()
    return path == root or path.startswith(root + "\\")


class _Unrecognised(Exception):
    pass


class FakeAdo:
    def __init__(self, builder: PortfolioBuilder,
                 identities: Optional[Dict[str, Dict]] = None,
                 page_cap: Optional[int] = None, wiql_cap: int = 20000,
                 org_url: str = "https://dev.azure.com/demo",
                 project: str = "Demo", account: Optional[str] = None):
        self.builder = builder
        self.identities = dict(identities or {})
        self.page_cap = page_cap
        self.wiql_cap = wiql_cap
        self.org_url = org_url.rstrip("/")
        self.project = project
        self.account = account
        self.unreadable: Set[int] = set()
        self.dateless_iteration = False
        self.requests: List[Tuple[str, str, Any]] = []
        self.reset_failures()

    # ------------------------------------------------------------ identities
    def identity(self, actor: str) -> Dict:
        if actor in self.identities:
            return self.identities[actor]
        ident = _uuid(actor)
        hex6 = ident.replace("-", "")[:6]
        people = self.builder._people
        if actor.startswith("p:") and actor[2:] in people:
            pid = actor[2:]
            name, unique = people[pid]["display_name"], pid + "@vendor.example"
        else:
            name, unique = "Other " + hex6, hex6 + "@customer.example"
            for r in self.builder._roles:
                for n in r["others"]:
                    if actor == self.builder.other(n):
                        unique = self.builder.alias_of(n)
        return {"displayName": name, "uniqueName": unique, "id": ident,
                "descriptor": "aad." + ident.replace("-", "")}

    def alias(self, actor: str) -> str:
        return self.identity(actor)["uniqueName"]

    def roster_patch(self) -> Dict[str, Dict[str, List[str]]]:
        out = {}
        for pid in self.builder._people:
            ident = self.identity("p:" + pid)
            out[pid] = {"identity_ids": [ident["id"]],
                        "aliases": [ident["uniqueName"]]}
        return out

    # ------------------------------------------------------------ failures
    def fail_next(self, status: int, headers: Optional[Dict] = None,
                  body: Any = None, times: int = 1) -> None:
        for _ in range(times):
            self._failures.append(Response(status, dict(headers or {}), body))

    def drop_after(self, n_requests: int) -> None:
        """Every request after the next `n_requests` raises TransportError."""
        self._drop_at = len(self.requests) + n_requests

    def reset_failures(self) -> None:
        self._failures: List[Response] = []
        self._drop_at: Optional[int] = None

    # ------------------------------------------------------------ transport
    def request(self, method: str, url: str, body: Optional[dict] = None) -> Response:
        self.requests.append((method, url, body))
        if self._drop_at is not None and len(self.requests) > self._drop_at:
            raise TransportError("fake ADO: connection dropped")
        if self._failures:
            return self._failures.pop(0)
        method = method.upper()
        if method not in ("GET", "POST"):
            return _err(405, "fake ADO: method %s not allowed" % method)
        parts = urllib.parse.urlsplit(url)
        prefix = urllib.parse.urlsplit(self.org_url).path.rstrip("/")
        if not parts.path.startswith(prefix + "/"):
            return _err(404, "fake ADO: not this organisation")
        segs = [urllib.parse.unquote(s)
                for s in parts.path[len(prefix) + 1:].split("/")]
        query = dict(urllib.parse.parse_qsl(parts.query))
        for pattern, verb, handler in self._routes():
            m = re.fullmatch(pattern, "/".join(
                s.replace("/", "%2F") for s in segs))
            if m and verb == method:
                return handler(*m.groups(), query=query, body=body)
        return _err(404, "fake ADO: no route for %s %s" % (method, parts.path))

    def _routes(self) -> List[Tuple[str, str, Callable[..., Response]]]:
        p = re.escape(self.project)
        return [
            (r"_apis/connectionData", "GET", self._connection),
            (p + r"/_apis/wit/wiql", "POST", self._wiql),
            (p + r"/_apis/wit/workItems/(\d+)/revisions", "GET", self._revisions),
            (p + r"/_apis/wit/workitemtypes/([^/]+)/states", "GET", self._states),
            (p + r"/([^/]+)/_apis/work/teamsettings/iterations", "GET",
             self._iterations),
            (p + r"/([^/]+)/_apis/work/boards", "GET", self._boards),
            (p + r"/([^/]+)/_apis/work/boards/([^/]+)", "GET", self._board),
        ]

    # ------------------------------------------------------------ endpoints
    def _connection(self, query, body) -> Response:
        ident = (self.identity(self.account) if self.account else
                 {"id": _uuid("fake-account"), "displayName": "Fake Account"})
        return Response(200, {}, {
            "authenticatedUser": {"id": ident["id"],
                                  "providerDisplayName": ident["displayName"],
                                  "isActive": True},
            "instanceId": _uuid("fake-instance")})

    def _ticket(self, tid: int) -> Optional[TicketBuilder]:
        for t in self.builder._tickets:
            if t.id == tid:
                return t
        return None

    def _revisions(self, tid, query, body) -> Response:
        t = self._ticket(int(tid))
        if t is None:
            return _err(404, "TF401232: Work item %s does not exist." % tid)
        if t.id in self.unreadable:
            return _err(403, "TF237088: You do not have permission to read "
                             "work item %s." % tid)
        skip = int(query.get("$skip", 0))
        top = int(query.get("$top", 200))
        if self.page_cap is not None:
            top = min(top, self.page_cap)
        value = [self._revision_payload(t, i)
                 for i in range(skip, min(len(t.revisions), skip + top))]
        return Response(200, {}, {"count": len(value), "value": value})

    def _revision_payload(self, t: TicketBuilder, i: int) -> Dict:
        r = t.revisions[i]
        title, desc, ac = t.texts[i]
        by = r.by if t.vendor else SQUAD_ACTOR
        assignee = r.assignee if t.vendor else SQUAD_ACTOR
        f: Dict[str, Any] = {
            "System.Id": t.id, "System.Rev": r.rev,
            "System.ChangedDate": _iso(r.changed),
            "System.ChangedBy": self.identity(by),
            "System.State": r.state, "System.Reason": r.reason,
            "System.IterationPath": r.iteration, "System.AreaPath": r.area,
            "System.WorkItemType": r.type, "System.TeamProject": r.project,
            "System.Title": title or ("Ticket %d" % t.id),
            "System.Description": "<div>%s</div>" % desc,
            "Microsoft.VSTS.Common.AcceptanceCriteria": "<div>%s</div>" % ac,
        }
        if assignee is not None:
            f["System.AssignedTo"] = self.identity(assignee)
        if r.parent is not None:
            f["System.Parent"] = r.parent
        if r.tags:
            f["System.Tags"] = "; ".join(r.tags)
        if r.column is not None:
            f["System.BoardColumn"] = r.column
        if r.column_done is not None:
            f["System.BoardColumnDone"] = r.column_done
        return {"id": t.id, "rev": r.rev, "fields": f,
                "url": "%s/%s/_apis/wit/workItems/%d/revisions/%d"
                       % (self.org_url, self.project, t.id, r.rev)}

    def _team(self, team: str) -> Optional[Dict]:
        return self.builder._teams.get(team)

    def _iterations(self, team, query, body) -> Response:
        if self._team(team) is None:
            return _err(404, "fake ADO: team %s not found" % team)
        value = []
        for name, start, finish in self._team(team)["sprints"]:
            path = self.builder.iteration_path(team, name)
            value.append({"id": _uuid("iteration:" + path), "name": name,
                          "path": path, "attributes": {
                              "startDate": start + "T00:00:00Z",
                              "finishDate": finish + "T00:00:00Z",
                              "timeFrame": "past"}})
        if self.dateless_iteration:
            path = self.builder.iteration_path(team, "Future")
            value.append({"id": _uuid("iteration:" + path), "name": "Future",
                          "path": path, "attributes": {
                              "startDate": None, "finishDate": None,
                              "timeFrame": "future"}})
        return Response(200, {}, {"count": len(value), "value": value})

    def _states(self, wit, query, body) -> Response:
        seen: Dict[str, Dict] = {}
        for t in self.builder._teams.values():
            for rule in t["phases"]:
                seen.setdefault(rule["state"], {
                    "name": rule["state"], "color": "b2b2b2",
                    "category": CATEGORY[rule["phase"]]})
        value = list(seen.values())
        return Response(200, {}, {"count": len(value), "value": value})

    def _board_id(self, team: str) -> str:
        return _uuid("board:" + team)

    def _boards(self, team, query, body) -> Response:
        if self._team(team) is None:
            return _err(404, "fake ADO: team %s not found" % team)
        value = [{"id": self._board_id(team), "name": "Stories",
                  "url": "%s/%s/%s/_apis/work/boards/%s" % (
                      self.org_url, self.project, team, self._board_id(team))}]
        return Response(200, {}, {"count": 1, "value": value})

    def _board(self, team, board_id, query, body) -> Response:
        if self._team(team) is None or board_id != self._board_id(team):
            return _err(404, "fake ADO: board not found")
        order: List[str] = []
        split: Set[str] = set()
        for t in self.builder._tickets:
            if t.team != team:
                continue
            for r in t.revisions:
                if r.column is None:
                    continue
                if r.column not in order:
                    order.append(r.column)
                if r.column_done is not None:
                    split.add(r.column)
        if not order:
            order = ["New", "Active", "Closed"]
        columns = []
        for i, name in enumerate(order):
            kind = ("incoming" if i == 0 else
                    "outgoing" if i == len(order) - 1 else "inProgress")
            columns.append({"id": _uuid("column:%s:%s" % (team, name)),
                            "name": name, "itemLimit": 0,
                            "stateMappings": {}, "isSplit": name in split,
                            "columnType": kind})
        return Response(200, {}, {
            "id": board_id, "name": "Stories", "columns": columns,
            "fields": {"columnField": {"referenceName": COLUMN_FIELD},
                       "rowField": {"referenceName": "WEF_FAKE_Kanban.Lane"},
                       "doneField": {"referenceName": COLUMN_FIELD + ".Done"}}})

    # ------------------------------------------------------------ WIQL
    def _wiql(self, query, body) -> Response:
        text = (body or {}).get("query", "")
        m = re.fullmatch(r"\s*SELECT \[System\.Id\] FROM WorkItems WHERE (.*)",
                         text, re.S)
        if not m:
            return _err(400, "fake ADO: unrecognised query form: %s" % text[:200])
        try:
            pred, end = self._parse_expr(m.group(1), 0)
            if m.group(1)[end:].strip():
                raise _Unrecognised(m.group(1)[end:])
        except _Unrecognised as exc:
            return _err(400, "fake ADO: unrecognised WIQL clause: %s"
                        % str(exc)[:200])
        hits = sorted(t.id for t in self.builder._tickets
                      if t.revisions and pred(t))
        if len(hits) > self.wiql_cap:
            return _err(400, "VS402337: The number of work items returned "
                             "exceeds the size limit of %d. Change the query "
                             "to return fewer items." % self.wiql_cap)
        return Response(200, {}, {
            "queryType": "flat", "queryResultType": "workItem",
            "asOf": "2026-11-11T00:00:00Z",
            "columns": [{"referenceName": "System.Id", "name": "ID"}],
            "workItems": [{"id": i, "url": "%s/_apis/wit/workItems/%d"
                           % (self.org_url, i)} for i in hits]})

    def _clause_patterns(self):
        latest = lambda t: t.revisions[-1]  # noqa: E731
        return [
            (r"\[System\.TeamProject\] = @project",
             lambda m: lambda t: latest(t).project == self.project),
            (r"\[System\.Id\] >= (\d+)",
             lambda m: lambda t: t.id >= int(m.group(1))),
            (r"\[System\.Id\] < (\d+)",
             lambda m: lambda t: t.id < int(m.group(1))),
            (r"\[System\.WorkItemType\] IN " + _LITS,
             lambda m: (lambda vs: lambda t: latest(t).type in vs)(
                 set(_lits(m.group(1))))),
            (r"\[System\.State\] NOT IN " + _LITS,
             lambda m: (lambda vs: lambda t: latest(t).state not in vs)(
                 set(_lits(m.group(1))))),
            (r"\[System\.ChangedDate\] >= '(\d{4}-\d{2}-\d{2})'",
             lambda m: (lambda d: lambda t: latest(t).changed.astimezone(
                 timezone.utc).date() >= d)(date.fromisoformat(m.group(1)))),
            (r"\[System\.AreaPath\] UNDER (%s)" % _LIT,
             lambda m: (lambda root: lambda t: _under(latest(t).area, root))(
                 _unquote(m.group(1)))),
            (r"EVER \[System\.AssignedTo\] = (%s)" % _LIT,
             lambda m: (lambda a: lambda t: self._ever_assigned(t, a))(
                 _unquote(m.group(1)).lower())),
        ]

    def _ever_assigned(self, t: TicketBuilder, alias: str) -> bool:
        actors = {r.assignee for r in t.revisions} if t.vendor else {SQUAD_ACTOR}
        for actor in actors - {None}:
            ident = self.identity(actor)
            forms = {ident["uniqueName"], ident["displayName"],
                     "%s <%s>" % (ident["displayName"], ident["uniqueName"])}
            if alias in {f.lower() for f in forms}:
                return True
        return False

    def _parse_term(self, s: str, i: int):
        i = len(s) - len(s[i:].lstrip())
        if s.startswith("(", i):
            pred, i = self._parse_expr(s, i + 1)
            j = len(s) - len(s[i:].lstrip())
            if not s.startswith(")", j):
                raise _Unrecognised(s[i:])
            return pred, j + 1
        for pattern, build in self._clause_patterns():
            m = re.compile(pattern).match(s, i)
            if m:
                return build(m), m.end()
        raise _Unrecognised(s[i:])

    def _parse_expr(self, s: str, i: int):
        preds, ops = [], set()
        while True:
            pred, i = self._parse_term(s, i)
            preds.append(pred)
            m = re.compile(r"\s+(AND|OR)\s+").match(s, i)
            if not m:
                break
            ops.add(m.group(1))
            i = m.end()
        if len(ops) > 1:
            raise _Unrecognised("AND and OR mixed without parentheses: " + s)
        if ops == {"OR"}:
            return (lambda t: any(p(t) for p in preds)), i
        return (lambda t: all(p(t) for p in preds)), i
