"""Backlog roles: who the product owner or business analyst is, set up
once, matched to board identities only through keyed hashes, and named
in the scope line instead of "another party"."""
import tempfile
import unittest
from pathlib import Path

from ledger import analyze
from ledger.ado.pseudonym import (INDEX_FILE, Pseudonymizer, alias_hash,
                                  load_index, load_or_create_key, role_map)
from ledger.config import Roster, load_config, load_roster, validate
from ledger.errors import LedgerError
from ledger.home import Home
from ledger.render.fmt import scope_text
from ledger.report import footnote
from ledger.testing.builder import PortfolioBuilder
from tests.test_config import ConfigTestBase, person

SPRINTS = [("S36", "2026-10-12", "2026-10-23"),
           ("S37", "2026-10-26", "2026-11-06")]


class ConfigTests(ConfigTestBase):
    def roles(self, roles, labels=None):
        cfg = {"backlog_roles": roles}
        if labels:
            cfg["labels"] = labels
        self.write(config=cfg)
        return load_config(self.home)

    def test_parsed_with_defaults(self):
        cfg = self.roles([{"id": "po", "label": "Product owner",
                           "aliases": ["po@customer.example"]}])
        r = cfg.backlog_roles[0]
        self.assertEqual((r.id, r.label, r.aliases, r.teams),
                         ("po", "Product owner", ("po@customer.example",),
                          ()))
        self.assertTrue(r.applies_to("Anything"))
        self.assertEqual(cfg.scope_owner, "the customer")

    def test_scope_owner_label(self):
        cfg = self.roles([], labels={"scope_owner": "the client's PO"})
        self.assertEqual(cfg.scope_owner, "the client's PO")
        self.assertIn("Sprint scope is set by the client's PO",
                      footnote(cfg))

    def test_bad_shapes_refused(self):
        for bad in ({"id": "PO", "label": "x", "aliases": ["a"]},
                    {"id": "po", "label": "", "aliases": ["a"]},
                    {"id": "po", "label": "x", "aliases": []},
                    {"id": "po", "label": "x", "aliases": ["a"],
                     "teams": "Claims"}):
            with self.assertRaises(LedgerError, msg=bad):
                self.roles([bad])
        with self.assertRaises(LedgerError):
            self.roles([{"id": "po", "label": "x", "aliases": ["a"]},
                        {"id": "po", "label": "y", "aliases": ["b"]}])

    def test_unknown_team_and_roster_engineer_refused(self):
        self.write(config={"backlog_roles": [
            {"id": "po", "label": "PO", "aliases": ["a"],
             "teams": ["Nope"]}]})
        with self.assertRaises(LedgerError) as c:
            validate(load_config(self.home), load_roster(self.home))
        self.assertIn("unknown team", str(c.exception))
        self.write(
            config={"backlog_roles": [{"id": "po", "label": "PO",
                                       "aliases": ["ANNA@vendor.example"]}]},
            roster={"people": [person(
                "anna", [{"team": "Claims", "from": "2026-01-01"}],
                aliases=["anna@vendor.example"])]})
        with self.assertRaises(LedgerError) as c:
            validate(load_config(self.home), load_roster(self.home))
        self.assertIn("roster engineer", str(c.exception))


class IndexTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Home(Path(tmp.name))
        self.key = load_or_create_key(self.home)

    def test_index_holds_only_hashes_and_is_written_on_each_new_identity(self):
        writes = []
        p = Pseudonymizer(self.key, Roster([]), [],
                          on_index=lambda ix: writes.append(
                              {k: list(v) for k, v in ix.items()}))
        ident = {"id": "abc", "uniqueName": "PO@Customer.example",
                 "displayName": "Pat Owner"}
        a = p.actor(ident)
        p.actor(ident)
        self.assertEqual(len(writes), 1, "a known identity writes nothing")
        hashes = writes[0][a]
        self.assertIn(alias_hash(self.key, "po@customer.example"), hashes)
        self.assertIn(alias_hash(self.key, "pat owner"), hashes)
        text = repr(writes)
        self.assertNotIn("Pat", text)
        self.assertNotIn("customer", text.lower())

    def test_role_map_matches_by_alias_and_team(self):
        b = PortfolioBuilder()
        b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
        b.team("Data", area="Demo\\Data", sprints=SPRINTS)
        b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        b.backlog_role("po", "Product owner", ["owner one"], teams=["Claims"])
        b.backlog_role("ba", "Business analyst", ["analyst"])
        b.write_identity_index(self.home)
        self.assertTrue(self.home.path(INDEX_FILE).exists())
        cfg = b.config()
        self.assertEqual(role_map(self.home, cfg.backlog_roles, "Claims"), {
            b.other("owner one"): "po", b.other("analyst"): "ba"})
        self.assertEqual(role_map(self.home, cfg.backlog_roles, "Data"),
                         {b.other("analyst"): "ba"})
        self.assertEqual(role_map(self.home, (), "Claims"), {})

    def test_load_index_tolerates_a_bad_file(self):
        self.home.write_json(["not", "a", "map"], INDEX_FILE)
        self.assertEqual(load_index(self.home), {})


class AnalyzeTests(unittest.TestCase):
    def build(self, with_index=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Home(Path(tmp.name))
        b = PortfolioBuilder()
        b.team("Claims", area="Demo\\Claims", sprints=SPRINTS)
        anna = b.person("anna", "Anna K.", team="Claims", start="2026-01-05")
        po, ba = b.other("pat"), b.other("bea")
        b.backlog_role("po", "Product owner", ["pat"])
        b.backlog_role("ba", "BA", ["bea"])
        b.ticket(1, team="Claims").on(
            "2026-10-22 10:00", iteration="S37", by=po).on(
            "2026-10-27 09:00", state="Active", assignee=anna)
        b.ticket(2, team="Claims").on(
            "2026-10-22 10:00", iteration="S37", by=ba).on(
            "2026-10-27 09:00", state="Active", assignee=anna)
        b.ticket(3, team="Claims").on(
            "2026-10-22 10:00", iteration="S37", by=b.other("sm")).on(
            "2026-10-27 09:00", state="Active", assignee=anna)
        b.write(home)
        last = b.sprints("Claims")[-1]
        b.snapshot(cutoff="2026-11-07 08:00", team="Claims").save(
            home.team_sprint_dir("Claims", last.id) / "snapshot")
        if not with_index:
            home.path(INDEX_FILE).unlink()
        cfg, roster = load_config(home), load_roster(home)
        out = analyze.run_analyze(home, cfg, roster, "Claims")
        sid = b.sprints("Claims")[-1].id
        d = home.team_sprint_dir("Claims", sid).relative_to(home.root).parts
        return out, home.read_json(*d, "metrics.json")

    def test_roles_named_in_figures_and_text(self):
        out, m = self.build()
        s = m["figures"]["scope"]
        self.assertEqual(s["at_start"]["parties"],
                         {"role_po": 1, "role_ba": 1, "other_party": 1,
                          "vendor": 0, "automation": 0, "not_recorded": 0})
        self.assertEqual(s["labels"], {"role_po": "Product owner",
                                       "role_ba": "BA"})
        self.assertEqual(m["tickets"]["1"]["placed_by"], "role_po")
        self.assertEqual(
            scope_text(m["figures"]),
            "Sprint scope: 3 with vendor engineers at the start (placed in "
            "the sprint: 1 by the product owner, 1 by the BA, 1 by another "
            "party)")
        self.assertEqual(out["warnings"], [])

    def test_unmatched_role_warns(self):
        out, m = self.build(with_index=False)
        self.assertEqual(m["figures"]["scope"]["at_start"]["parties"]
                         ["other_party"], 3)
        self.assertTrue(any("no board identity matched backlog role(s) "
                            "ba, po" in w for w in out["warnings"]),
                        out["warnings"])


if __name__ == "__main__":
    unittest.main()
