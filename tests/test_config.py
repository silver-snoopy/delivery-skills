import os
import tempfile
import unittest
from datetime import date

from ledger.config import (Config, Person, email_index, load_config, merge_staff, parse_notes,
                           parse_staff, read_team, save_config, write_team)


class ConfigTest(unittest.TestCase):
    def test_parse_staff_list_formats(self):
        pairs = parse_staff('Maya R. <Maya.R@vendor.example>; "Doe, John" <john.doe@vendor.example>;\nann@vendor.example')
        self.assertEqual(pairs, [("Maya R.", "maya.r@vendor.example"), ("John Doe", "john.doe@vendor.example"),
                                 ("ann", "ann@vendor.example")])

    def test_same_name_two_emails_is_one_person(self):
        m = merge_staff([("Tom H.", "tom.h@vendor.example"), ("Tom H.", "tom.h2@vendor.example")])
        self.assertEqual(m, {"Tom H.": ["tom.h@vendor.example", "tom.h2@vendor.example"]})

    def test_team_csv_round_trip_and_case_insensitive_index(self):
        people = [Person("Tom H.", ["tom.h@vendor.example", "tom.h2@vendor.example"], "ALFA",
                         date(2024, 11, 4), None, 0.5),
                  Person("Ann", ["ann@vendor.example"], "BETA", None, date(2026, 1, 5))]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "team.csv")
            write_team(path, people)
            self.assertEqual(read_team(path), people)
            idx = email_index(read_team(path))
        self.assertEqual(idx["TOM.H2@VENDOR.EXAMPLE".lower()].name, "Tom H.")

    def test_parse_notes(self):
        n = parse_notes("#48211 agreed: QA env down\n#48277 Unknown: API dependency\nALFA summary: Done upload.\n"
                        "ALFA status: amber - QA slot lost\nBETA exec note: Pilot live\nnonsense line")
        self.assertEqual(n.items[48211], ("agreed", "QA env down"))
        self.assertEqual(n.items[48277], ("unknown", "API dependency"))
        self.assertEqual(n.summary["ALFA"], "Done upload.")
        self.assertEqual(n.status["ALFA"], ("AMBER", "QA slot lost"))
        self.assertEqual(n.exec_note["BETA"], "Pilot live")
        self.assertEqual(len(n.warnings), 1)

    def test_parse_notes_not_agreed_and_bad_status(self):
        n = parse_notes("#1 not agreed: late\nALFA status: purple - x")
        self.assertEqual(n.items[1], ("not agreed", "late"))
        self.assertEqual(len(n.warnings), 1)

    def test_config_round_trip_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            save_config(d, Config(organization="https://dev.azure.com/ORG", teams={"ALFA": {"count": "tasks"}},
                                  name_aliases={"Maya Rao": "Maya R."}))
            cfg = load_config(d)
        self.assertEqual(cfg.onboarding_sprints, 2)
        self.assertEqual(cfg.not_delivered_reasons, ["Removed", "Won't Fix", "Won't Do", "Cut",
                                                     "Duplicate", "Obsolete", "Deferred", "Abandoned"])
        self.assertEqual(cfg.teams["ALFA"]["count"], "tasks")
        self.assertEqual(cfg.name_aliases, {"Maya Rao": "Maya R."})


if __name__ == "__main__":
    unittest.main()
