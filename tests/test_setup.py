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
        b.item(n + i, "User Story", area="Proj\\Claims\\Intake").at("2026-10-06 09:00", state="Active", assigned=MAYA) \
            .at("2026-10-08 09:00", state="Closed")
    for i in range(tasks):
        b.item(500 + i, "Task", parent=100, area="Proj\\Claims\\Intake").at("2026-10-06 09:00", state="Active", assigned=MAYA) \
            .at("2026-10-07 09:00", state="Closed")
    b.item(900, "User Story", area="Proj\\Claims\\Intake").at("2026-10-05 09:00", state="Active", assigned=JOHN)
    return b


class SuggestTest(unittest.TestCase):
    def test_squad_column_is_first_suggestion(self):
        s = setup.suggest(board().cache(), STAFF, [], {"Maya R.": "Alfa"})
        self.assertEqual({p["name"]: p["team"] for p in s["people"]}, {"Maya R.": "ALFA", "John Doe": "ALFA"})

    def test_suggestions(self):
        s = setup.suggest(board().cache(), STAFF, [])
        maya = s["people"][0]
        self.assertEqual((maya["team"], maya["area"], maya["joined"]), ("INTAKE", "Proj\\Claims\\Intake", date(2026, 10, 6)))
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
        self.assertEqual(s["people"][0], {"name": "Ana K.", "emails": [ANA], "team": "", "joined": None,
                                          "items": 0, "area": ""})

    def test_count_suggestion(self):
        for tasks, stories, want in ((30, 4, "tasks"), (10, 8, "stories")):
            b = board(tasks, 0)
            for i in range(stories):
                b.item(200 + i, "User Story", area="Proj\\Claims\\Intake").at("2026-10-06 09:00", state="Active", assigned=MAYA) \
                    .at("2026-10-08 09:00", state="Closed")
            s = setup.suggest(b.cache(), STAFF, [])
            self.assertEqual(s["teams"]["INTAKE"], {"count": want, "stories": stories, "tasks": tasks})

    def test_waiting_state_suggestion(self):
        b = board()
        b.item(901).at("2026-10-06 09:00", state="Ready for QA", assigned=MAYA)
        b.item(902).at("2026-10-06 09:00", state="In Review", assigned=MAYA)
        s = setup.suggest(b.cache(), STAFF, [])
        self.assertEqual(s["waiting"], ["In Review", "Ready for QA"])
        self.assertIn({"name": "Active", "category": "InProgress"}, s["states"])


class InitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = os.path.join(self.tmp.name, "work")
        self.staff = os.path.join(self.tmp.name, "staff.txt")
        self.vac = os.path.join(self.tmp.name, "vac.xlsx")
        self.write_staff("Maya R. <%s>; John Doe <%s>" % (MAYA, JOHN))
        rows = calendar_rows()
        rows[6] = {4: "Nobody X"}
        build_workbook(self.vac, rows, ["G2:AJ2", "AK2:BO2"])

    def write_staff(self, text):
        with open(self.staff, "w") as f:
            f.write(text)

    def args(self):
        return Namespace(dir=self.d, query="Proj\\Claims", staff=self.staff, vacations=self.vac, yes=True,
                         org="https://dev.azure.com/org", project="P", timezone="Europe/Zurich")

    def run_init(self, data=None, ask=None):
        out = io.StringIO()
        with mock.patch("ledger.setup.fetch", return_value=deepcopy(data or board().cache())):
            code = setup.run_init(self.args(), object(), ask or (lambda p, d: d), out)
        return code, out.getvalue()

    def test_init_yes_writes_files_and_keeps_answers(self):
        code, text = self.run_init()
        self.assertEqual(code, 0)
        cfg = load_config(self.d)
        self.assertEqual((cfg.organization, cfg.project, cfg.timezone, cfg.teams),
                         ("https://dev.azure.com/org", "P", "Europe/Zurich", {"ALFA": {"count": "stories"}}))
        self.assertTrue(os.path.isabs(cfg.staff) and os.path.isabs(cfg.vacations))
        self.assertIn("Nobody X", text)
        self.assertIn("Active: InProgress", text)
        self.assertTrue(os.path.exists(os.path.join(self.d, "cache", "revisions.json")))
        # second run: edit an answer, add a person, drop another
        team_path = os.path.join(self.d, "team.csv")
        people = read_team(team_path)
        people[0].team = "BETA"
        from ledger.config import write_team, save_config
        write_team(team_path, people)
        cfg.name_aliases["Nobody X"] = "John Doe"
        cfg.waiting_states = ["Custom"]
        save_config(self.d, cfg)
        self.write_staff("Maya R. <%s>; Ana K. <%s>" % (MAYA, ANA))
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

    def test_unseen_email_typed_team_and_timezone(self):
        self.write_staff("Maya R. <%s>; John Doe <%s>; Ana K. <%s>; nobody" % (MAYA, JOHN, ANA))
        a = self.args()
        a.timezone = None
        with mock.patch("ledger.setup.machine_timezone", return_value=None):
            out = io.StringIO()
            with mock.patch("ledger.setup.fetch", return_value=board().cache()):
                setup.run_init(a, object(), lambda p, d: " gamma " if "Team? (Ana" in p else d, out)
        text = out.getvalue()
        self.assertIn("warning: %s is never assigned on the board; check the sign-in email" % ANA, text)
        self.assertNotIn("warning: %s is never" % MAYA, text)
        self.assertIn("'nobody' has no email address", text)
        self.assertIn("could not detect the time zone", text)
        self.assertEqual(load_config(self.d).timezone, "UTC")
        self.assertEqual({p.name: p.team for p in read_team(os.path.join(self.d, "team.csv"))}["Ana K."],
                         "GAMMA")

    def test_bad_timezone_stops_init(self):
        a = self.args()
        a.timezone = "Mars/Base"
        with self.assertRaises(ValueError):
            setup.run_init(a, object(), lambda p, d: d, io.StringIO())

    def test_unmatched_calendar_name_answer_is_remembered(self):
        asked = []
        self.run_init(ask=lambda p, d: asked.append(p) or ("John Doe" if "Nobody X" in p else d))
        self.assertIn("Which person in staff.txt is 'Nobody X'? (blank if none)", asked)
        self.assertEqual(load_config(self.d).name_aliases, {"Nobody X": "John Doe"})

    def test_init_stops_on_missing_fields(self):
        data = board().cache()
        del data["revisions"][0]["category"]
        with self.assertRaises(AdoError) as cm:
            self.run_init(data)
        self.assertIn("category", str(cm.exception))
        self.assertFalse(os.path.exists(self.d))
        data = board().cache()
        data["revisions"][0]["category"] = "Weird"
        with self.assertRaises(AdoError):
            self.run_init(data)
        self.assertFalse(os.path.exists(self.d))

    def test_missing_org_and_project(self):
        a = self.args()
        a.org = a.project = None
        with mock.patch("ledger.setup.az_defaults", return_value=(None, None)):
            with self.assertRaises(AdoError) as cm:
                setup.run_init(a, None, lambda p, d: d, io.StringIO())
        self.assertIn("az devops configure", cm.exception.next_step)


if __name__ == "__main__":
    unittest.main()
