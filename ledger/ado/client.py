"""Azure DevOps REST client: complete WIQL enumeration, revisions, metadata.

All knowledge of ADO response field names lives in the small `_parse_*`
functions below, one per endpoint, so a correction is a one-line change.
"""
import json
import urllib.parse
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ledger.ado.transport import Response, TransportError
from ledger.errors import LedgerError

API = "7.1"
ID_RANGE = (1, 2_000_000)
BATCH_LIMIT = 19000
MAX_ID = 2 ** 40
SIZE_LIMIT_MARKERS = ("VS402337", "exceeds the size limit")
TIMEOUT_MARKERS = ("VS402335", "timeout", "timed out")


# ---------------------------------------------------------------- WIQL clauses
# The collector composes queries only from these helpers; FakeAdo parses
# exactly these forms.

def wiql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quoted_list(values: Sequence[str]) -> str:
    return "(" + ", ".join(wiql_quote(v) for v in values) + ")"


def clause_types(types: Sequence[str]) -> str:
    return "[System.WorkItemType] IN " + _quoted_list(types)


def clause_ever_assigned(alias: str) -> str:
    return "EVER [System.AssignedTo] = " + wiql_quote(alias)


def clause_states_not_in(states: Sequence[str]) -> str:
    return "[System.State] NOT IN " + _quoted_list(states)


def clause_changed_since(d: date) -> str:
    return "[System.ChangedDate] >= " + wiql_quote(d.isoformat())


def clause_area_under(area: str) -> str:
    return "[System.AreaPath] UNDER " + wiql_quote(area)


def where_all(*clauses: str) -> str:
    return " AND ".join(clauses)


def where_any(*clauses: str) -> str:
    return "(" + " OR ".join(clauses) + ")"


# ---------------------------------------------------------------- errors

class NotFound(LedgerError):
    def __init__(self, ticket_id: int):
        super().__init__("Ticket %d was not found (deleted or moved)." % ticket_id)
        self.ticket_id = ticket_id


class Unreadable(LedgerError):
    def __init__(self, ticket_id: int):
        super().__init__("Ticket %d is not readable by the signed-in account."
                         % ticket_id)
        self.ticket_id = ticket_id


class Malformed(LedgerError):
    """A history the tool cannot use: revisions not numbered 1..N, or a
    revision that cannot be parsed."""

    def __init__(self, ticket_id: int, why: str):
        super().__init__("Ticket %d: %s; its history was not stored."
                         % (ticket_id, why))
        self.ticket_id = ticket_id


# ---------------------------------------------------------------- parsers

def _server_message(resp: Response) -> str:
    if isinstance(resp.body, dict) and resp.body.get("message"):
        return str(resp.body["message"])
    if resp.body is not None:
        return json.dumps(resp.body)[:500]
    return (resp.text or "")[:500]


def _parse_wiql(body: Any) -> List[int]:
    if not isinstance(body, dict) or not isinstance(body.get("workItems"), list):
        raise LedgerError("Azure DevOps returned an unexpected WIQL response.")
    return [int(w["id"]) for w in body["workItems"]]


def _parse_revision_page(body: Any) -> List[Dict]:
    if not isinstance(body, dict) or not isinstance(body.get("value"), list):
        raise LedgerError("Azure DevOps returned an unexpected revisions page.")
    return body["value"]


def _date_part(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value[:10]) if value else None


def _parse_iteration(v: Dict) -> Dict:
    attrs = v.get("attributes") or {}
    return {"id": v["id"], "name": v["name"], "path": v.get("path"),
            "start": _date_part(attrs.get("startDate")),
            "finish": _date_part(attrs.get("finishDate"))}


def _parse_state(v: Dict) -> Dict:
    return {"name": v["name"], "category": v.get("category")}


def _parse_board_list(body: Dict) -> List[Tuple[str, str]]:
    return [(b["id"], b["name"]) for b in body["value"]]


def _parse_board(body: Dict) -> Dict:
    fields = body.get("fields") or {}
    return {"board": body["name"],
            "column_field": (fields.get("columnField") or {}).get("referenceName"),
            "done_field": (fields.get("doneField") or {}).get("referenceName"),
            "columns": [{"name": c["name"], "split": bool(c.get("isSplit"))}
                        for c in body.get("columns") or []]}


def _parse_connection(body: Dict) -> Dict:
    user = body["authenticatedUser"]
    return {"id": user["id"], "name": user.get("providerDisplayName")}


# ---------------------------------------------------------------- client

def _seg(name: str) -> str:
    return urllib.parse.quote(name, safe="")


class AdoClient:
    def __init__(self, transport, org_url: str, project: str, api: str = API):
        self.transport = transport
        self.org = org_url.rstrip("/")
        self.project = project
        self.api = api

    # urls
    def _project_url(self, path: str) -> str:
        return "%s/%s/%s" % (self.org, _seg(self.project), path)

    def _team_url(self, team: str, path: str) -> str:
        return "%s/%s/%s/%s" % (self.org, _seg(self.project), _seg(team), path)

    def _versioned(self, url: str, api: Optional[str] = None) -> str:
        return url + ("&" if "?" in url else "?") + "api-version=" + (api or self.api)

    def _get_ok(self, url: str) -> Any:
        resp = self.transport.request("GET", url)
        if resp.status != 200:
            raise LedgerError("Azure DevOps request failed (HTTP %d): %s"
                              % (resp.status, _server_message(resp)))
        return resp.body

    # endpoints
    def connection(self) -> Dict:
        url = "%s/_apis/connectionData?api-version=%s-preview" % (self.org, self.api)
        return _parse_connection(self._get_ok(url))

    def wiql_ids(self, where: str, id_range: Tuple[int, int] = ID_RANGE,
                 batch_limit: int = BATCH_LIMIT,
                 on_partition: Optional[Callable[[Tuple[int, int], List[int]],
                                                 None]] = None) -> List[int]:
        url = self._versioned(self._project_url("_apis/wit/wiql"))
        found: set = set()
        lo, hi = id_range
        self._enumerate(url, where, (lo, hi), batch_limit, on_partition, found)
        # Probe above the enumerated range; ids from a probe are never trusted,
        # only the bounded enumeration that follows it.
        while True:
            probe = self._wiql_query(url, where, hi, None)
            if probe is not None and not probe:
                return sorted(found)
            if hi > MAX_ID:
                raise LedgerError(
                    "Azure DevOps keeps reporting work items above id %d; "
                    "stopped to avoid an endless query." % hi)
            self._enumerate(url, where, (hi, 2 * hi), batch_limit,
                            on_partition, found)
            hi = 2 * hi

    def wiql_ids_in(self, where: str, id_range: Tuple[int, int],
                    batch_limit: int = BATCH_LIMIT,
                    on_partition: Optional[Callable[[Tuple[int, int],
                                                     List[int]], None]] = None
                    ) -> List[int]:
        """Ids in [lo, hi) only: the bounded enumeration, with no look
        above the range."""
        url = self._versioned(self._project_url("_apis/wit/wiql"))
        found: set = set()
        self._enumerate(url, where, tuple(id_range), batch_limit,
                        on_partition, found)
        return sorted(found)

    def _enumerate(self, url, where, id_range, batch_limit, on_partition,
                   found: set) -> None:
        pending = [tuple(id_range)]
        while pending:
            a, b = pending.pop()
            ids = self._wiql_query(url, where, a, b)
            if ids is not None and len(ids) >= batch_limit:
                ids = None  # a full batch may be truncated
            if ids is None:
                if b - a <= 1:
                    raise LedgerError(
                        "Azure DevOps could not return query results for "
                        "ticket id %d even on its own; the result would be "
                        "incomplete." % a)
                mid = (a + b) // 2
                pending += [(mid, b), (a, mid)]  # lower half first
                continue
            found.update(ids)
            if on_partition is not None:
                on_partition((a, b), sorted(set(ids)))

    def _wiql_query(self, url, where, a, b) -> Optional[List[int]]:
        """Ids in [a, b) (b None: no upper bound), or None when it must be split."""
        query = ("SELECT [System.Id] FROM WorkItems WHERE "
                 "[System.TeamProject] = @project AND (%s) AND "
                 "[System.Id] >= %d" % (where, a))
        if b is not None:
            query += " AND [System.Id] < %d" % b
        try:
            resp = self.transport.request("POST", url, {"query": query})
        except TransportError:
            return None
        if resp.status in (408, 504):
            return None
        if resp.status != 200:
            message = _server_message(resp)
            if resp.status == 400 and any(m in message
                                          for m in SIZE_LIMIT_MARKERS):
                return None
            # a query timeout also arrives as a 400 or 500 with a message
            if any(m.lower() in message.lower() for m in TIMEOUT_MARKERS):
                return None
        if resp.status != 200:
            raise LedgerError("Azure DevOps rejected a query (HTTP %d): %s"
                              % (resp.status, _server_message(resp)))
        return _parse_wiql(resp.body)

    def revisions(self, ticket_id: int, page: int = 200) -> List[Dict]:
        base = self._project_url("_apis/wit/workItems/%d/revisions" % ticket_id)
        out: List[Dict] = []
        effective = None
        while True:
            url = self._versioned("%s?$top=%d&$skip=%d&$expand=fields"
                                  % (base, page, len(out)))
            resp = self.transport.request("GET", url)
            if resp.status == 404:
                raise NotFound(ticket_id)
            if resp.status in (401, 403):
                raise Unreadable(ticket_id)
            if resp.status != 200:
                raise LedgerError("Reading ticket %d failed (HTTP %d): %s"
                                  % (ticket_id, resp.status, _server_message(resp)))
            items = _parse_revision_page(resp.body)
            if items and items[0].get("rev") != len(out) + 1:
                raise Malformed(ticket_id, "the server returned revision %r "
                                "where %d was expected"
                                % (items[0].get("rev"), len(out) + 1))
            out.extend(items)
            if effective is None:
                effective = len(items)
            if not items or len(items) < effective:
                break
        revs = [r.get("rev") for r in out]
        if not revs or revs != list(range(1, len(revs) + 1)):
            raise Malformed(ticket_id, "its revision history is incomplete "
                            "or out of order")
        return out

    def iterations(self, ado_team: str) -> List[Dict]:
        url = self._versioned(self._team_url(
            ado_team, "_apis/work/teamsettings/iterations"))
        return [_parse_iteration(v) for v in self._get_ok(url)["value"]]

    def states(self, work_item_type: str) -> List[Dict]:
        url = self._versioned(self._project_url(
            "_apis/wit/workitemtypes/%s/states" % _seg(work_item_type)))
        return [_parse_state(v) for v in self._get_ok(url)["value"]]

    def board_columns(self, ado_team: str) -> List[Dict]:
        base = self._team_url(ado_team, "_apis/work/boards")
        out = []
        for board_id, _name in _parse_board_list(self._get_ok(self._versioned(base))):
            detail = self._get_ok(self._versioned("%s/%s" % (base, _seg(board_id))))
            out.append(_parse_board(detail))
        return out
