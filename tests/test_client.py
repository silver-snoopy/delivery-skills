import unittest
from datetime import date, datetime, timedelta

from ledger.ado.client import (AdoClient, NotFound, Unreadable,
                               clause_area_under, clause_changed_since,
                               clause_ever_assigned, clause_states_not_in,
                               clause_types, where_all, where_any, wiql_quote)
from ledger.ado.transport import Response, TransportError
from ledger.errors import LedgerError
from ledger.testing.builder import PortfolioBuilder
from ledger.testing.fake_ado import FakeAdo

ORG = "https://dev.azure.com/demo"
SPRINTS = [("S36", "2026-10-12", "2026-10-23"),
           ("S37", "2026-10-26", "2026-11-06")]


def make():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
    return b


def client(fake):
    return AdoClient(fake, ORG, "Demo")


class ScriptedTransport:
    """Answers each request with handler(method, url, body) and records it."""

    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def request(self, method, url, body=None):
        self.requests.append((method, url, body))
        return self.handler(method, url, body)


def _range_of(body):
    import re
    q = body["query"]
    a = int(re.search(r"\[System\.Id\] >= (\d+)", q).group(1))
    m = re.search(r"\[System\.Id\] < (\d+)", q)
    b = int(m.group(1)) if m else None
    return a, b


class ClauseTests(unittest.TestCase):
    def test_clause_helpers(self):
        self.assertEqual(wiql_quote("O'Neil"), "'O''Neil'")
        self.assertEqual(clause_types(["User Story", "Bug"]),
                         "[System.WorkItemType] IN ('User Story', 'Bug')")
        self.assertEqual(clause_ever_assigned("anna@vendor.example"),
                         "EVER [System.AssignedTo] = 'anna@vendor.example'")
        self.assertEqual(clause_states_not_in(["Closed", "Removed"]),
                         "[System.State] NOT IN ('Closed', 'Removed')")
        self.assertEqual(clause_changed_since(date(2026, 8, 1)),
                         "[System.ChangedDate] >= '2026-08-01'")
        self.assertEqual(clause_area_under("Demo\\Claims"),
                         "[System.AreaPath] UNDER 'Demo\\Claims'")
        self.assertEqual(where_all("A", "B"), "A AND B")
        self.assertEqual(where_any("A", "B"), "(A OR B)")


class RevisionTests(unittest.TestCase):
    def _long_ticket(self, n):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        t = b.ticket(1, team="Claims", title="T")
        t0 = datetime(2026, 10, 1, 8, 0)
        for i in range(n):
            t.on((t0 + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M"),
                 state="Active" if i % 2 else "New", assignee=anna)
        return b

    def test_revisions_paged(self):
        fake = FakeAdo(self._long_ticket(450))
        revs = client(fake).revisions(1, page=200)
        self.assertEqual(len(fake.requests), 3)
        self.assertEqual([r["rev"] for r in revs], list(range(1, 451)))
        self.assertIn("$top=200", fake.requests[0][1])
        self.assertIn("$skip=400", fake.requests[2][1])
        self.assertIn("api-version=7.1", fake.requests[0][1])

    def test_revisions_paged_when_server_caps_page(self):
        fake = FakeAdo(self._long_ticket(450), page_cap=150)
        revs = client(fake).revisions(1, page=200)
        self.assertEqual([r["rev"] for r in revs], list(range(1, 451)))
        # 150 + 150 + 150, then an empty page proves the end.
        self.assertEqual(len(fake.requests), 4)
        self.assertIn("$skip=300", fake.requests[2][1])

    def test_revisions_gap_is_error(self):
        def handler(method, url, body):
            if "$skip=0" not in url:
                return Response(200, {}, {"count": 0, "value": []})
            return Response(200, {}, {"count": 3, "value": [
                {"id": 7, "rev": 1, "fields": {}},
                {"id": 7, "rev": 2, "fields": {}},
                {"id": 7, "rev": 4, "fields": {}}]})
        with self.assertRaises(LedgerError):
            AdoClient(ScriptedTransport(handler), ORG, "Demo").revisions(7)

    def test_revisions_server_ignoring_skip_is_error_not_loop(self):
        handler = lambda m, u, b: Response(200, {}, {"count": 2, "value": [
            {"id": 7, "rev": 1, "fields": {}}, {"id": 7, "rev": 2,
                                                "fields": {}}]})
        t = ScriptedTransport(handler)
        with self.assertRaises(LedgerError):
            AdoClient(t, ORG, "Demo").revisions(7, page=2)
        self.assertEqual(len(t.requests), 2)

    def test_revisions_empty_is_error(self):
        handler = lambda m, u, b: Response(200, {}, {"count": 0, "value": []})
        with self.assertRaises(LedgerError):
            AdoClient(ScriptedTransport(handler), ORG, "Demo").revisions(7)

    def test_not_found_and_unreadable(self):
        b = make()
        b.ticket(5, team="Claims", title="T").on("2026-10-26 09:00")
        fake = FakeAdo(b)
        fake.unreadable.add(5)
        c = client(fake)
        with self.assertRaises(NotFound) as nf:
            c.revisions(99)
        self.assertEqual(nf.exception.ticket_id, 99)
        with self.assertRaises(Unreadable) as ur:
            c.revisions(5)
        self.assertEqual(ur.exception.ticket_id, 5)
        self.assertIsInstance(nf.exception, LedgerError)
        self.assertIsInstance(ur.exception, LedgerError)
        handler = lambda m, u, body: Response(401, {}, None)
        with self.assertRaises(Unreadable):
            AdoClient(ScriptedTransport(handler), ORG, "Demo").revisions(5)

    def test_server_error_names_status_and_message(self):
        handler = lambda m, u, b: Response(500, {}, {"message": "boom"})
        with self.assertRaises(LedgerError) as cm:
            AdoClient(ScriptedTransport(handler), ORG, "Demo").revisions(5)
        self.assertIn("500", cm.exception.message)
        self.assertIn("boom", cm.exception.message)


class WiqlTests(unittest.TestCase):
    def _twelve(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        for i in range(12):
            b.ticket(100 + i * 37, team="Claims", title="T").on(
                "2026-10-26 09:00", assignee=anna)
        b.ticket(5000, team="Claims", title="other").on("2026-10-26 09:00")
        return b

    def test_wiql_splits_on_full_batch(self):
        fake = FakeAdo(self._twelve(), wiql_cap=20000)
        where = clause_ever_assigned(fake.alias("p:anna"))
        ids = client(fake).wiql_ids(where, batch_limit=5)
        self.assertEqual(ids, [100 + i * 37 for i in range(12)])
        self.assertGreater(len(fake.requests), 1)
        method, url, body = fake.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, ORG + "/Demo/_apis/wit/wiql?api-version=7.1")
        self.assertTrue(body["query"].startswith(
            "SELECT [System.Id] FROM WorkItems WHERE "
            "[System.TeamProject] = @project AND (" + where + ") AND "
            "[System.Id] >= 1 AND [System.Id] < 2000000"))

    def test_wiql_splits_on_size_error(self):
        fake = FakeAdo(self._twelve(), wiql_cap=3)
        where = clause_ever_assigned(fake.alias("p:anna"))
        seen = []
        ids = client(fake).wiql_ids(
            where, on_partition=lambda r, got: seen.append((r, got)))
        self.assertEqual(ids, [100 + i * 37 for i in range(12)])
        # The first (full-range) query hit the size limit, so it was split.
        first = fake.request(*fake.requests[0])
        self.assertEqual(first.status, 400)
        self.assertIn("VS402337", first.body["message"])
        # Leaves tile the whole range and together hold every id.
        ranges = sorted(r for r, _ in seen)
        self.assertEqual(ranges[0][0], 1)
        self.assertEqual(ranges[-1][1], 2000000)
        for (a1, b1), (a2, b2) in zip(ranges, ranges[1:]):
            self.assertEqual(b1, a2)
        self.assertEqual(sorted(i for _, got in seen for i in got), ids)

    def _ids_fake(self, ids, **kw):
        b = make()
        for i in ids:
            b.ticket(i, team="Claims").on("2026-10-26 09:00")
        return FakeAdo(b, **kw)

    def test_wiql_finds_ids_above_default_range(self):
        want = [5, 1_999_999, 2_000_000, 3_500_000]
        fake = self._ids_fake(want)
        ids = client(fake).wiql_ids(clause_types(["User Story"]))
        self.assertEqual(ids, want)

    def test_wiql_extends_repeatedly(self):
        want = [3, 99, 100, 150, 420, 950]
        fake = self._ids_fake(want)
        seen = []
        ids = client(fake).wiql_ids(
            clause_types(["User Story"]), id_range=(1, 100),
            on_partition=lambda r, got: seen.append(r))
        self.assertEqual(ids, want)
        # 100-200, 200-400, 400-800, 800-1600 were enumerated as extensions.
        self.assertIn((800, 1600), seen)
        probes = [b for _, _, b in fake.requests
                  if "[System.Id] <" not in b["query"]]
        self.assertEqual(len(probes), 5)

    def test_wiql_probe_hitting_size_limit_still_complete(self):
        want = [10, 120, 130, 140, 150]
        fake = self._ids_fake(want, wiql_cap=3)
        ids = client(fake).wiql_ids(clause_types(["User Story"]),
                                    id_range=(1, 100))
        self.assertEqual(ids, want)
        first_probe = next(b for _, _, b in fake.requests
                           if "[System.Id] <" not in b["query"])
        self.assertEqual(fake.request("POST", ORG + "/Demo/_apis/wit/wiql"
                                      "?api-version=7.1", first_probe).status,
                         400)

    def test_wiql_single_empty_probe_when_nothing_above(self):
        fake = self._ids_fake([3, 40])
        ids = client(fake).wiql_ids(clause_types(["User Story"]),
                                    id_range=(1, 100))
        self.assertEqual(ids, [3, 40])
        self.assertEqual(len(fake.requests), 2)
        self.assertNotIn("[System.Id] <", fake.requests[1][2]["query"])
        self.assertTrue(fake.requests[1][2]["query"].endswith(
            "[System.Id] >= 100"))

    def test_wiql_probe_other_error_raises(self):
        def handler(m, u, body):
            if "[System.Id] <" in body["query"]:
                return Response(200, {}, {"workItems": []})
            return Response(500, {}, {"message": "boom"})
        with self.assertRaises(LedgerError):
            AdoClient(ScriptedTransport(handler), ORG, "Demo").wiql_ids(
                "X", id_range=(1, 10))

    def test_wiql_extension_guard(self):
        def handler(m, u, body):
            if "[System.Id] <" in body["query"]:
                return Response(200, {}, {"workItems": []})
            return Response(200, {}, {"workItems": [{"id": 5}]})  # probe
        with self.assertRaises(LedgerError):
            AdoClient(ScriptedTransport(handler), ORG, "Demo").wiql_ids(
                "X", id_range=(1, 2))

    def test_wiql_splits_on_timeout_and_transport_error(self):
        calls = {"n": 0}

        def handler(method, url, body):
            a, b = _range_of(body)
            if b is None:
                return Response(200, {}, {"workItems": []})
            calls["n"] += 1
            if b - a > 500:
                if calls["n"] % 2:
                    raise TransportError("reset")
                return Response(408, {}, None)
            return Response(200, {}, {"workItems": [
                {"id": i} for i in (10, 700) if a <= i < b]})
        ids = AdoClient(ScriptedTransport(handler), ORG, "Demo").wiql_ids(
            "X", id_range=(1, 2001))
        self.assertEqual(ids, [10, 700])

    def test_wiql_width_one_failure_raises(self):
        handler = lambda m, u, b: Response(
            400, {}, {"message": "VS402337: exceeds the size limit"})
        with self.assertRaises(LedgerError):
            AdoClient(ScriptedTransport(handler), ORG, "Demo").wiql_ids(
                "X", id_range=(1, 9))

    def test_wiql_other_error_raises_without_split(self):
        t = ScriptedTransport(lambda m, u, b: Response(
            400, {}, {"message": "TF51005: bad field"}))
        with self.assertRaises(LedgerError) as cm:
            AdoClient(t, ORG, "Demo").wiql_ids("X")
        self.assertEqual(len(t.requests), 1)
        self.assertIn("TF51005", cm.exception.message)

    def test_wiql_dedupes_and_sorts(self):
        handler = lambda m, u, b: Response(200, {}, {"workItems": [
            {"id": 9}, {"id": 3}, {"id": 9}]
            if "[System.Id] <" in b["query"] else []})
        ids = AdoClient(ScriptedTransport(handler), ORG, "Demo").wiql_ids(
            "X", id_range=(1, 10))
        self.assertEqual(ids, [3, 9])

    def test_wiql_missing_work_items_is_error(self):
        handler = lambda m, u, b: Response(200, {}, {"queryType": "flat"})
        with self.assertRaises(LedgerError):
            AdoClient(ScriptedTransport(handler), ORG, "Demo").wiql_ids("X")


class MetadataTests(unittest.TestCase):
    def test_iterations_parses_dates_and_missing_dates(self):
        b = PortfolioBuilder()
        b.team("Claims Team", area="Demo\\Claims", sprints=SPRINTS)
        fake = FakeAdo(b)
        fake.dateless_iteration = True
        its = client(fake).iterations("Claims Team")
        self.assertIn("/Demo/Claims%20Team/_apis/work/teamsettings/"
                      "iterations?api-version=7.1", fake.requests[0][1])
        named = {i["name"]: i for i in its}
        self.assertEqual(named["S37"]["start"], date(2026, 10, 26))
        self.assertEqual(named["S37"]["finish"], date(2026, 11, 6))
        self.assertEqual(named["S37"]["path"], "Demo\\Claims Team\\S37")
        dateless = [i for i in its if i["start"] is None]
        self.assertEqual(len(dateless), 1)
        self.assertIsNone(dateless[0]["finish"])
        self.assertEqual(set(its[0]), {"id", "name", "path", "start",
                                       "finish"})

    def test_board_columns_with_split(self):
        b = make()
        t = b.ticket(1, team="Claims", title="T")
        t.on("2026-10-26 09:00", column="New")
        t.on("2026-10-27 09:00", column="Dev", column_done=False)
        t.on("2026-10-28 09:00", column="Dev", column_done=True)
        t.on("2026-10-29 09:00", column="Done", column_done=None)
        fake = FakeAdo(b)
        boards = client(fake).board_columns("Claims")
        self.assertEqual(len(boards), 1)
        bd = boards[0]
        self.assertEqual(bd["column_field"], "WEF_FAKE_Kanban.Column")
        self.assertEqual(bd["done_field"], "WEF_FAKE_Kanban.Column.Done")
        self.assertEqual(bd["columns"], [
            {"name": "New", "split": False}, {"name": "Dev", "split": True},
            {"name": "Done", "split": False}])
        self.assertEqual(len(fake.requests), 2)

    def test_states(self):
        fake = FakeAdo(make())
        states = client(fake).states("User Story")
        self.assertIn("/_apis/wit/workitemtypes/User%20Story/states",
                      fake.requests[0][1])
        cats = {s["name"]: s["category"] for s in states}
        self.assertEqual(cats["New"], "Proposed")
        self.assertEqual(cats["In QA"], "InProgress")
        self.assertEqual(cats["Closed"], "Completed")
        self.assertEqual(cats["Removed"], "Removed")

    def test_connection(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        fake = FakeAdo(b, account="p:anna")
        me = client(fake).connection()
        self.assertEqual(me, {"id": fake.identity("p:anna")["id"],
                              "name": "Anna K."})
        self.assertTrue(fake.requests[0][1].startswith(
            ORG + "/_apis/connectionData?api-version=7.1"))


if __name__ == "__main__":
    unittest.main()
