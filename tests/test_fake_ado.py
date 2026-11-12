import unittest

from ledger.ado.client import (clause_area_under, clause_changed_since,
                               clause_ever_assigned, clause_states_not_in,
                               clause_types, where_all, where_any)
from ledger.ado.transport import TransportError
from ledger.testing.builder import PortfolioBuilder
from ledger.testing.fake_ado import FakeAdo

ORG = "https://dev.azure.com/demo"
WIQL_URL = ORG + "/Demo/_apis/wit/wiql?api-version=7.1"


def make():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims",
           sprints=[("S37", "2026-10-26", "2026-11-06")])
    return b


def query(where, a=1, b=2000000):
    return {"query": "SELECT [System.Id] FROM WorkItems WHERE "
                     "[System.TeamProject] = @project AND (%s) AND "
                     "[System.Id] >= %d AND [System.Id] < %d" % (where, a, b)}


def ids(resp):
    return sorted(w["id"] for w in resp.body["workItems"])


def revs_url(tid, top=200, skip=0):
    return (ORG + "/Demo/_apis/wit/workItems/%d/revisions?$top=%d&$skip=%d"
            "&$expand=fields&api-version=7.1" % (tid, top, skip))


class IdentityTests(unittest.TestCase):
    def test_identity_generation_is_deterministic(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        f1, f2 = FakeAdo(b), FakeAdo(b)
        anna = f1.identity("p:anna")
        self.assertEqual(anna, f2.identity("p:anna"))
        self.assertEqual(anna["displayName"], "Anna K.")
        self.assertEqual(anna["uniqueName"], "anna@vendor.example")
        self.assertEqual(f1.alias("p:anna"), "anna@vendor.example")
        self.assertEqual(set(anna), {"displayName", "uniqueName", "id",
                                     "descriptor"})
        other = f1.identity(b.other("qa"))
        self.assertEqual(other, f2.identity(b.other("qa")))
        self.assertTrue(other["displayName"].startswith("Other "))
        self.assertTrue(other["uniqueName"].endswith("@customer.example"))
        self.assertNotEqual(other["id"], anna["id"])
        self.assertEqual(f1.roster_patch(), {"anna": {
            "identity_ids": [anna["id"]],
            "aliases": ["anna@vendor.example"]}})

    def test_supplied_identity_wins(self):
        b = make()
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        given = {"displayName": "A", "uniqueName": "a@x.example",
                 "id": "1", "descriptor": "aad.1"}
        fake = FakeAdo(b, identities={"p:anna": given})
        self.assertEqual(fake.identity("p:anna"), given)


class PayloadTests(unittest.TestCase):
    def test_payload_fields(self):
        b = make()
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        t = b.ticket(3, team="Claims", title="Fix it",
                     description="Desc", acceptance="AC", parent=1)
        t.on("2026-10-26 09:00", state="New", by=b.other("sm"))
        t.on("2026-10-27 09:00", state="Active", assignee=anna,
             iteration="S37", tags=["a", "b"], column="Dev",
             column_done=False)
        fake = FakeAdo(b)
        resp = fake.request("GET", revs_url(3))
        self.assertEqual(resp.status, 200)
        r1, r2 = resp.body["value"]
        f1, f2 = r1["fields"], r2["fields"]
        self.assertNotIn("System.AssignedTo", f1)
        self.assertNotIn("System.Tags", f1)
        self.assertNotIn("System.BoardColumnDone", f1)
        self.assertEqual(f2["System.AssignedTo"], fake.identity("p:anna"))
        self.assertEqual(f1["System.ChangedBy"], fake.identity(b.other("sm")))
        self.assertEqual(f1["System.ChangedDate"], "2026-10-26T08:00:00Z")
        self.assertEqual(f2["System.Tags"], "a; b")
        self.assertEqual(f2["System.IterationPath"], "Demo\\Claims\\S37")
        self.assertEqual(f2["System.BoardColumn"], "Dev")
        self.assertIs(f2["System.BoardColumnDone"], False)
        self.assertEqual(f2["System.Parent"], 1)
        self.assertEqual(f2["System.Description"], "<div>Desc</div>")
        self.assertEqual(f2["Microsoft.VSTS.Common.AcceptanceCriteria"],
                         "<div>AC</div>")
        self.assertEqual(f2["System.TeamProject"], "Demo")
        self.assertEqual((r2["id"], r2["rev"], f2["System.Rev"]), (3, 2, 2))

    def test_text_follows_edits(self):
        b = make()
        t = b.ticket(3, team="Claims", title="Old", description="one")
        t.on("2026-10-26 09:00")
        t.edit("2026-10-27 09:00", description="two", title="New")
        r1, r2 = FakeAdo(b).request("GET", revs_url(3)).body["value"]
        self.assertEqual(r1["fields"]["System.Description"], "<div>one</div>")
        self.assertEqual(r2["fields"]["System.Description"], "<div>two</div>")
        self.assertEqual(r1["fields"]["System.Title"], "Old")
        self.assertEqual(r2["fields"]["System.Title"], "New")

    def test_squad_ticket_has_synthetic_identities(self):
        b = make()
        b.squad_ticket(8, team="Claims").on("2026-10-26 09:00")
        fake = FakeAdo(b)
        f = fake.request("GET", revs_url(8)).body["value"][0]["fields"]
        self.assertIn("displayName", f["System.ChangedBy"])
        self.assertIn("displayName", f["System.AssignedTo"])
        self.assertFalse(f["System.ChangedBy"]["uniqueName"]
                         .endswith("@vendor.example"))

    def test_page_cap(self):
        b = make()
        t = b.ticket(3, team="Claims")
        for d in range(1, 11):
            t.on("2026-10-%02d 09:00" % d)
        fake = FakeAdo(b, page_cap=4)
        page = fake.request("GET", revs_url(3, top=200, skip=0)).body
        self.assertEqual([r["rev"] for r in page["value"]], [1, 2, 3, 4])
        page = fake.request("GET", revs_url(3, top=3, skip=8)).body
        self.assertEqual([r["rev"] for r in page["value"]], [9, 10])

    def test_unknown_and_unreadable(self):
        b = make()
        b.ticket(3, team="Claims").on("2026-10-26 09:00")
        fake = FakeAdo(b)
        self.assertEqual(fake.request("GET", revs_url(4)).status, 404)
        fake.unreadable.add(3)
        self.assertEqual(fake.request("GET", revs_url(3)).status, 403)


class WiqlTests(unittest.TestCase):
    def setUp(self):
        b = make()
        b.team("Policy", area="Demo\\Policy")
        b.team("ClaimsX", area="Demo\\ClaimsX")
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        ben = b.person("ben", "Ben O'Hara", team="Claims", start="2026-01-05")
        # 1: anna then reassigned to ben; ends Closed
        b.ticket(1, team="Claims").on("2026-10-01 09:00", assignee=anna) \
            .on("2026-10-02 09:00", assignee=ben, state="Closed")
        # 2: ben only, active, in a sub-area
        b.ticket(2, team="Claims").on("2026-07-01 09:00", assignee=ben,
                                      state="Active",
                                      area="Demo\\Claims\\Sub")
        # 3: nobody, Bug, ClaimsX (prefix but not under Claims)
        b.ticket(3, team="ClaimsX", type="Bug").on("2026-09-01 09:00")
        # 4: Policy, removed
        b.ticket(4, team="Policy").on("2026-10-05 09:00", state="Removed")
        self.b = b
        self.fake = FakeAdo(b)

    def run_where(self, where, a=1, b=2000000):
        resp = self.fake.request("POST", WIQL_URL, query(where, a, b))
        self.assertEqual(resp.status, 200, resp.body)
        return ids(resp)

    def test_ever_matches_past_assignee(self):
        self.assertEqual(self.run_where(clause_ever_assigned(
            self.fake.alias("p:anna"))), [1])
        self.assertEqual(self.run_where(clause_ever_assigned(
            "ANNA@VENDOR.EXAMPLE")), [1])
        self.assertEqual(self.run_where(clause_ever_assigned("Anna K.")), [1])
        self.assertEqual(self.run_where(clause_ever_assigned(
            "Anna K. <anna@vendor.example>")), [1])
        self.assertEqual(self.run_where(clause_ever_assigned(
            "Ben O'Hara")), [1, 2])

    def test_under_boundaries(self):
        self.assertEqual(self.run_where(clause_area_under("Demo\\Claims")),
                         [1, 2])
        self.assertEqual(self.run_where(
            clause_area_under("Demo\\Claims\\Sub")), [2])
        self.assertEqual(self.run_where(clause_area_under("demo\\claimsx")),
                         [3])

    def test_other_clauses(self):
        self.assertEqual(self.run_where(clause_types(["Bug"])), [3])
        self.assertEqual(self.run_where(
            clause_states_not_in(["Closed", "Removed"])), [2, 3])
        from datetime import date
        self.assertEqual(self.run_where(
            clause_changed_since(date(2026, 9, 1))), [1, 3, 4])
        self.assertEqual(self.run_where(clause_types(["User Story"]),
                                        a=2, b=4), [2])

    def test_where_any_grouping(self):
        w = where_all(clause_states_not_in(["Removed"]), where_any(
            clause_ever_assigned("anna@vendor.example"),
            clause_area_under("Demo\\Policy"), clause_types(["Bug"])))
        self.assertEqual(self.run_where(w), [1, 3])

    def test_unrecognised_clause_gives_400(self):
        resp = self.fake.request("POST", WIQL_URL,
                                 query("[System.Title] CONTAINS 'x'"))
        self.assertEqual(resp.status, 400)
        self.assertIn("[System.Title] CONTAINS", resp.body["message"])

    def test_wiql_cap(self):
        fake = FakeAdo(self.b, wiql_cap=2)
        resp = fake.request("POST", WIQL_URL,
                            query(clause_types(["User Story", "Bug"])))
        self.assertEqual(resp.status, 400)
        self.assertIn("VS402337", resp.body["message"])
        resp = fake.request("POST", WIQL_URL,
                            query(clause_types(["Bug"])))
        self.assertEqual(ids(resp), [3])


class FailureInjectionTests(unittest.TestCase):
    def test_fail_next_and_drop_after(self):
        b = make()
        b.ticket(3, team="Claims").on("2026-10-26 09:00")
        fake = FakeAdo(b)
        fake.fail_next(429, headers={"retry-after": "2"}, times=2)
        for _ in range(2):
            r = fake.request("GET", revs_url(3))
            self.assertEqual((r.status, r.headers), (429, {"retry-after": "2"}))
        self.assertEqual(fake.request("GET", revs_url(3)).status, 200)
        fake.drop_after(1)
        self.assertEqual(fake.request("GET", revs_url(3)).status, 200)
        with self.assertRaises(TransportError):
            fake.request("GET", revs_url(3))
        with self.assertRaises(TransportError):
            fake.request("GET", revs_url(3))
        fake.reset_failures()
        self.assertEqual(fake.request("GET", revs_url(3)).status, 200)
        self.assertEqual(len(fake.requests), 7)
        self.assertEqual(fake.requests[0], ("GET", revs_url(3), None))

    def test_unsupported_method_or_path(self):
        fake = FakeAdo(make())
        self.assertEqual(fake.request("GET", ORG + "/Demo/_apis/nope").status,
                         404)
        self.assertEqual(fake.request("PATCH", revs_url(1)).status, 405)


if __name__ == "__main__":
    unittest.main()
