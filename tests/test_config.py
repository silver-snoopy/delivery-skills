import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ledger.config import (DEFAULT_THRESHOLDS, load_config, load_roster,
                           validate)
from ledger.errors import LedgerError
from ledger.home import Home

TZ = ZoneInfo("Europe/Budapest")


def utc(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


def team(name, areas, phases=None):
    return {"name": name, "ado_team": name + " Team", "area_paths": areas,
            "phases": phases or [{"state": "Active", "phase": "doing"}]}


def person(pid, assignments, **extra):
    d = {"id": pid, "display_name": pid.title(), "assignments": assignments}
    d.update(extra)
    return d


class ConfigTestBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Home(Path(tmp.name))

    def write(self, config=None, roster=None):
        base = {"ado": {"org_url": "https://dev.azure.com/example",
                        "project": "P"},
                "timezone": "Europe/Budapest",
                "teams": [team("Claims", ["P\\Claims"]),
                          team("Pricing", ["P\\Pricing"])]}
        base.update(config or {})
        self.home.write_json(base, "config.json")
        self.home.write_json(
            roster if roster is not None else {"people": []}, "roster.json")

    def load(self, config=None, roster=None):
        self.write(config, roster)
        return load_config(self.home), load_roster(self.home)


class ConfigTests(ConfigTestBase):
    def test_defaults_applied(self):
        cfg, _ = self.load()
        self.assertEqual(cfg.thresholds, DEFAULT_THRESHOLDS)
        self.assertEqual(cfg.unit_level, "stories")
        self.assertEqual(cfg.unit_types,
                         ("User Story", "Product Backlog Item", "Bug"))
        self.assertEqual(cfg.agent_text_access, "none")
        self.assertEqual(cfg.vendor_label, "Vendor team")
        self.assertEqual(cfg.org_url, "https://dev.azure.com/example")
        self.assertEqual(DEFAULT_THRESHOLDS["window_sprints"], 6)
        self.assertEqual(DEFAULT_THRESHOLDS["unready_share"], 0.3334)
        self.assertEqual(DEFAULT_THRESHOLDS["worked_min_days"], 0.02)
        self.assertEqual(len(DEFAULT_THRESHOLDS), 27)

    def test_threshold_override_and_unknown_key(self):
        cfg, _ = self.load({"thresholds": {"quiet_days": 9}})
        self.assertEqual(cfg.thresholds["quiet_days"], 9)
        self.assertEqual(cfg.thresholds["window_sprints"], 6)
        self.assertEqual(DEFAULT_THRESHOLDS["quiet_days"], 5)
        with self.assertRaises(LedgerError) as cm:
            self.load({"thresholds": {"bogus": 1}})
        self.assertIn("bogus", cm.exception.message)

    def test_optional_fields_parsed(self):
        cfg, _ = self.load({
            "holidays": ["2026-12-25"],
            "not_delivered": {"states": ["Removed"], "reasons": ["Dropped"]},
            "service_identities": ["svc-1"],
            "labels": {"vendor": "Acme crew"},
            "agent_text_access": "titles",
            "teams": [team("Claims", ["P\\Claims"], [
                {"state": "Resolved", "phase": "handoff", "column": "QA",
                 "column_done": True, "wait_label": "test"}])],
        })
        self.assertEqual(cfg.holidays, frozenset([date(2026, 12, 25)]))
        self.assertEqual(cfg.not_delivered_states, frozenset(["Removed"]))
        self.assertEqual(cfg.not_delivered_reasons, frozenset(["Dropped"]))
        self.assertEqual(cfg.service_identities, ("svc-1",))
        self.assertEqual(cfg.vendor_label, "Acme crew")
        rule = cfg.team("Claims").phases[0]
        self.assertEqual((rule.column, rule.column_done, rule.wait_label),
                         ("QA", True, "test"))

    def test_overlapping_area_paths_rejected(self):
        cfg, r = self.load({"teams": [team("Alpha", ["P\\A"]),
                                      team("Beta", ["P\\A\\B"])]})
        with self.assertRaises(LedgerError) as cm:
            validate(cfg, r)
        self.assertIn("Alpha", cm.exception.message)
        self.assertIn("Beta", cm.exception.message)

    def test_team_for_area_longest_prefix(self):
        cfg, _ = self.load({"teams": [team("Claims", ["P\\Claims"]),
                                      team("Intake", ["P\\Other"])]})
        self.assertEqual(cfg.team_for_area("P\\Claims\\Intake"), "Claims")
        self.assertEqual(cfg.team_for_area("p\\claims"), "Claims")
        self.assertIsNone(cfg.team_for_area("P\\ClaimsX"))

    def test_team_lookup_unknown(self):
        cfg, _ = self.load()
        self.assertEqual(cfg.team("Claims").ado_team, "Claims Team")
        with self.assertRaises(LedgerError):
            cfg.team("Nope")

    def test_bad_timezone_and_unknown_team_rejected(self):
        with self.assertRaises(LedgerError) as cm:
            self.load({"timezone": "Mars/Olympus"})
        self.assertIn("could not be loaded", cm.exception.hint)
        cfg, r = self.load(roster={"people": [person("anna", [
            {"team": "Ghost", "from": "2026-10-01"}])]})
        with self.assertRaises(LedgerError) as cm:
            validate(cfg, r)
        self.assertIn("Ghost", cm.exception.message)

    def test_invalid_phase_or_unit_level_rejected(self):
        cfg, r = self.load({"teams": [team("Claims", ["P\\Claims"], [
            {"state": "Active", "phase": "sideways"}])]})
        with self.assertRaises(LedgerError) as cm:
            validate(cfg, r)
        self.assertIn("sideways", cm.exception.message)
        cfg, r = self.load({"unit_level": "epics-ish"})
        with self.assertRaises(LedgerError):
            validate(cfg, r)

    def rejected(self, config=None, roster=None):
        cfg, r = self.load(config, roster)
        with self.assertRaises(LedgerError) as cm:
            validate(cfg, r)
        self.assertTrue(cm.exception.hint)
        return cm.exception.message

    def test_assignment_ending_before_it_starts_rejected(self):
        msg = self.rejected(roster={"people": [person("anna", [
            {"team": "Claims", "from": "2026-10-10", "to": "2026-10-01"}])]})
        self.assertIn("anna", msg)
        self.assertIn("2026-10-01", msg)
        self.assertIn("2026-10-10", msg)

    def test_absence_ending_before_it_starts_rejected(self):
        msg = self.rejected(roster={"people": [person(
            "anna", [{"team": "Claims", "from": "2026-10-01"}],
            absences=[{"from": "2026-10-20", "to": "2026-10-12"}])]})
        self.assertIn("anna", msg)
        self.assertIn("absence", msg)

    def test_duplicate_team_names_rejected(self):
        msg = self.rejected({"teams": [team("Claims", ["P\\Claims"]),
                                       team("Claims", ["P\\Other"])]})
        self.assertIn("Claims", msg)
        self.assertIn("more than once", msg)

    def test_team_names_with_the_same_folder_rejected(self):
        msg = self.rejected({"teams": [team("Claims Intake", ["P\\A"]),
                                       team("claims-intake", ["P\\B"])]})
        self.assertIn("Claims Intake", msg)
        self.assertIn("claims-intake", msg)
        self.assertIn("same folder", msg)

    def test_duplicate_person_ids_rejected(self):
        msg = self.rejected(roster={"people": [
            person("anna", [{"team": "Claims", "from": "2026-10-01"}]),
            person("anna", [{"team": "Pricing", "from": "2026-10-01"}])]})
        self.assertIn("anna", msg)
        self.assertIn("more than once", msg)

    def test_valid_setup_passes(self):
        cfg, r = self.load(roster={"people": [person("anna", [
            {"team": "Claims", "from": "2026-10-01"}])]})
        validate(cfg, r)

    def test_missing_files_hint_setup_skill(self):
        for name in ("config.json", "roster.json"):
            self.write()
            self.home.path(name).unlink()
            loader = load_config if name == "config.json" else load_roster
            with self.assertRaises(LedgerError) as cm:
                loader(self.home)
            self.assertIn("delivery-setup", cm.exception.hint)


class RosterTests(ConfigTestBase):
    def roster(self):
        _, r = self.load(roster={"people": [
            person("anna", [{"team": "Claims", "from": "2026-10-01",
                             "to": "2026-10-31"}],
                   identity_ids=["id-1"], aliases=["Anna K"],
                   absences=[{"from": "2026-10-05", "to": "2026-10-06",
                              "note": "leave"}]),
            person("ben", [{"team": "Claims", "from": "2026-10-01",
                            "allocation": 0}]),
        ]})
        return r

    def test_allocation_over_one_rejected(self):
        cfg, r = self.load(roster={"people": [person("anna", [
            {"team": "Claims", "from": "2026-10-01", "to": "2026-10-31",
             "allocation": 0.6},
            {"team": "Pricing", "from": "2026-10-15", "allocation": 0.6}])]})
        with self.assertRaises(LedgerError) as cm:
            validate(cfg, r)
        self.assertIn("anna", cm.exception.message)

    def test_three_overlapping_half_allocations_rejected(self):
        cfg, r = self.load(roster={"people": [person("anna", [
            {"team": "Claims", "from": "2026-10-01", "allocation": 0.5},
            {"team": "Pricing", "from": "2026-10-02", "allocation": 0.5},
            {"team": "Claims", "from": "2026-10-03", "allocation": 0.5}])]})
        with self.assertRaises(LedgerError) as cm:
            validate(cfg, r)
        self.assertIn("anna", cm.exception.message)

    def test_agent_text_access_validated(self):
        for ok in ("full", "titles", "none"):
            cfg, r = self.load({"agent_text_access": ok})
            validate(cfg, r)
        cfg, r = self.load({"agent_text_access": "everything"})
        with self.assertRaises(LedgerError) as cm:
            validate(cfg, r)
        self.assertIn("everything", cm.exception.message)

    def test_adjacent_assignments_not_overlapping(self):
        cfg, r = self.load(roster={"people": [person("anna", [
            {"team": "Claims", "from": "2026-10-01", "to": "2026-10-14",
             "allocation": 1.0},
            {"team": "Pricing", "from": "2026-10-15", "allocation": 1.0}])]})
        validate(cfg, r)

    def test_vendor_on_respects_assignment_dates(self):
        r = self.roster()
        self.assertTrue(r.vendor_on("p:anna", "Claims", utc(2026, 10, 15, 12), TZ))
        self.assertTrue(r.vendor_on("p:anna", "Claims", utc(2026, 10, 31, 12), TZ))
        self.assertFalse(r.vendor_on("p:anna", "Claims", utc(2026, 11, 2, 12), TZ))
        self.assertFalse(r.vendor_on("p:anna", "Pricing", utc(2026, 10, 15, 12), TZ))
        self.assertFalse(r.vendor_on("o:abc", "Claims", utc(2026, 10, 15, 12), TZ))
        self.assertFalse(r.vendor_on(None, "Claims", utc(2026, 10, 15, 12), TZ))

    def test_vendor_on_uses_local_date_and_zero_allocation(self):
        r = self.roster()
        # 23:30 UTC on Oct 31 is Nov 1 in Budapest: past the inclusive end.
        self.assertFalse(r.vendor_on("p:anna", "Claims", utc(2026, 10, 31, 23), TZ))
        self.assertFalse(r.vendor_on("p:ben", "Claims", utc(2026, 10, 15, 12), TZ))
        self.assertFalse(r.vendor_on("p:nobody", "Claims", utc(2026, 10, 15, 12), TZ))

    def test_match_identity_id_then_alias(self):
        r = self.roster()
        self.assertEqual(r.match_identity("id-1", ["Someone"]), "p:anna")
        self.assertEqual(r.match_identity(None, ["x", "anna k"]), "p:anna")
        self.assertEqual(r.match_identity("zzz", ["ANNA K"]), "p:anna")
        self.assertIsNone(r.match_identity("zzz", ["Nobody"]))
        self.assertIsNone(r.match_identity(None, []))

    def test_person_assignment_and_people_on(self):
        r = self.roster()
        self.assertEqual(r.person("anna").display_name, "Anna")
        with self.assertRaises(LedgerError):
            r.person("zed")
        self.assertEqual(
            r.assignment_on("anna", "Claims", date(2026, 10, 31)).end,
            date(2026, 10, 31))
        self.assertIsNone(r.assignment_on("anna", "Claims", date(2026, 11, 1)))
        ids = [p.id for p in r.people_on("Claims", date(2026, 10, 28),
                                         date(2026, 11, 5))]
        self.assertEqual(ids, ["anna"])
        ids = [p.id for p in r.people_on("Claims", date(2026, 11, 1),
                                         date(2026, 11, 5))]
        self.assertEqual(ids, [])
        self.assertEqual(r.person("anna").absences[0].note, "leave")


if __name__ == "__main__":
    unittest.main()
