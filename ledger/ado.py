"""Azure DevOps access: WIQL, the work item REST APIs and the revisions cache."""
import http.client
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
BATCH = 200
CACHE_FILE = os.path.join("cache", "revisions.json")
GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
WORKERS = 8
ROW_KEYS = ["id", "rev", "changed", "type", "state", "category", "reason", "iteration",
            "area", "assigned", "parent", "title", "tags"]
CACHE_KEYS = ("fetched", "levels", "iterations", "revisions", "parents")
CATEGORIES = ("Proposed", "InProgress", "Resolved", "Completed", "Removed")
AZ_TIMEOUT = 60
QUERY_FORMS = ("a saved query URL (.../_queries/query/<id>), a query id, a WIQL text starting "
               "with SELECT, or an area path")


class AdoError(Exception):
    def __init__(self, message: str, next_step: str = "", code: int = 0):
        super().__init__(message)
        self.next_step = next_step
        self.code = code


def az_token(run=subprocess.run) -> str:
    cmd = ["az", "account", "get-access-token", "--resource", ADO_RESOURCE,
           "--query", "accessToken", "-o", "tsv"]
    try:
        res = run(cmd, capture_output=True, text=True, timeout=AZ_TIMEOUT)
    except FileNotFoundError:
        raise AdoError("the Azure CLI (az) is not installed", "install the Azure CLI, then run: az login")
    except subprocess.TimeoutExpired:
        raise AdoError("az did not answer within %d seconds" % AZ_TIMEOUT,
                       "run az login in a terminal, then try again")
    if res.returncode != 0:
        raise AdoError("could not get an Azure DevOps token: %s" % (res.stderr or "").strip(), "az login")
    return (res.stdout or "").strip()


def az_defaults(run=subprocess.run) -> Tuple[Optional[str], Optional[str]]:
    try:
        res = run(["az", "devops", "configure", "--list"], capture_output=True, text=True,
                  timeout=AZ_TIMEOUT)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None
    if res.returncode != 0:
        return None, None
    found = {}  # type: Dict[str, str]
    for line in (res.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            found[key.strip().lower()] = value.strip()
    return found.get("organization") or None, found.get("project") or None


def _open(req: urllib.request.Request) -> str:
    """The decoded body; an HTTP error status raises AdoError with its code."""
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = " ".join(e.read().decode("utf-8", "replace")[:300].split())
        e.close()
        if e.code in (401, 403):
            raise AdoError("Azure DevOps refused the request (HTTP %d): %s" % (e.code, detail),
                           "check that your account can read this project", e.code)
        raise AdoError("Azure DevOps returned HTTP %d for %s: %s" % (e.code, req.full_url, detail),
                       "check the query, organisation and project; for HTTP 429 or 5xx run again later",
                       e.code)


def _request(method: str, url: str, body: Optional[dict], token: str) -> dict:
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    for attempt in range(3):
        try:
            text = _open(req)
            break
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            if attempt == 2:
                raise AdoError("could not reach Azure DevOps: %s" % getattr(e, "reason", e),
                               "check your network connection: %s" % url)
            time.sleep(2 * (attempt + 1))
    try:
        return json.loads(text or "{}")
    except ValueError:
        raise AdoError("Azure DevOps answered %s with a page that is not JSON (often a sign-in page)"
                       % url, "run az login again, then try again")


def az_http(run=subprocess.run) -> Callable:
    """An `http(method, url, body)` callable that gets the az token once and reuses it."""
    token = []  # type: List[str]

    def http(method: str, url: str, body: Optional[dict] = None) -> dict:
        if not token:
            token.append(az_token(run))
        return _request(method, url, body, token[0])
    return http


def lit(text: str) -> str:
    """Escape a value for an OData or WIQL single-quoted string literal."""
    return text.replace("'", "''")


def _quote(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def _chunks(items: List, size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class Client:
    def __init__(self, org: str, project: str, http: Optional[Callable] = None):
        self.org = org.rstrip("/")
        self.project = project
        self.http = http or az_http()

    def _base(self) -> str:
        return "%s/%s" % (self.org, urllib.parse.quote(self.project))

    def query_ids(self, query: str, since: date) -> List[int]:
        text = query.strip()
        guid = re.search(r"/_queries/query(?:-edit)?/(%s)" % GUID, text) \
            or re.fullmatch(r"(%s)" % GUID, text)
        if not guid and re.match(r"(?i)https?://", text):
            raise ValueError("the query '%s' is not a saved query URL; use %s" % (text, QUERY_FORMS))
        if guid:
            res = self.http("GET", "%s/_apis/wit/wiql/%s?api-version=7.1" % (self._base(), guid.group(1)), None)
        else:
            if not text.lower().startswith("select"):
                text = ("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project "
                        "AND [System.AreaPath] UNDER '%s' AND [System.ChangedDate] >= '%s'"
                        % (lit(text), since.isoformat()))
            res = self.http("POST", "%s/_apis/wit/wiql?api-version=7.1" % self._base(), {"query": text})
        ids = [w["id"] for w in res.get("workItems") or []]
        for rel in res.get("workItemRelations") or []:
            if rel.get("target"):
                ids.append(rel["target"]["id"])
        return sorted(set(ids))

    def _api(self, path: str) -> str:
        return "%s/_apis/%s%sapi-version=7.1" % (self._base(), path, "&" if "?" in path else "?")

    def wiql(self, text: str) -> List[int]:
        res = self.http("POST", self._api("wit/wiql"), {"query": text})
        return [w["id"] for w in res.get("workItems") or []]

    def _revision_page(self, wid: int, skip: int) -> List[dict]:
        res = self.http("GET", self._api("wit/workitems/%d/revisions?$top=%d&$skip=%d"
                                         % (wid, BATCH, skip)), None)
        return res.get("value") or []

    def revisions(self, wid: int) -> List[dict]:
        """Every revision of `wid`; none when the item is gone or unreadable (HTTP 403/404)."""
        out = []  # type: List[dict]
        try:
            while True:
                page = self._revision_page(wid, len(out))
                out.extend(page)
                if len(page) < BATCH:
                    return out
        except AdoError as e:
            if e.code in (403, 404):
                return []
            raise

    def categories(self, wtype: str) -> Dict[str, str]:
        res = self.http("GET", self._api("wit/workitemtypes/%s/states" % _quote(wtype)), None)
        return {s["name"]: s.get("category") for s in res.get("value") or []}

    def iterations(self) -> List[dict]:
        """Dated iterations, with paths in the form work items use (no "Iteration" segment)."""
        root = self.http("GET", self._api("wit/classificationnodes/Iterations?$depth=20"), None)
        out = []  # type: List[dict]

        def walk(node: dict) -> None:
            attrs = node.get("attributes") or {}
            if attrs.get("startDate") and attrs.get("finishDate"):
                parts = (node.get("path") or "").strip("\\").split("\\")
                out.append({"path": "\\".join(parts[:1] + parts[2:]), "name": node.get("name"),
                            "start": attrs["startDate"][:10], "end": attrs["finishDate"][:10]})
            for child in node.get("children") or []:
                walk(child)
        walk(root)
        return out

    def titles(self, ids: List[int]) -> Dict[str, str]:
        out = {}  # type: Dict[str, str]
        for chunk in _chunks(ids):
            res = self.http("POST", self._api("wit/workitemsbatch"),
                            {"ids": chunk, "fields": ["System.Title"], "errorPolicy": "omit"})
            for item in res.get("value") or []:
                if item:
                    out[str(item["id"])] = (item.get("fields") or {}).get("System.Title")
        return out

    def levels(self) -> Dict[str, List[str]]:
        res = self.http("GET", "%s/_apis/work/processconfiguration?api-version=7.1" % self._base(), None)

        def names(key: str) -> List[str]:
            return [t["name"] for t in (res.get(key) or {}).get("workItemTypes") or []]
        return {"stories": names("requirementBacklog"), "tasks": names("taskBacklog")}


def _id_list(ids: List[int]) -> str:
    return "(%s)" % ",".join(str(i) for i in ids)


def _flatten(raw: dict, categories: Dict[Tuple[str, str], str]) -> dict:
    f = raw.get("fields") or {}
    assigned = (f.get("System.AssignedTo") or {}).get("uniqueName")
    tags = f.get("System.Tags")
    wtype, state = f.get("System.WorkItemType"), f.get("System.State")
    return {
        "id": raw["id"], "rev": raw["rev"], "changed": f.get("System.ChangedDate"),
        "type": wtype, "state": state, "category": categories.get((wtype, state)),
        "reason": f.get("System.Reason"), "iteration": f.get("System.IterationPath"),
        "area": f.get("System.AreaPath"),
        "assigned": assigned.lower() if assigned else None, "parent": f.get("System.Parent"),
        "title": f.get("System.Title"),
        "tags": [t.strip() for t in tags.split(";") if t.strip()] if tags else [],
    }


def check_rows(data: dict) -> None:
    """Raise AdoError when a revision row lacks a field or has an unknown state category."""
    for r in data["revisions"]:
        for key in ROW_KEYS:
            if key not in r:
                raise AdoError("the revisions lack the field '%s' (work item %s)" % (key, r.get("id")),
                               "check that the revisions return every field and run init again")
        if r["category"] not in CATEGORIES:
            raise AdoError("unknown state category '%s' (work item %s)" % (r["category"], r["id"]),
                           "expected one of: %s" % ", ".join(CATEGORIES))


def fetch(client: Client, query: str, emails: List[str], today: date, history_days: int = 200,
          warn: Callable[[str], None] = lambda line: None, known: Sequence[int] = ()) -> dict:
    """Revisions of the query's items, their children, items assigned to `emails` since
    `history_days` before `today` and the `known` (cached) items; raises AdoError when nothing
    comes back. "requested" lists every id asked for, for save_cache."""
    since = date.fromordinal(today.toordinal() - history_days)
    query_ids = set(client.query_ids(query, since))
    ids = set(query_ids)
    scope = "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND "
    for chunk in _chunks(sorted(ids)):
        ids.update(client.wiql(scope + "[System.Parent] IN " + _id_list(chunk)))
    for chunk in _chunks(sorted(emails), 20):
        ever = " OR ".join("[System.AssignedTo] EVER '%s'" % lit(e) for e in chunk)
        ids.update(client.wiql(scope + "[System.ChangedDate] >= '%s' AND (%s)"
                               % (since.isoformat(), ever)))
    ids |= set(known)
    with ThreadPoolExecutor(WORKERS) as pool:
        raws = [r for revs in pool.map(client.revisions, sorted(ids)) for r in revs]
    types = sorted({(r.get("fields") or {}).get("System.WorkItemType") for r in raws} - {None})
    categories = {(t, state): cat for t in types for state, cat in client.categories(t).items()}
    rows = [_flatten(raw, categories) for raw in raws]
    if not rows:
        raise AdoError("the query returned no work items",
                       "check the query and your access to its area paths")
    rows.sort(key=lambda r: (r["id"], r["rev"]))
    found = {r["id"] for r in rows}
    missing = query_ids - found - set(known)
    if missing:
        warn("%d work items from the query returned no history (possible area-path permission "
             "gaps)" % len(missing))
    gone = sorted(set(known) - found)
    if gone:
        warn("%d saved work items returned no history and were dropped (deleted or no longer "
             "readable): %s" % (len(gone), ", ".join("#%d" % i for i in gone[:5])
                                + (", ..." if len(gone) > 5 else "")))
    iterations = client.iterations()
    iterations.sort(key=lambda i: (i["start"], i["path"]))
    parents = client.titles(sorted({r["parent"] for r in rows if r["parent"] is not None}))
    data = {"fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "levels": client.levels(), "iterations": iterations, "revisions": rows, "parents": parents,
            "requested": sorted(ids)}
    check_rows(data)
    return data


def cache_path(dir: str) -> str:
    return os.path.join(dir, CACHE_FILE)


def _merge(old: dict, new: dict, requested: set) -> dict:
    """`new` over `old`. An item in `new` or in `requested` takes its rows from `new` only, so a
    requested item with no rows (deleted, unreadable) is dropped; other old items stay. Old
    iterations stay only while a revision still uses their path. Parents by id; levels and the
    fetch time from `new`."""
    replaced = requested | {r["id"] for r in new["revisions"]}
    revs = [r for r in old["revisions"] if r["id"] not in replaced] + new["revisions"]
    used = {r["iteration"] for r in revs}
    its = {i["path"]: i for i in old["iterations"] if i["path"] in used}
    its.update((i["path"], i) for i in new["iterations"])
    return {"fetched": new["fetched"], "levels": new["levels"],
            "iterations": sorted(its.values(), key=lambda i: (i["start"], i["path"])),
            "revisions": sorted(revs, key=lambda r: (r["id"], r["rev"])),
            "parents": {**old["parents"], **new["parents"]}}


def cached_ids(dir: str) -> List[int]:
    """Work item ids in the cache in `dir`; none when it is missing or unreadable."""
    try:
        return sorted({r["id"] for r in load_cache(dir)["revisions"]})
    except AdoError:
        return []


def save_cache(dir: str, data: dict, warn: Callable[[str], None] = lambda line: None) -> dict:
    """Merge `data` into the cache in `dir` (an unreadable cache is replaced, with a warning);
    return the result."""
    path = cache_path(dir)
    data = dict(data)
    requested = set(data.pop("requested", ()))
    if os.path.exists(path):
        try:
            data = _merge(load_cache(dir), data, requested)
        except AdoError:
            warn("the saved board data was unreadable and has been replaced; older sprints may "
                 "have lost history")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return data


def load_cache(dir: str) -> dict:
    path = cache_path(dir)
    if not os.path.exists(path):
        raise AdoError("no cache at %s" % path, "run without --offline")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        missing = [k for k in CACHE_KEYS if k not in data]
        if missing:
            raise AdoError("the cache lacks %s" % ", ".join(missing))
        check_rows(data)
    except (ValueError, AdoError) as e:
        raise AdoError("the cache at %s is unreadable: %s" % (path, e), "run without --offline")
    return data
