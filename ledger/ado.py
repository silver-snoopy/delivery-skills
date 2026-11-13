"""Azure DevOps access: WIQL, Analytics OData and the revisions cache."""
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

ADO_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
BATCH = 200
CACHE_FILE = os.path.join("cache", "revisions.json")
GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
REVISION_FIELDS = ("WorkItemId,Revision,ChangedDate,WorkItemType,State,StateCategory,Reason,"
                   "IterationPath,AreaPath,ParentWorkItemId,Title,TagNames")
ROW_KEYS = ["id", "rev", "changed", "type", "state", "category", "reason", "iteration",
            "area", "assigned", "parent", "title", "tags"]


class AdoError(Exception):
    def __init__(self, message: str, next_step: str = ""):
        super().__init__(message)
        self.next_step = next_step


def az_token(run=subprocess.run) -> str:
    cmd = ["az", "account", "get-access-token", "--resource", ADO_RESOURCE,
           "--query", "accessToken", "-o", "tsv"]
    try:
        res = run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise AdoError("the Azure CLI (az) is not installed", "install the Azure CLI, then run: az login")
    if res.returncode != 0:
        raise AdoError("could not get an Azure DevOps token: %s" % (res.stderr or "").strip(), "az login")
    return (res.stdout or "").strip()


def az_defaults(run=subprocess.run) -> Tuple[Optional[str], Optional[str]]:
    try:
        res = run(["az", "devops", "configure", "--list"], capture_output=True, text=True)
    except FileNotFoundError:
        return None, None
    if res.returncode != 0:
        return None, None
    found = {}  # type: Dict[str, str]
    for line in (res.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            found[key.strip().lower()] = value.strip()
    return found.get("organization") or None, found.get("project") or None


def default_http(method: str, url: str, body: Optional[dict] = None) -> dict:
    headers = {"Authorization": "Bearer " + az_token(), "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AdoError("Azure DevOps refused the request (HTTP %d)" % e.code,
                           "check that your account can read this project and Analytics (View analytics)")
        raise AdoError("Azure DevOps returned HTTP %d for %s" % (e.code, url), "HTTP %d: %s" % (e.code, url))
    except urllib.error.URLError as e:
        raise AdoError("could not reach Azure DevOps: %s" % e.reason, "check your network connection: %s" % url)


def org_name(org: str) -> str:
    parsed = urllib.parse.urlparse(org.strip())
    host = parsed.netloc or parsed.path
    if host.endswith(".visualstudio.com"):
        return host.split(".")[0]
    parts = [p for p in parsed.path.split("/") if p]
    if parts:
        return parts[0]
    return host


def lit(text: str) -> str:
    """Escape a value for an OData or WIQL single-quoted string literal."""
    return text.replace("'", "''")


def _quote(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def _chunks(items: List, size: int = BATCH):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class Client:
    def __init__(self, org: str, project: str, http: Callable = default_http):
        self.org = org.rstrip("/")
        self.project = project
        self.http = http

    def _base(self) -> str:
        return "%s/%s" % (self.org, urllib.parse.quote(self.project))

    def query_ids(self, query: str, since: date) -> List[int]:
        text = query.strip()
        guid = re.search(r"/_queries/query/(%s)" % GUID, text) or re.fullmatch(r"(%s)" % GUID, text)
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

    def odata(self, entity: str, params: Dict[str, str]) -> List[dict]:
        url = "https://analytics.dev.azure.com/%s/%s/_odata/v4.0-preview/%s?%s" % (
            org_name(self.org), urllib.parse.quote(self.project), entity,
            "&".join("%s=%s" % (_quote(k), _quote(v)) for k, v in params.items()))
        rows = []  # type: List[dict]
        while url:
            res = self.http("GET", url, None)
            rows.extend(res.get("value") or [])
            url = res.get("@odata.nextLink")
        return rows

    def levels(self) -> Dict[str, List[str]]:
        res = self.http("GET", "%s/_apis/work/processconfiguration?api-version=7.1" % self._base(), None)

        def names(key: str) -> List[str]:
            return [t["name"] for t in (res.get(key) or {}).get("workItemTypes") or []]
        return {"stories": names("requirementBacklog"), "tasks": names("taskBacklog")}


def _id_list(ids: List[int]) -> str:
    return "(%s)" % ",".join(str(i) for i in ids)


def _flatten(raw: dict) -> dict:
    assigned = (raw.get("AssignedTo") or {}).get("UserEmail")
    tags = raw.get("TagNames")
    return {
        "id": raw["WorkItemId"], "rev": raw["Revision"], "changed": raw.get("ChangedDate"),
        "type": raw.get("WorkItemType"), "state": raw.get("State"), "category": raw.get("StateCategory"),
        "reason": raw.get("Reason"), "iteration": raw.get("IterationPath"), "area": raw.get("AreaPath"),
        "assigned": assigned.lower() if assigned else None, "parent": raw.get("ParentWorkItemId"),
        "title": raw.get("Title"),
        "tags": [t.strip() for t in tags.split(";") if t.strip()] if tags else [],
    }


def fetch(client: Client, query: str, emails: List[str], today: date, history_days: int = 120) -> dict:
    since = date.fromordinal(today.toordinal() - history_days)
    ids = set(client.query_ids(query, since))
    for chunk in _chunks(sorted(ids)):
        for r in client.odata("WorkItems", {"$filter": "ParentWorkItemId in " + _id_list(chunk),
                                            "$select": "WorkItemId"}):
            ids.add(r["WorkItemId"])
    if emails:
        mails = ",".join("'%s'" % lit(e) for e in emails)
        for r in client.odata("WorkItems", {
                "$filter": "Revisions/any(r: r/AssignedTo/UserEmail in (%s) and r/ChangedDate ge %sZ)"
                           % (mails, since.isoformat()),
                "$select": "WorkItemId"}):
            ids.add(r["WorkItemId"])
    rows = []  # type: List[dict]
    for chunk in _chunks(sorted(ids)):
        for raw in client.odata("WorkItemRevisions", {
                "$filter": "WorkItemId in " + _id_list(chunk), "$select": REVISION_FIELDS,
                "$expand": "AssignedTo($select=UserEmail)"}):
            rows.append(_flatten(raw))
    rows.sort(key=lambda r: (r["id"], r["rev"]))
    iterations = [{"path": r["IterationPath"], "name": r["IterationName"],
                   "start": (r.get("StartDate") or "")[:10], "end": (r.get("EndDate") or "")[:10]}
                  for r in client.odata("Iterations", {
                      "$select": "IterationPath,IterationName,StartDate,EndDate",
                      "$filter": "StartDate ne null and EndDate ne null"})]
    iterations.sort(key=lambda i: (i["start"], i["path"]))
    parent_ids = sorted({r["parent"] for r in rows if r["parent"] is not None})
    parents = {}  # type: Dict[str, str]
    for chunk in _chunks(parent_ids):
        for r in client.odata("WorkItems", {"$filter": "WorkItemId in " + _id_list(chunk),
                                            "$select": "WorkItemId,Title"}):
            parents[str(r["WorkItemId"])] = r.get("Title")
    return {"fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "levels": client.levels(), "iterations": iterations, "revisions": rows, "parents": parents}


def save_cache(dir: str, data: dict) -> str:
    path = os.path.join(dir, CACHE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")
    return path


def load_cache(dir: str) -> dict:
    path = os.path.join(dir, CACHE_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError("no cache at %s; run without --offline first" % path)
    with open(path, encoding="utf-8") as f:
        return json.load(f)
