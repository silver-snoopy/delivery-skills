"""Small builder for revision caches used by every test that needs board history."""
from datetime import datetime, timezone
from typing import Dict, List

from ledger.replay import local_tz

_CATEGORY = {"New": "Proposed", "To Do": "Proposed", "Active": "InProgress",
             "In Progress": "InProgress", "Committed": "InProgress", "Ready for QA": "InProgress",
             "In Review": "InProgress", "Resolved": "Resolved", "Closed": "Completed",
             "Done": "Completed", "Removed": "Removed"}
_KEEP = object()


class Item:
    def __init__(self, board: "Board", id: int, type: str, parent, title, area: str):
        self.board, self.id, self.type, self.parent = board, id, type, parent
        self.title = title if title is not None else "Item %d" % id
        self.area = area
        self.rows = []  # type: List[dict]
        self.cur = {"state": "New", "category": "Proposed", "iteration": None, "assigned": None,
                    "reason": "", "tags": []}

    def at(self, when: str, state=_KEEP, category=_KEEP, iteration=_KEEP, assigned=_KEEP,
           reason=_KEEP, tags=_KEEP) -> "Item":
        if state is not _KEEP:
            self.cur["state"] = state
            if category is _KEEP and state in _CATEGORY:
                self.cur["category"] = _CATEGORY[state]
        for key, value in (("category", category), ("iteration", iteration), ("assigned", assigned),
                           ("reason", reason), ("tags", tags)):
            if value is not _KEEP:
                self.cur[key] = list(value) if key == "tags" else value
        local = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=self.board.tz)
        utc = local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.rows.append({"id": self.id, "rev": len(self.rows) + 1, "changed": utc,
                          "type": self.type, "area": self.area, "parent": self.parent,
                          "title": self.title, **{k: (list(v) if k == "tags" else v)
                                                  for k, v in self.cur.items()}})
        return self


class Board:
    def __init__(self, tz: str = "Europe/Zurich"):
        self.tz = local_tz(tz)
        self.sprints = []  # type: List[dict]
        self.items = []  # type: List[Item]

    def sprint(self, name: str, start: str, end: str, path: str = None) -> "Board":
        self.sprints.append({"path": path if path is not None else "Proj\\" + name, "name": name,
                             "start": start, "end": end})
        return self

    def item(self, id: int, type: str = "User Story", parent=None, title=None,
             area: str = "Proj\\Claims") -> Item:
        item = Item(self, id, type, parent, title, area)
        self.items.append(item)
        return item

    def cache(self) -> dict:
        rows = sorted((r for i in self.items for r in i.rows), key=lambda r: (r["id"], r["rev"]))
        known = {i.id: i.title for i in self.items}  # type: Dict[int, str]
        parents = {str(r["parent"]): known.get(r["parent"], "Parent %s" % r["parent"])
                   for r in rows if r["parent"] is not None}
        return {"fetched": "2026-11-13T00:00:00Z",
                "levels": {"stories": ["User Story", "Product Backlog Item"], "tasks": ["Task"]},
                "iterations": sorted((dict(s) for s in self.sprints),
                                     key=lambda s: (s["start"], s["path"])),
                "revisions": rows, "parents": parents}
