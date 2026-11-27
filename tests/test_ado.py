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

from ledger.ado import (
    AdoError,
    Client,
    _flatten,
    az_defaults,
    az_http,
    az_token,
    cache_path,
    fetch,
    load_cache,
    save_cache,
)

GUID = "11111111-2222-3333-4444-555555555555"
ORG = "https://dev.azure.com/acme"


def fake_run(returncode=0, stdout="", stderr=""):
    return lambda cmd, **kw: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def rev(
    i,
    r,
    parent=None,
    tags=None,
    assigned="Maya.R@Vendor.example",
    state="Active",
    iteration="Proj\\S1",
    changed="2026-11-01T10:00:00Z",
):
    return {
        "id": i,
        "rev": r,
        "fields": {
            "System.ChangedDate": changed,
            "System.WorkItemType": "User Story",
            "System.State": state,
            "System.Reason": "Implementation started",
            "System.IterationPath": iteration,
            "System.AreaPath": "Proj\\Claims",
            "System.Parent": parent,
            "System.Title": f"T{i}",
            "System.Tags": tags,
            "System.AssignedTo": {"uniqueName": assigned} if assigned else None,
        },
    }


NODES = {
    "name": "Proj",
    "path": "\\Proj\\Iteration",
    "children": [
        {
            "name": "S2",
            "path": "\\Proj\\Iteration\\S2",
            "attributes": {"startDate": "2026-11-02T00:00:00Z", "finishDate": "2026-11-13T00:00:00Z"},
        },
        {
            "name": "Team A",
            "path": "\\Proj\\Iteration\\Team A",
            "children": [
                {
                    "name": "S1",
                    "path": "\\Proj\\Iteration\\S1",
                    "attributes": {"startDate": "2026-10-19T00:00:00Z", "finishDate": "2026-10-30T00:00:00Z"},
                },
                {"name": "Backlog", "path": "\\Proj\\Iteration\\Team A\\Backlog", "attributes": {}},
            ],
        },
    ],
}


class Fake:
    def __init__(self, revisions=None, wiql_ids=(1, 2), status=None):
        self.calls = []
        self.revisions = (
            revisions if revisions is not None else [rev(2, 2), rev(1, 1, parent=9, tags="Blocked; x")]
        )
        self.wiql_ids = wiql_ids
        self.status = status or {}

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        u = unquote(url)
        m = re.search(r"/workitems/(\d+)/revisions\?\$top=(\d+)&\$skip=(\d+)", u)
        if m:
            wid, top, skip = (int(x) for x in m.groups())
            if wid in self.status:
                raise AdoError(f"HTTP {self.status[wid]}", code=self.status[wid])
            mine = [r for r in self.revisions if r["id"] == wid]
            return {"value": mine[skip : skip + top]}
        if "/_apis/wit/wiql" in u:
            query = (body or {}).get("query", "")
            if "[System.Parent] IN" in query or "EVER" in query:
                return {"workItems": [{"id": 3}]}
            return {"workItems": [{"id": i} for i in self.wiql_ids]}
        if "processconfiguration" in u:
            return {
                "requirementBacklog": {"workItemTypes": [{"name": "User Story"}]},
                "taskBacklog": {"workItemTypes": [{"name": "Task"}]},
            }
        if "/workitemtypes/" in u and u.split("?")[0].endswith("/states"):
            return {
                "value": [
                    {"name": "Active", "category": "InProgress"},
                    {"name": "Closed", "category": "Completed"},
                ]
            }
        if "classificationnodes/Iterations" in u:
            return NODES
        if "workitemsbatch" in u:
            return {
                "value": [
                    {"id": i, "fields": {"System.Title": "Epic"}} if i == 9 else None for i in body["ids"]
                ]
            }
        raise AssertionError(url)

    def revision_ids(self):
        return sorted(
            {
                int(re.search(r"/workitems/(\d+)/revisions", c[1]).group(1))
                for c in self.calls
                if "/revisions?" in c[1]
            }
        )

    def wiql_queries(self):
        return [c[2]["query"] for c in self.calls if c[2] and "query" in c[2]]


class AdoTest(unittest.TestCase):
    def client(self, http):
        return Client(ORG, "My Project", http)

    def test_query_kinds(self):
        http = Fake()
        c = self.client(http)
        c.query_ids(f"{ORG}/My%20Project/_queries/query/{GUID}/", date(2026, 7, 1))
        c.query_ids(GUID, date(2026, 7, 1))
        self.assertEqual(http.calls[0][0], "GET")
        self.assertIn(f"/_apis/wit/wiql/{GUID}?api-version=7.1", http.calls[0][1])
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
            return {
                "workItemRelations": [
                    {"source": None, "target": {"id": 5}},
                    {"source": {"id": 5}, "target": {"id": 6}},
                ]
            }

        self.assertEqual(self.client(http).query_ids(GUID, date(2026, 7, 1)), [5, 6])

    def test_levels(self):
        self.assertEqual(self.client(Fake()).levels(), {"stories": ["User Story"], "tasks": ["Task"]})

    def test_query_url_forms(self):
        http = Fake()
        c = self.client(http)
        c.query_ids(f"{ORG}/P/_queries/query-edit/{GUID}", date(2026, 7, 1))
        self.assertIn(f"/_apis/wit/wiql/{GUID}?", http.calls[0][1])
        with self.assertRaises(ValueError) as e:
            c.query_ids("https://dev.azure.com/acme/P/_workitems/recentlyupdated", date(2026, 7, 1))
        self.assertIn("saved query URL", str(e.exception))

    def test_fetch_flattens_rows_and_lowercases_email(self):
        http = Fake()
        data = fetch(self.client(http), "Proj\\Claims", ["maya.r@vendor.example"], date(2026, 11, 13))
        self.assertEqual(
            (data["revisions"][0]["area"], data["revisions"][0]["iteration"]), ("Proj\\Claims", "Proj\\S1")
        )
        self.assertEqual([(r["id"], r["rev"]) for r in data["revisions"]], [(1, 1), (2, 2)])
        r = data["revisions"][0]
        self.assertEqual(
            set(r),
            {
                "id",
                "rev",
                "changed",
                "type",
                "state",
                "category",
                "reason",
                "iteration",
                "area",
                "assigned",
                "parent",
                "title",
                "tags",
            },
        )
        self.assertEqual(r["assigned"], "maya.r@vendor.example")
        self.assertEqual(r["category"], "InProgress")
        self.assertEqual(r["parent"], 9)
        self.assertEqual(r["tags"], ["Blocked", "x"])
        self.assertEqual(data["revisions"][1]["tags"], [])
        self.assertEqual(data["parents"], {"9": "Epic"})
        with tempfile.TemporaryDirectory() as d:
            saved = {k: v for k, v in data.items() if k != "requested"}
            self.assertEqual(data["requested"], [1, 2, 3])
            self.assertEqual(save_cache(d, data), saved)
            self.assertEqual(load_cache(d), saved)
        self.assertEqual(
            data["iterations"],
            [
                {"path": "Proj\\S1", "name": "S1", "start": "2026-10-19", "end": "2026-10-30"},
                {"path": "Proj\\S2", "name": "S2", "start": "2026-11-02", "end": "2026-11-13"},
            ],
        )
        self.assertEqual(data["levels"]["tasks"], ["Task"])
        self.assertEqual(http.revision_ids(), [1, 2, 3])
        ever = next(q for q in http.wiql_queries() if "EVER" in q)
        self.assertIn("[System.ChangedDate] >= '2026-04-27'", ever)
        self.assertIn("[System.AssignedTo] EVER 'maya.r@vendor.example'", ever)
        self.assertTrue(
            ever.startswith("SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project AND ")
        )

    def test_history_days_sets_the_window(self):
        http = Fake()
        fetch(self.client(http), "Proj", ["a@vendor.example"], date(2026, 11, 13), history_days=10)
        self.assertTrue(any("[System.ChangedDate] >= '2026-11-03'" in q for q in http.wiql_queries()))

    def test_no_revisions_is_an_error(self):
        with self.assertRaises(AdoError) as e:
            fetch(self.client(Fake(revisions=[])), "Proj", [], date(2026, 11, 13))
        self.assertIn("the query returned no work items", str(e.exception))
        self.assertIn("check the query and your access", e.exception.next_step)

    def test_query_ids_without_history_are_a_warning(self):
        warnings = []
        fetch(self.client(Fake(wiql_ids=(1, 2, 7, 8))), "Proj", [], date(2026, 11, 13), warn=warnings.append)
        self.assertEqual(len(warnings), 1)
        self.assertIn("2 work items from the query returned no history", warnings[0])

    def test_unassigned_row(self):
        http = Fake(revisions=[rev(1, 1, assigned=None)])
        data = fetch(self.client(http), "Proj", [], date(2026, 11, 13))
        self.assertIsNone(data["revisions"][0]["assigned"])

    def test_parent_queries_are_batched_by_200(self):
        http = Fake(wiql_ids=range(1, 451))
        fetch(self.client(http), "Proj", [], date(2026, 11, 13))
        self.assertEqual(len([q for q in http.wiql_queries() if "[System.Parent] IN" in q]), 3)

    def test_emails_are_batched_by_20(self):
        http = Fake()
        fetch(self.client(http), "Proj", [f"e{i}@x.example" for i in range(45)], date(2026, 11, 13))
        self.assertEqual(len([q for q in http.wiql_queries() if "EVER" in q]), 3)

    def test_unreadable_item_has_no_history_and_other_errors_stop(self):
        for code in (403, 404):
            self.assertEqual(self.client(Fake(status={1: code})).revisions(1), [])
        with self.assertRaises(AdoError):
            self.client(Fake(status={1: 500})).revisions(1)

    def test_revisions_follow_pages(self):
        http = Fake(revisions=[rev(1, r) for r in range(1, 206)])
        self.assertEqual(len(self.client(http).revisions(1)), 205)
        self.assertEqual(len([c for c in http.calls if "/revisions?" in c[1]]), 2)
        self.assertIn("$skip=200", unquote(http.calls[1][1]))

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
            return {
                "id": i,
                "rev": r,
                "changed": "2026-11-01T10:00:00Z",
                "type": "Task",
                "state": "Active",
                "category": "InProgress",
                "reason": "",
                "iteration": it,
                "area": "A",
                "assigned": None,
                "parent": None,
                "title": "T",
                "tags": [],
            }

        old = {
            "fetched": "old",
            "levels": {"stories": ["A"]},
            "parents": {"9": "Old", "8": "Kept"},
            "iterations": [{"path": "Proj\\S1", "name": "S1", "start": "2026-01-01", "end": "2026-01-02"}],
            "revisions": [flat(1, 1), flat(1, 2), flat(4, 1)],
        }
        new = {
            "fetched": "new",
            "levels": {"stories": ["B"]},
            "parents": {"9": "New"},
            "iterations": [{"path": "Proj\\S2", "name": "S2", "start": "2026-02-01", "end": "2026-02-02"}],
            "revisions": [flat(1, 2, "Proj\\S2"), flat(2, 1)],
        }
        with tempfile.TemporaryDirectory() as d:
            save_cache(d, old)
            merged = save_cache(d, new)
            self.assertEqual(load_cache(d), merged)
        # an item the new fetch returns takes its rows from it; item 4 was not asked for and stays
        self.assertEqual([(r["id"], r["rev"]) for r in merged["revisions"]], [(1, 2), (2, 1), (4, 1)])
        self.assertEqual(merged["revisions"][0]["iteration"], "Proj\\S2")
        self.assertEqual([i["name"] for i in merged["iterations"]], ["S1", "S2"])
        self.assertEqual(merged["parents"], {"9": "New", "8": "Kept"})
        self.assertEqual((merged["levels"], merged["fetched"]), ({"stories": ["B"]}, "new"))

    def test_refetch_drops_gone_items_and_keeps_newest_state(self):
        def raw(i, r, it):
            return rev(i, r, iteration=it, changed=f"2026-11-0{r}T10:00:00Z")

        cats = {("User Story", "Active"): "InProgress"}
        old = {
            "fetched": "old",
            "levels": {},
            "parents": {},
            "iterations": [
                {"path": "Proj\\Old\\S30", "name": "S30", "start": "2026-11-10", "end": "2026-11-21"}
            ],
            "revisions": [
                _flatten(raw(5, 1, "Proj\\Old\\S30"), cats),
                _flatten(raw(6, 1, "Proj\\Old\\S30"), cats),
            ],
        }
        http = Fake(
            revisions=[raw(1, 1, "Proj\\S1"), raw(6, 1, "Proj\\S2"), raw(6, 2, "Proj\\S2")], wiql_ids=(1,)
        )
        warnings = []
        with tempfile.TemporaryDirectory() as d:
            save_cache(d, old)
            new = fetch(self.client(http), "Proj", [], date(2026, 11, 13), known=[5, 6], warn=warnings.append)
            self.assertEqual(http.revision_ids(), [1, 3, 5, 6])
            merged = save_cache(d, new)
            self.assertEqual(load_cache(d), merged)
        self.assertEqual(sorted({r["id"] for r in merged["revisions"]}), [1, 6])
        self.assertEqual(
            [r["iteration"] for r in merged["revisions"] if r["id"] == 6], ["Proj\\S2", "Proj\\S2"]
        )
        self.assertEqual(
            warnings,
            ["1 saved work items returned no history and were dropped (deleted or no longer readable): #5"],
        )
        self.assertNotIn("Proj\\Old\\S30", [i["path"] for i in merged["iterations"]])
        self.assertNotIn("requested", merged)

    def test_unreadable_cache_is_replaced_with_a_warning(self):
        warnings = []
        empty = {"fetched": "x", "levels": {}, "iterations": [], "revisions": [], "parents": {}}
        with tempfile.TemporaryDirectory() as d:
            save_cache(d, empty, warnings.append)
            self.assertEqual(warnings, [])
            with open(cache_path(d), "w") as f:
                f.write("{broken")
            save_cache(d, empty, warnings.append)
        self.assertEqual(
            warnings,
            [
                "the saved board data was unreadable and has been replaced; "
                "older sprints may have lost history"
            ],
        )

    def test_fetch_checks_rows(self):
        with self.assertRaises(AdoError):
            fetch(self.client(Fake(revisions=[rev(1, 1, state="Weird")])), "Proj", [], date(2026, 11, 13))

    def test_corrupt_or_invalid_cache(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.dirname(cache_path(d)))
            for content in (
                "{not json",
                json.dumps({"revisions": []}),
                json.dumps(
                    {"fetched": "x", "levels": {}, "iterations": [], "parents": {}, "revisions": [{"id": 1}]}
                ),
            ):
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
        err = urllib.error.HTTPError(
            "https://x",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"message": "VS403496: unknown property AreaPath"}' + b"x" * 400),
        )
        with mock.patch("urllib.request.urlopen", side_effect=err), self.assertRaises(AdoError) as e:
            az_http(fake_run(stdout="tok"))("GET", "https://x", None)
        self.assertIn("HTTP 400", str(e.exception))
        self.assertIn("unknown property AreaPath", str(e.exception))
        self.assertLess(len(str(e.exception)), 400)
        self.assertNotIn("HTTP 400", e.exception.next_step)

    def test_dropped_connection_is_retried_then_an_error(self):
        import http.client

        err = http.client.RemoteDisconnected("Remote end closed connection without response")
        with (
            mock.patch("urllib.request.urlopen", side_effect=err) as u,
            mock.patch("time.sleep") as sleep,
            self.assertRaises(AdoError) as e,
        ):
            az_http(fake_run(stdout="tok"))("GET", "https://x", None)
        self.assertEqual(u.call_count, 3)
        self.assertEqual([c[0][0] for c in sleep.call_args_list], [2, 4])
        self.assertIn("could not reach Azure DevOps", str(e.exception))
        self.assertIn("check your network connection", e.exception.next_step)

    def test_connection_recovers_on_retry(self):
        err = urllib.error.URLError("reset")
        with (
            mock.patch("urllib.request.urlopen", side_effect=[err, self.response('{"a": 1}')]),
            mock.patch("time.sleep"),
        ):
            self.assertEqual(az_http(fake_run(stdout="tok"))("GET", "https://x", None), {"a": 1})

    def test_http_status_is_not_retried(self):
        err = urllib.error.HTTPError("https://x", 403, "Forbidden", {}, io.BytesIO(b"no"))
        with (
            mock.patch("urllib.request.urlopen", side_effect=err) as u,
            mock.patch("time.sleep"),
            self.assertRaises(AdoError) as e,
        ):
            az_http(fake_run(stdout="tok"))("GET", "https://x", None)
        self.assertEqual(u.call_count, 1)
        self.assertEqual(e.exception.code, 403)
        self.assertEqual(e.exception.next_step, "check that your account can read this project")

    def test_non_json_response_is_an_error(self):
        with (
            mock.patch("urllib.request.urlopen", return_value=self.response("<html>sign in</html>")),
            self.assertRaises(AdoError) as e,
        ):
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
