import io
import os
import tempfile
import unittest
from argparse import Namespace
from copy import deepcopy
from datetime import date
from unittest import mock

from ledger import setup
from ledger.ado import AdoError
from ledger.config import Person, load_config, read_team
from tests.fixture import Board
from tests.test_xlsx import build_workbook, calendar_rows

MAYA, JOHN, ANA = "maya.r@vendor.example", "john.doe@vendor.example", "ana.k@vendor.example"
STAFF = {"Maya R.": [MAYA], "John Doe": [JOHN]}


def board(tasks=0, stories=1):
    b = Board().sprint("S1", "2026-10-05", "2026-10-16")
    n = 100
    for i in range(stories):
        b.item(n + i, "User Story", area="Proj\\Claims\\Intake").at(
            "2026-10-06 09:00", state="Active", assigned=MAYA
        ).at("2026-10-08 09:00", state="Closed")
    for i in range(tasks):
        b.item(500 + i, "Task", parent=100, area="Proj\\Claims\\Intake").at(
            "2026-10-06 09:00", state="Active", assigned=MAYA
        ).at("2026-10-07 09:00", state="Closed")
    b.item(900, "User Story", area="Proj\\Claims\\Intake").at(
        "2026-10-05 09:00", state="Active", assigned=JOHN
    )
    return b


class SuggestTest(unittest.TestCase):
    def test_squad_column_is_first_suggestion(self):
        s = setup.suggest(board().cache(), STAFF, [], {"Maya R.": "Alfa"})
        self.assertEqual({p["name"]: p["team"] for p in s["people"]}, {"Maya R.": "ALFA", "John Doe": "ALFA"})

    def test_suggestions(self):
        s = setup.suggest(board().cache(), STAFF, [])
        maya = s["people"][0]
        self.assertEqual(
            (maya["team"], maya["area"], maya["joined"]),
            ("INTAKE", "Proj\\Claims\\Intake", date(2026, 10, 6)),
        )
        known = [Person("John Doe", [JOHN], "ALFA", date(2026, 1, 5), None)]
        s = setup.suggest(board().cache(), STAFF, known)
        self.assertEqual([p["team"] for p in s["people"]], ["ALFA", "ALFA"])
        self.assertEqual(s["people"][1]["joined"], date(2026, 1, 5))

    def test_join_date_near_the_window_start_is_left_blank(self):
        s = setup.suggest(board().cache(), STAFF, [], since=date(2026, 9, 10))
        self.assertIsNone(s["people"][0]["joined"])  # first seen 2026-10-06, within 30 days
        s = setup.suggest(board().cache(), STAFF, [], since=date(2026, 9, 5))
        self.assertEqual(s["people"][0]["joined"], date(2026, 10, 6))

    def test_person_without_tickets_is_kept(self):
        s = setup.suggest(board().cache(), {"Ana K.": [ANA]}, [])
        self.assertEqual(
            s["people"][0],
            {"name": "Ana K.", "emails": [ANA], "team": "", "joined": None, "items": 0, "area": ""},
        )

    def test_count_suggestion(self):
        for tasks, stories, want in ((30, 4, "tasks"), (10, 8, "stories")):
            b = board(tasks, 0)
            for i in range(stories):
                b.item(200 + i, "User Story", area="Proj\\Claims\\Intake").at(
                    "2026-10-06 09:00", state="Active", assigned=MAYA
                ).at("2026-10-08 09:00", state="Closed")
            s = setup.suggest(b.cache(), STAFF, [])
            self.assertEqual(s["teams"]["INTAKE"], {"count": want, "stories": stories, "tasks": tasks})

    def test_waiting_state_suggestion(self):
        b = board()
        b.item(901).at("2026-10-06 09:00", state="Ready for QA", assigned=MAYA)
        b.item(902).at("2026-10-06 09:00", state="In Review", assigned=MAYA)
        s = setup.suggest(b.cache(), STAFF, [])
        self.assertEqual(s["waiting"], ["In Review", "Ready for QA"])
        self.assertIn({"name": "Active", "category": "InProgress"}, s["states"])

    def test_hold_state_suggestion(self):
        b = board()
        b.item(903).at("2026-10-06 09:00", state="On Hold", assigned=MAYA)
        b.item(904).at("2026-10-06 09:00", state="Impeded", assigned=MAYA)
        b.item(905).at("2026-10-06 09:00", state="Ready for QA", assigned=MAYA)
        s = setup.suggest(b.cache(), STAFF, [])
        self.assertEqual(s["hold"], ["Impeded", "On Hold"])

    def test_suggest_test_states(self):
        b = board()
        states = (
            ("Ready for QA", "InProgress"),
            ("In Review", "InProgress"),
            ("On Hold", "InProgress"),
            ("Verify", "Resolved"),
            ("UAT", "Resolved"),
            ("Acceptance", "InProgress"),
            ("Testing", "InProgress"),
            ("Resolved", "Resolved"),
            ("Test backlog", "Proposed"),
            ("Test done", "Completed"),
            ("Test removed", "Removed"),
        )
        for i, (state, category) in enumerate(states, 1):
            b.item(i).at("2026-10-06 09:00", state=state, category=category, assigned=MAYA)
        s = setup.suggest(b.cache(), STAFF, [])
        self.assertEqual(
            s["test"], ["Acceptance", "In Review", "Ready for QA", "Resolved", "Testing", "UAT", "Verify"]
        )
        self.assertNotIn("Active", s["test"])
        self.assertNotIn("On Hold", s["test"])


class InitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = os.path.join(self.tmp.name, "work")
        self.staff = os.path.join(self.tmp.name, "staff.txt")
        self.vac = os.path.join(self.tmp.name, "vac.xlsx")
        self.write_staff(f"Maya R. <{MAYA}>; John Doe <{JOHN}>")
        rows = calendar_rows()
        rows[6] = {4: "Nobody X"}
        build_workbook(self.vac, rows, ["G2:AJ2", "AK2:BO2"])

    def write_staff(self, text):
        with open(self.staff, "w") as f:
            f.write(text)

    def args(self):
        return Namespace(
            dir=self.d,
            query="Proj\\Claims",
            staff=self.staff,
            vacations=self.vac,
            yes=True,
            org="https://dev.azure.com/org",
            project="P",
            timezone="Europe/Zurich",
        )

    def run_init(self, data=None, ask=None):
        out = io.StringIO()
        with mock.patch("ledger.setup.fetch", return_value=deepcopy(data or board().cache())):
            code = setup.run_init(self.args(), object(), ask or (lambda p, d: d), out)
        return code, out.getvalue()

    def test_init_yes_writes_files_and_keeps_answers(self):
        code, text = self.run_init()
        self.assertEqual(code, 0)
        cfg = load_config(self.d)
        self.assertEqual(
            (cfg.organization, cfg.project, cfg.timezone, cfg.teams),
            ("https://dev.azure.com/org", "P", "Europe/Zurich", {"ALFA": {"count": "stories"}}),
        )
        self.assertTrue(os.path.isabs(cfg.staff) and os.path.isabs(cfg.vacations))
        self.assertIn("Nobody X", text)
        self.assertIn("Active: InProgress", text)
        self.assertTrue(os.path.exists(os.path.join(self.d, "cache", "revisions.json")))
        # second run: edit an answer, add a person, drop another
        team_path = os.path.join(self.d, "team.csv")
        people = read_team(team_path)
        people[0].team = "BETA"
        from ledger.config import save_config, write_team

        write_team(team_path, people)
        cfg.name_aliases["Nobody X"] = "John Doe"
        cfg.waiting_states = ["Custom"]
        save_config(self.d, cfg)
        self.write_staff(f"Maya R. <{MAYA}>; Ana K. <{ANA}>")
        asked = []
        code, text = self.run_init(ask=lambda p, d: asked.append(p) or d)
        self.assertFalse([p for p in asked if "Maya" in p or "John" in p])
        self.assertTrue(any("Ana K." in p for p in asked))
        by = {p.name: p for p in read_team(team_path)}
        self.assertEqual(by["Maya R."].team, "BETA")
        self.assertEqual(by["John Doe"].left, date.today())
        self.assertIsNone(by["Ana K."].joined)
        cfg = load_config(self.d)
        self.assertEqual(cfg.waiting_states, ["Custom"])
        self.assertEqual(cfg.name_aliases, {"Nobody X": "John Doe"})
        self.assertIn("left", text)

    def test_hold_state_proposed_and_saved(self):
        b = board()
        b.item(903).at("2026-10-06 09:00", state="On Hold", assigned=MAYA)
        _, text = self.run_init(b.cache())
        self.assertIn("Proposed hold states: On Hold", text)
        self.assertEqual(load_config(self.d).hold_states, ["On Hold"])
        self.assertLess(text.index("Proposed waiting states"), text.index("Proposed hold states"))
        self.assertLess(text.index("Proposed hold states"), text.index("Proposed test states"))

    def test_test_states_proposed_and_saved(self):
        b = board()
        b.item(1).at("2026-10-06 09:00", state="Ready for QA", assigned=MAYA)
        b.item(2).at("2026-10-06 09:00", state="In Review", assigned=MAYA)
        _, text = self.run_init(b.cache())
        self.assertIn("Proposed test states: In Review, Ready for QA", text)
        self.assertEqual(load_config(self.d).test_states, ["In Review", "Ready for QA"])

    def test_configured_test_states_are_kept(self):
        from ledger.config import Config, save_config

        cfg = Config(timezone="Europe/Zurich")
        cfg.test_states = ["Custom QA"]
        save_config(self.d, cfg)
        b = board()
        b.item(1).at("2026-10-06 09:00", state="Ready for QA", assigned=MAYA)
        self.run_init(b.cache())
        self.assertEqual(load_config(self.d).test_states, ["Custom QA"])

    def test_no_test_states_prints_none(self):
        _, text = self.run_init()
        self.assertIn("Proposed test states: none", text)

    def test_squad_teams_maps_calendar_squad_to_team(self):
        from ledger.config import Config, save_config

        save_config(self.d, Config(timezone="Europe/Zurich", squad_teams={"Alfa": "CLAIMS"}))
        self.run_init()
        teams = {p.name: p.team for p in read_team(os.path.join(self.d, "team.csv"))}
        self.assertEqual(teams["Maya R."], "CLAIMS")

    def test_unseen_email_typed_team_and_timezone(self):
        self.write_staff(f"Maya R. <{MAYA}>; John Doe <{JOHN}>; Ana K. <{ANA}>; nobody")
        a = self.args()
        a.timezone = None
        with mock.patch("ledger.setup.machine_timezone", return_value=None):
            out = io.StringIO()
            with mock.patch("ledger.setup.fetch", return_value=board().cache()):
                setup.run_init(a, object(), lambda p, d: " gamma " if "Team? (Ana" in p else d, out)
        text = out.getvalue()
        self.assertIn(f"warning: {ANA} is never assigned on the board; check the sign-in email", text)
        self.assertNotIn(f"warning: {MAYA} is never", text)
        self.assertIn("'nobody' has no email address", text)
        self.assertIn("could not detect the time zone", text)
        self.assertEqual(load_config(self.d).timezone, "UTC")
        self.assertEqual(
            {p.name: p.team for p in read_team(os.path.join(self.d, "team.csv"))}["Ana K."], "GAMMA"
        )

    def test_bad_timezone_stops_init(self):
        a = self.args()
        a.timezone = "Mars/Base"
        with self.assertRaises(ValueError):
            setup.run_init(a, object(), lambda p, d: d, io.StringIO())

    def test_summary_lists_closure_reasons(self):
        b = board()
        b.item(950).at("2026-10-06 09:00", state="Active", assigned=MAYA).at(
            "2026-10-07 09:00", state="Closed", reason="Obsolete"
        )
        _, text = self.run_init(b.cache())
        self.assertIn("Not-delivered reasons (config): Removed, Won't Fix", text)
        self.assertIn(
            "Closure reasons on the board (* = not delivered): (no reason) (1), *Obsolete (1)", text
        )

    def test_unmatched_calendar_name_answer_is_remembered(self):
        asked = []
        self.run_init(ask=lambda p, d: asked.append(p) or ("John Doe" if "Nobody X" in p else d))
        self.assertIn("Which person in staff.txt is 'Nobody X'? (blank if none)", asked)
        self.assertEqual(load_config(self.d).name_aliases, {"Nobody X": "John Doe"})

    def test_init_stops_when_fetch_fails(self):  # fetch checks the rows (test_ado)
        with (
            mock.patch("ledger.setup.fetch", side_effect=AdoError("unknown state category 'Weird'")),
            self.assertRaises(AdoError),
        ):
            setup.run_init(self.args(), object(), lambda p, d: d, io.StringIO())
        self.assertFalse(os.path.exists(self.d))

    def test_missing_org_and_project(self):
        a = self.args()
        a.org = a.project = None
        with (
            mock.patch("ledger.setup.az_defaults", return_value=(None, None)),
            self.assertRaises(AdoError) as cm,
        ):
            setup.run_init(a, None, lambda p, d: d, io.StringIO())
        self.assertIn("az devops configure", cm.exception.next_step)

    def test_init_reuses_lowercase_team_key_and_preserves_custom_metrics(self):
        from ledger.config import Config, save_config

        cfg = Config(
            timezone="Europe/Zurich",
            teams={"alfa": {"metrics": ["planned", "delivered", "po_satisfaction"], "show_flow": False}},
        )
        save_config(self.d, cfg)
        code, text = self.run_init()
        self.assertEqual(code, 0)
        self.assertIn("ALFA counts stories", text)
        reloaded = load_config(self.d)
        self.assertEqual(list(reloaded.teams.keys()), ["alfa"])
        self.assertEqual(
            reloaded.teams["alfa"],
            {
                "count": "stories",
                "metrics": ["planned", "delivered", "po_satisfaction"],
                "show_flow": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
