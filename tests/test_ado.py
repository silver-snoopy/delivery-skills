import json
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from urllib.parse import unquote

from ledger.ado import (AdoError, Client, az_defaults, az_token, fetch, load_cache, org_name,
                        save_cache)

GUID = "11111111-2222-3333-4444-555555555555"
ORG = "https://dev.azure.com/acme"


def fake_run(returncode=0, stdout="", stderr=""):
    return lambda cmd, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def rev(i, r, **kw):
    row = {"WorkItemId": i, "Revision": r, "ChangedDate": "2026-11-01T10:00:00Z",
           "WorkItemType": "User Story", "State": "Active", "StateCategory": "InProgress",
           "Reason": "Implementation started", "IterationPath": "Proj\\S1", "AreaPath": "Proj\\Claims",
           "ParentWorkItemId": None, "Title": "T%d" % i, "TagNames": None,
           "AssignedTo": {"UserEmail": "Maya.R@Vendor.example"}}
    row.update(kw)
    return row


class Fake:
    def __init__(self, revisions=None, wiql_ids=(1, 2)):
        self.calls = []
        self.revisions = revisions if revisions is not None else [rev(2, 2), rev(1, 1, ParentWorkItemId=9, TagNames="Blocked; x")]
        self.wiql_ids = wiql_ids

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        u = unquote(url)
        if "/_apis/wit/wiql" in u:
            return {"workItems": [{"id": i} for i in self.wiql_ids]}
        if "processconfiguration" in u:
            return {"requirementBacklog": {"workItemTypes": [{"name": "User Story"}]},
                    "taskBacklog": {"workItemTypes": [{"name": "Task"}]}}
        if "/WorkItemRevisions?" in u:
            return {"value": self.revisions}
        if "/Iterations?" in u:
            return {"value": [
                {"IterationPath": "Proj\\S2", "IterationName": "S2", "StartDate": "2026-11-02T00:00:00Z", "EndDate": "2026-11-13T00:00:00Z"},
                {"IterationPath": "Proj\\S1", "IterationName": "S1", "StartDate": "2026-10-19T00:00:00Z", "EndDate": "2026-10-30T00:00:00Z"}]}
        if "ParentWorkItemId in" in u or "Revisions/any" in u:
            return {"value": [{"WorkItemId": 3}]}
        if "$select=WorkItemId,Title" in u:
            return {"value": [{"WorkItemId": 9, "Title": "Epic"}]}
        raise AssertionError(url)


class AdoTest(unittest.TestCase):
    def client(self, http):
        return Client(ORG, "My Project", http)

    def test_query_kinds(self):
        http = Fake()
        c = self.client(http)
        c.query_ids("%s/My%%20Project/_queries/query/%s/" % (ORG, GUID), date(2026, 7, 1))
        c.query_ids(GUID, date(2026, 7, 1))
        self.assertEqual(http.calls[0][0], "GET")
        self.assertIn("/_apis/wit/wiql/%s?api-version=7.1" % GUID, http.calls[0][1])
        self.assertEqual(http.calls[1][0], "GET")
        ids = c.query_ids("select [System.Id] from WorkItems", date(2026, 7, 1))
        self.assertEqual(ids, [1, 2])
        self.assertEqual(http.calls[2][0], "POST")
        self.assertEqual(http.calls[2][2], {"query": "select [System.Id] from WorkItems"})
        c.query_ids("Proj\\Cla'ims", date(2026, 7, 1))
        self.assertIn("UNDER 'Proj\\Cla''ims'", http.calls[3][2]["query"])
        self.assertIn("[System.ChangedDate] >= '2026-07-01'", http.calls[3][2]["query"])

    def test_link_query_uses_relation_targets(self):
        def http(m, u, b):
            return {"workItemRelations": [{"source": None, "target": {"id": 5}},
                                          {"source": {"id": 5}, "target": {"id": 6}}]}
        self.assertEqual(self.client(http).query_ids(GUID, date(2026, 7, 1)), [5, 6])

    def test_odata_follows_next_link(self):
        pages = [{"value": [{"a": 1}], "@odata.nextLink": "https://next/page2"}, {"value": [{"a": 2}]}]
        calls = []

        def http(m, u, b):
            calls.append(u)
            return pages[len(calls) - 1]
        rows = self.client(http).odata("WorkItems", {"$filter": "Title eq 'it''s'", "$select": "A"})
        self.assertEqual(rows, [{"a": 1}, {"a": 2}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], "https://next/page2")
        self.assertTrue(calls[0].startswith("https://analytics.dev.azure.com/acme/My%20Project/_odata/v4.0-preview/WorkItems?"))
        self.assertNotIn(" ", calls[0])
        self.assertIn("%24filter=Title%20eq%20%27it%27%27s%27", calls[0])

    def test_org_name(self):
        self.assertEqual(org_name("https://dev.azure.com/acme/"), "acme")
        self.assertEqual(org_name("https://acme.visualstudio.com"), "acme")

    def test_levels(self):
        self.assertEqual(self.client(Fake()).levels(), {"stories": ["User Story"], "tasks": ["Task"]})

    def test_fetch_flattens_rows_and_lowercases_email(self):
        http = Fake()
        data = fetch(self.client(http), "Proj\\Claims", ["maya.r@vendor.example"], date(2026, 11, 13))
        self.assertEqual([(r["id"], r["rev"]) for r in data["revisions"]], [(1, 1), (2, 2)])
        r = data["revisions"][0]
        self.assertEqual(set(r), {"id", "rev", "changed", "type", "state", "category", "reason", "iteration",
                                  "area", "assigned", "parent", "title", "tags"})
        self.assertEqual(r["assigned"], "maya.r@vendor.example")
        self.assertEqual(r["tags"], ["Blocked", "x"])
        self.assertEqual(data["revisions"][1]["tags"], [])
        self.assertEqual(data["parents"], {9: "Epic"})
        self.assertEqual([i["name"] for i in data["iterations"]], ["S1", "S2"])
        self.assertEqual(data["iterations"][0]["start"], "2026-10-19")
        self.assertEqual(data["levels"]["tasks"], ["Task"])
        rev_call = [unquote(c[1]) for c in http.calls if "/WorkItemRevisions?" in c[1]][0]
        self.assertIn("WorkItemId in (1,2,3)", rev_call)
        self.assertIn("$expand=AssignedTo($select=UserEmail)", rev_call)
        self.assertTrue(any("ChangedDate ge 2026-07-16Z" in unquote(c[1]) for c in http.calls))

    def test_unassigned_row(self):
        http = Fake(revisions=[rev(1, 1, AssignedTo=None)])
        data = fetch(self.client(http), "Proj", [], date(2026, 11, 13))
        self.assertIsNone(data["revisions"][0]["assigned"])

    def test_ids_are_batched_by_200(self):
        http = Fake(wiql_ids=range(1, 451))
        fetch(self.client(http), "Proj", [], date(2026, 11, 13))
        self.assertEqual(len([c for c in http.calls if "/WorkItemRevisions?" in c[1]]), 3)

    def test_az_not_logged_in(self):
        with self.assertRaises(AdoError) as e:
            az_token(run=fake_run(returncode=1, stderr="Please run 'az login'"))
        self.assertEqual(e.exception.next_step, "az login")
        self.assertEqual(az_token(run=fake_run(stdout="tok\n")), "tok")

    def test_az_defaults(self):
        out = "[defaults]\norganization = https://dev.azure.com/acme\nproject = Proj\n"
        self.assertEqual(az_defaults(run=fake_run(stdout=out)), ("https://dev.azure.com/acme", "Proj"))
        self.assertEqual(az_defaults(run=fake_run(returncode=1)), (None, None))

    def test_cache_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError) as e:
                load_cache(d)
            self.assertIn("revisions.json", str(e.exception))
            save_cache(d + "/new", {"fetched": "x", "revisions": []})
            self.assertEqual(load_cache(d + "/new")["fetched"], "x")


if __name__ == "__main__":
    unittest.main()
