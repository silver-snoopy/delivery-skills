import json
import os
import tempfile
import unittest
from datetime import date

from ledger.config import (
    DEFAULT_MANAGER_METRICS,
    KNOWN_METRICS,
    Config,
    Person,
    email_index,
    load_config,
    merge_staff,
    parse_notes,
    parse_staff,
    read_team,
    save_config,
    write_team,
)
from ledger.metrics import metrics_of, show_flow_of
from ledger.replay import local_tz


class ConfigTest(unittest.TestCase):
    def test_parse_staff_list_formats(self):
        pairs = parse_staff(
            'Maya R. <Maya.R@vendor.example>; "Doe, John" <john.doe@vendor.example>;\nann@vendor.example'
        )
        self.assertEqual(
            pairs,
            [
                ("Maya R.", "maya.r@vendor.example"),
                ("John Doe", "john.doe@vendor.example"),
                ("ann", "ann@vendor.example"),
            ],
        )

    def test_same_name_two_emails_is_one_person(self):
        m = merge_staff([("Tom H.", "tom.h@vendor.example"), ("Tom H.", "tom.h2@vendor.example")])
        self.assertEqual(m, {"Tom H.": ["tom.h@vendor.example", "tom.h2@vendor.example"]})

    def test_team_csv_round_trip_and_case_insensitive_index(self):
        people = [
            Person(
                "Tom H.",
                ["tom.h@vendor.example", "tom.h2@vendor.example"],
                "ALFA",
                date(2024, 11, 4),
                None,
                0.5,
            ),
            Person("Ann", ["ann@vendor.example"], "BETA", None, date(2026, 1, 5)),
        ]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "team.csv")
            write_team(path, people)
            self.assertEqual(read_team(path), people)
            idx = email_index(read_team(path))
        self.assertEqual(idx["TOM.H2@VENDOR.EXAMPLE".lower()].name, "Tom H.")

    def test_markdown_notes_and_legacy_rejection(self):
        n = parse_notes(
            "\ufeff# ALFA\n## Summary\n- Delivered upload\nIn progress: retries\n"
            "## Exec note\nPilot live\n## Note\nfirst\nsecond\n"
            "## Status\namber - QA slot lost\n"
        )
        self.assertEqual(n.summary["ALFA"], "Delivered upload\nIn progress: retries")
        self.assertEqual(n.exec_note["ALFA"], "Pilot live")
        self.assertEqual(n.note["ALFA"], "first\nsecond")
        self.assertEqual(n.status["ALFA"], ("AMBER", "QA slot lost"))
        old = parse_notes("#1 agreed: late\nALFA summary: old")
        self.assertFalse(old.items)
        self.assertFalse(old.summary)
        self.assertTrue(old.warnings)

    def test_markdown_blank_and_invalid_status(self):
        n = parse_notes("# ALFA\n## Status\n\n# BETA\n## Status\npurple - x\n")
        self.assertFalse(n.status)
        self.assertEqual(len(n.warnings), 1)

    def test_config_round_trip_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            save_config(
                d,
                Config(
                    organization="https://dev.azure.com/ORG",
                    teams={"ALFA": {"count": "tasks"}},
                    name_aliases={"Maya Rao": "Maya R."},
                ),
            )
            cfg = load_config(d)
        self.assertEqual(cfg.onboarding_sprints, 2)
        self.assertEqual(
            cfg.not_delivered_reasons,
            ["Removed", "Won't Fix", "Won't Do", "Cut", "Duplicate", "Obsolete", "Deferred", "Abandoned"],
        )
        self.assertEqual(cfg.teams["ALFA"]["count"], "tasks")
        self.assertEqual(cfg.name_aliases, {"Maya Rao": "Maya R."})

        self.assertEqual(cfg.history_days, 200)
        self.assertEqual(cfg.theme, "")
        self.assertEqual(cfg.theme_reports, ["manager"])

    def test_parse_staff_skips_entries_without_email(self):
        warnings = []
        pairs = parse_staff("John Doe; Maya R. <maya@vendor.example>; Tom <tom>", warnings)
        self.assertEqual(pairs, [("Maya R.", "maya@vendor.example")])
        self.assertEqual(len(warnings), 2)
        self.assertIn("John Doe", warnings[0])

    def test_team_csv_bom_and_team_case(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team.csv")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("name,email,team,joined,left,allocation\nAnn,Ann@vendor.example, alfa ,,,1\n")
            self.assertEqual(read_team(path), [Person("Ann", ["ann@vendor.example"], "ALFA", None, None)])

    def test_blank_email_cell_yields_no_email(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team.csv")
            with open(path, "w") as f:
                f.write("name,email,team,joined,left,allocation\nAnn,,ALFA,,,1\n")
            self.assertEqual(read_team(path), [Person("Ann", [], "ALFA", None, None)])

    def test_write_team_round_trip_for_person_with_no_email(self):
        people = [Person("Ann", [], "ALFA", None, None)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "team.csv")
            write_team(path, people)
            with open(path) as f:
                self.assertEqual(f.read().splitlines()[1], "Ann,,ALFA,,,1")
            self.assertEqual(read_team(path), people)

    def test_parse_staff_drops_trailing_tag_in_parentheses(self):
        pairs = parse_staff(
            'Maya Rao (external) <maya@vendor.example>; "Doe, John (ext)" <jd@vendor.example>'
        )
        self.assertEqual(pairs, [("Maya Rao", "maya@vendor.example"), ("John Doe", "jd@vendor.example")])

    def test_count_accepts_a_type_list(self):
        cfg, _ = self.load({"teams": {"ALFA": {"count": ["User Story", "Bug"]}}})
        self.assertEqual(cfg.teams["ALFA"]["count"], ["User Story", "Bug"])
        for bad in ([], [""], ["Bug", 3]):
            with self.assertRaises(ValueError) as e:
                self.load({"teams": {"ALFA": {"count": bad}}})
            self.assertIn("list of work item types", str(e.exception))

    def load(self, raw):
        warnings = []
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "config.json"), "w") as f:
                json.dump(raw, f)
            return load_config(d, warnings.append), warnings

    def test_unknown_config_keys_are_a_warning(self):
        cfg, warnings = self.load({"timezone": "UTC", "waiting_state": ["QA"], "zzz": 1})
        self.assertEqual(cfg.waiting_states, [])
        self.assertEqual(warnings, ["config.json keys not recognised and ignored: waiting_state, zzz"])

    def test_bad_count_and_timezone_are_errors(self):
        with self.assertRaises(ValueError) as e:
            self.load({"teams": {"ALFA": {"count": "story"}}})
        self.assertIn("ALFA", str(e.exception))
        with self.assertRaises(ValueError) as e:
            self.load({"timezone": "Europe/Zürich"})
        self.assertIn("Europe/Zürich", str(e.exception))
        with self.assertRaises(ValueError):
            local_tz("Nowhere/City")

    def test_manager_metrics_and_show_flow_defaults_and_resolution(self):
        cfg, warnings = self.load(
            {
                "teams": {
                    "alfa": {"count": "stories"},
                    "BETA": {"count": "tasks", "metrics": [], "show_flow": False},
                    "GAMMA": {"metrics": ["po_satisfaction", "planned"], "show_flow": True},
                }
            }
        )
        self.assertEqual(warnings, [])
        self.assertEqual(cfg.manager_metrics, list(DEFAULT_MANAGER_METRICS))
        self.assertEqual(KNOWN_METRICS, (*DEFAULT_MANAGER_METRICS, "po_satisfaction"))
        self.assertTrue(cfg.show_flow)
        # Case-insensitive lookup + inheritance
        self.assertEqual(metrics_of(cfg, "ALFA"), list(DEFAULT_MANAGER_METRICS))
        self.assertTrue(show_flow_of(cfg, "ALFA"))
        # Unconfigured team inherits global
        self.assertEqual(metrics_of(cfg, "DELTA"), list(DEFAULT_MANAGER_METRICS))
        self.assertTrue(show_flow_of(cfg, "DELTA"))
        # Explicit [] and False override global
        self.assertEqual(metrics_of(cfg, "BETA"), [])
        self.assertFalse(show_flow_of(cfg, "BETA"))
        # Custom ordering + explicit True
        self.assertEqual(metrics_of(cfg, "GAMMA"), ["po_satisfaction", "planned"])
        self.assertTrue(show_flow_of(cfg, "GAMMA"))
        # Global overrides inherited by teams without explicit overrides
        cfg2, _ = self.load(
            {
                "manager_metrics": ["delivered", "build_time"],
                "show_flow": False,
                "teams": {"ALFA": {"count": "stories"}, "BETA": {"show_flow": True}},
            }
        )
        self.assertEqual(metrics_of(cfg2, "ALFA"), ["delivered", "build_time"])
        self.assertFalse(show_flow_of(cfg2, "ALFA"))
        self.assertTrue(show_flow_of(cfg2, "BETA"))

    def test_invalid_metrics_and_show_flow_raise_value_error(self):
        bad_metrics = [
            None,
            "planned",
            123,
            {"planned": True},
            [123],
            [{"x": 1}],
            [None],
            ["planned", "bogus"],
            ["planned", "delivered", "planned"],
        ]
        for bad in bad_metrics:
            with self.assertRaises(ValueError):
                self.load({"manager_metrics": bad})
            with self.assertRaises(ValueError) as cm:
                self.load({"teams": {"ALFA": {"metrics": bad}}})
            self.assertIn("ALFA", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self.load({"teams": {"ALFA": {"metrics": ["planned", "bogus"]}}})
        self.assertIn("bogus", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self.load({"manager_metrics": ["planned", "delivered", "planned"]})
        self.assertIn("planned", str(cm.exception))
        for bad in (None, 0, 1, "true", "false", []):
            with self.assertRaises(ValueError):
                self.load({"show_flow": bad})
            with self.assertRaises(ValueError) as cm:
                self.load({"teams": {"ALFA": {"show_flow": bad}}})
            self.assertIn("ALFA", str(cm.exception))

    def test_invalid_teams_case_collision_and_unknown_team_keys(self):
        for bad in (None, [], "ALFA"):
            with self.assertRaises(ValueError):
                self.load({"teams": bad})
        for bad in (None, [], "stories", 1):
            with self.assertRaises(ValueError) as cm:
                self.load({"teams": {"ALFA": bad}})
            self.assertIn("ALFA", str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            self.load({"teams": {"alfa": {"count": "stories"}, "ALFA": {"count": "tasks"}}})
        self.assertIn("ALFA", str(cm.exception))
        self.assertIn("alfa", str(cm.exception))
        cfg, warnings = self.load({"teams": {"ALFA": {"count": "stories", "zzz": 1, "aaa": 2}}})
        self.assertEqual(cfg.teams["ALFA"]["count"], "stories")
        self.assertEqual(len(warnings), 1)
        self.assertIn("ALFA", warnings[0])
        self.assertIn("aaa, zzz", warnings[0])

    def test_theme_and_theme_reports_validation(self):
        cfg, warnings = self.load({})
        self.assertEqual(warnings, [])
        self.assertEqual(cfg.theme, "")
        self.assertEqual(cfg.theme_reports, ["manager"])

        for reports in ([], ["dm"], ["manager", "dm", "person"]):
            cfg, warnings = self.load({"theme": "brand/style.css", "theme_reports": reports})
            self.assertEqual(warnings, [])
            self.assertEqual(cfg.theme, "brand/style.css")
            self.assertEqual(cfg.theme_reports, reports)

        for bad_theme in (None, True, 123, ["a.css"], {"path": "a.css"}):
            with self.assertRaises(ValueError):
                self.load({"theme": bad_theme})

        for bad_reports in (
            None,
            "manager",
            123,
            {"manager": True},
            [123],
            [None],
            ["manager", "bogus"],
            ["manager", "dm", "manager"],
        ):
            with self.assertRaises(ValueError):
                self.load({"theme_reports": bad_reports})


if __name__ == "__main__":
    unittest.main()
