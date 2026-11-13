import io
import json
import os
import re
import subprocess
import tempfile
import unittest
import urllib.error
from datetime import date
from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote

from ledger.ado import (AdoError, Client, az_defaults, az_http, az_token, cache_path, fetch,
                        load_cache, org_name, save_cache)

GUID = "11111111-2222-3333-4444-555555555555"
ORG = "https://dev.azure.com/acme"


def fake_run(returncode=0, stdout="", stderr=""):
    return lambda cmd, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def rev(i, r, **kw):
    row = {"WorkItemId": i, "Revision": r, "ChangedDate": "2026-11-01T10:00:00Z",
           "WorkItemType": "User Story", "State": "Active", "StateCategory": "InProgress",
           "Reason": "Implementation started", "Iteration": {"IterationPath": "Proj\\S1"},
           "Area": {"AreaPath": "Proj\\Claims"},
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

    def test_query_url_forms(self):
        http = Fake()
        c = self.client(http)
        c.query_ids("%s/P/_queries/query-edit/%s" % (ORG, GUID), date(2026, 7, 1))
        self.assertIn("/_apis/wit/wiql/%s?" % GUID, http.calls[0][1])
        with self.assertRaises(ValueError) as e:
            c.query_ids("https://dev.azure.com/acme/P/_workitems/recentlyupdated", date(2026, 7, 1))
        self.assertIn("saved query URL", str(e.exception))

    def test_fetch_flattens_rows_and_lowercases_email(self):
        http = Fake()
        data = fetch(self.client(http), "Proj\\Claims", ["maya.r@vendor.example"], date(2026, 11, 13))
        self.assertEqual((data["revisions"][0]["area"], data["revisions"][0]["iteration"]),
                         ("Proj\\Claims", "Proj\\S1"))
        self.assertEqual([(r["id"], r["rev"]) for r in data["revisions"]], [(1, 1), (2, 2)])
        r = data["revisions"][0]
        self.assertEqual(set(r), {"id", "rev", "changed", "type", "state", "category", "reason", "iteration",
                                  "area", "assigned", "parent", "title", "tags"})
        self.assertEqual(r["assigned"], "maya.r@vendor.example")
        self.assertEqual(r["tags"], ["Blocked", "x"])
        self.assertEqual(data["revisions"][1]["tags"], [])
        self.assertEqual(data["parents"], {"9": "Epic"})
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(save_cache(d, data), data)
            self.assertEqual(load_cache(d), data)
        it_call = [unquote(c[1]) for c in http.calls if "/Iterations?" in c[1]][0]
        self.assertIn("$filter=StartDate ne null and EndDate ne null", it_call)
        for it in data["iterations"]:
            self.assertRegex(it["start"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertRegex(it["end"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual([i["name"] for i in data["iterations"]], ["S1", "S2"])
        self.assertEqual(data["iterations"][0]["start"], "2026-10-19")
        self.assertEqual(data["levels"]["tasks"], ["Task"])
        rev_call = [unquote(c[1]) for c in http.calls if "/WorkItemRevisions?" in c[1]][0]
        self.assertIn("WorkItemId in (1,2,3)", rev_call)
        self.assertIn("$expand=AssignedTo($select=UserEmail),Area($select=AreaPath),"
                      "Iteration($select=IterationPath)", rev_call)
        select = re.search(r"\$select=([^&]*)", rev_call).group(1)
        self.assertNotIn("AreaPath", select)
        self.assertNotIn("IterationPath", select)
        self.assertTrue(any("ChangedDate ge 2026-04-27T00:00:00Z" in unquote(c[1]) for c in http.calls))

    def test_history_days_sets_the_window(self):
        http = Fake()
        fetch(self.client(http), "Proj", ["a@vendor.example"], date(2026, 11, 13), history_days=10)
        self.assertTrue(any("ChangedDate ge 2026-11-03T00:00:00Z" in unquote(c[1]) for c in http.calls))

    def test_no_revisions_is_an_error(self):
        with self.assertRaises(AdoError) as e:
            fetch(self.client(Fake(revisions=[])), "Proj", [], date(2026, 11, 13))
        self.assertIn("the query returned no work items", str(e.exception))
        self.assertIn("check the query and your access", e.exception.next_step)

    def test_query_ids_without_history_are_a_warning(self):
        warnings = []
        fetch(self.client(Fake(wiql_ids=(1, 2, 7, 8))), "Proj", [], date(2026, 11, 13),
              warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 work items from the query returned no history", warnings[0])

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
            with self.assertRaises(AdoError) as e:
                load_cache(d)
            self.assertIn("revisions.json", str(e.exception))
            self.assertEqual(e.exception.next_step, "run without --offline")
            empty = {"fetched": "x", "levels": {}, "iterations": [], "revisions": [], "parents": {}}
            save_cache(d + "/new", empty)
            self.assertEqual(load_cache(d + "/new")["fetched"], "x")

    def test_save_cache_merges_so_data_never_shrinks(self):
        def flat(i, r, it="Proj\\S1"):
            return {"id": i, "rev": r, "changed": "2026-11-01T10:00:00Z", "type": "Task",
                    "state": "Active", "category": "InProgress", "reason": "", "iteration": it,
                    "area": "A", "assigned": None, "parent": None, "title": "T", "tags": []}
        old = {"fetched": "old", "levels": {"stories": ["A"]}, "parents": {"9": "Old", "8": "Kept"},
               "iterations": [{"path": "P\\S1", "name": "S1", "start": "2026-01-01", "end": "2026-01-02"}],
               "revisions": [flat(1, 1), flat(1, 2)]}
        new = {"fetched": "new", "levels": {"stories": ["B"]}, "parents": {"9": "New"},
               "iterations": [{"path": "P\\S2", "name": "S2", "start": "2026-02-01", "end": "2026-02-02"}],
               "revisions": [flat(1, 2, "Proj\\S2"), flat(2, 1)]}
        with tempfile.TemporaryDirectory() as d:
            save_cache(d, old)
            merged = save_cache(d, new)
            self.assertEqual(load_cache(d), merged)
        self.assertEqual([(r["id"], r["rev"]) for r in merged["revisions"]], [(1, 1), (1, 2), (2, 1)])
        self.assertEqual(merged["revisions"][1]["iteration"], "Proj\\S2")
        self.assertEqual([i["name"] for i in merged["iterations"]], ["S1", "S2"])
        self.assertEqual(merged["parents"], {"9": "New", "8": "Kept"})
        self.assertEqual((merged["levels"], merged["fetched"]), ({"stories": ["B"]}, "new"))

    def test_corrupt_or_invalid_cache(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.dirname(cache_path(d)))
            for content in ("{not json", json.dumps({"revisions": []}),
                            json.dumps({"fetched": "x", "levels": {}, "iterations": [], "parents": {},
                                        "revisions": [{"id": 1}]})):
                with open(cache_path(d), "w") as f:
                    f.write(content)
                with self.assertRaises(AdoError) as e:
                    load_cache(d)
                self.assertEqual(e.exception.next_step, "run without --offline")


class HttpTest(unittest.TestCase):
    def response(self, body):
        resp = mock.MagicMock()
        resp.__enter__.return_value.read.return_value = body.encode("utf-8")
        return resp

    def test_token_is_fetched_once_per_client(self):
        runs = []

        def run(cmd, **kw):
            runs.append(kw.get("timeout"))
            return SimpleNamespace(returncode=0, stdout="tok\n", stderr="")
        with mock.patch("urllib.request.urlopen", return_value=self.response('{"a": 1}')) as u:
            http = az_http(run)
            self.assertEqual(http("GET", "https://x/1", None), {"a": 1})
            http("GET", "https://x/2", None)
        self.assertEqual(runs, [60])
        self.assertEqual(u.call_args[0][0].get_header("Authorization"), "Bearer tok")

    def test_http_error_includes_body(self):
        err = urllib.error.HTTPError("https://x", 400, "Bad Request", {},
                                     io.BytesIO(b'{"message": "VS403496: unknown property AreaPath"}' + b"x" * 400))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(AdoError) as e:
                az_http(fake_run(stdout="tok"))("GET", "https://x", None)
        self.assertIn("HTTP 400", str(e.exception))
        self.assertIn("unknown property AreaPath", str(e.exception))
        self.assertLess(len(str(e.exception)), 400)
        self.assertNotIn("HTTP 400", e.exception.next_step)

    def test_non_json_response_is_an_error(self):
        with mock.patch("urllib.request.urlopen", return_value=self.response("<html>sign in</html>")):
            with self.assertRaises(AdoError) as e:
                az_http(fake_run(stdout="tok"))("GET", "https://x", None)
        self.assertIn("not JSON", str(e.exception))

    def test_az_timeout(self):
        def run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        with self.assertRaises(AdoError) as e:
            az_token(run=run)
        self.assertIn("60 seconds", str(e.exception))
        self.assertEqual(az_defaults(run=run), (None, None))


if __name__ == "__main__":
    unittest.main()
