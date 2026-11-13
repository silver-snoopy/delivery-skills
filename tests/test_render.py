import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from ledger import cli
from ledger.ado import CACHE_FILE
from ledger.config import Config, Notes, Person, save_config, write_team
from ledger.metrics import team_sprint
from ledger.render import folder_name, render_dm, render_manager, render_person
from tests.fixture import Board
from tests.test_xlsx import build_workbook, calendar_rows

MAYA, IVAN, LENA = "maya@vendor.example", "ivan.h@vendor.example", "lena@vendor.example"
IVAN2, PO = "ivan.holt@vendor.example", "po@customer.example"
PEOPLE = [Person("Maya Brook", [MAYA], "ALFA", None, None),
          Person("Ivan Holt", [IVAN, IVAN2], "ALFA", None, None),
          Person("Lena Ford", [LENA], "BETA", None, None)]
S28, S29 = "Proj\\Sprint 28", "Proj\\Sprint 29"
GEN = datetime(2026, 11, 13, 9, 12)


def board():
    b = Board().sprint("Sprint 28", "2026-10-13", "2026-10-24") \
        .sprint("Sprint 29", "2026-10-27", "2026-11-07")
    for i in (101, 102):
        b.item(i, parent=900, title="Upload <b>x</b>").at("2026-10-27 09:00", iteration=S29,
                                                         assigned=MAYA, state="Active") \
            .at("2026-11-0%d 10:00" % (i - 98), state="Closed")
    b.item(103, title="Retry & reconcile").at("2026-10-14 09:00", iteration=S28, assigned=IVAN2,
                                              state="Active").at("2026-10-27 08:00", iteration=S29)
    b.item(104).at("2026-10-28 09:00", iteration=S29, assigned=PO, state="Active") \
        .at("2026-11-04 09:00", state="Closed")
    b.item(900, "Epic", title="Claims intake")
    b.item(301, title="Quote PDF", area="Proj\\Pricing").at("2026-10-27 09:00", iteration=S29,
                                                            assigned=LENA, state="Active")
    for i in (302, 303, 304):
        b.item(i, "Task", parent=301, area="Proj\\Pricing").at(
            "2026-10-27 09:00", iteration=S29, assigned=LENA, state="Active") \
            .at("2026-11-04 09:00", state="Closed" if i < 304 else "Active")
    return b


def config():
    return Config(timezone="Europe/Zurich", waiting_states=["Ready for QA"],
                  teams={"ALFA": {"count": "stories"}, "BETA": {"count": "tasks"}})


def teams(notes=None):
    data = board().cache()
    sprint = next(s for s in data["iterations"] if s["name"] == "Sprint 29")
    return [team_sprint(data, config(), PEOPLE, t, sprint, notes or Notes(), {})
            for t in ("ALFA", "BETA")]


NOTES = Notes(summary={"ALFA": "Upload is live."}, note={"ALFA": "Ivan Holt covered support."},
              exec_note={"ALFA": "QA environment was down."},
              status={"BETA": ("AMBER", "PO asked to hold the release")})


def text(page):
    page = re.sub(r"(?s)<(style|title)>.*?</\1>", " ", page)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


class RenderTest(unittest.TestCase):
    def test_manager_has_no_names_or_emails(self):
        page = render_manager("Sprint 29", teams(NOTES), GEN)
        for p in PEOPLE:
            self.assertNotIn(p.name, page)
            for part in p.name.split():
                self.assertNotIn(part, page)
            for e in p.emails:
                self.assertNotIn(e, page)
        self.assertNotIn("covered support", page)
        self.assertIn("Upload is live.", page)
        self.assertIn("QA environment was down.", page)

    def test_wording_is_work_items(self):
        page = text(render_manager("Sprint 29", teams(), GEN))
        self.assertEqual(page.count("2 of 3 planned work items delivered"), 2)
        self.assertNotRegex(page, r"(?i)(stories|tasks) delivered")
        self.assertIn("ALFA counts stories", page)
        self.assertIn("BETA counts tasks", page)
        self.assertIn("Claims intake (2 work items)", page)
        self.assertIn("our convention, not an industry standard", page)
        for render in (render_manager, render_dm):
            page = text(render("Sprint 29", teams(), GEN))
            self.assertNotIn("on the customer", page)
            self.assertIn("waiting (in a waiting state or blocked)", page)
        for word in ("story", "task"):
            stripped = page.replace("counts stories", "").replace("counts tasks", "")
            stripped = re.sub(r"Method\..*", "", stripped)
            self.assertNotRegex(stripped, r"(?i)\b%ss?\b" % word)

    def test_manual_status_and_small_sample(self):
        page = text(render_manager("Sprint 29", teams(NOTES), GEN))
        self.assertIn("AMBER PO asked to hold the release (set by hand)", page)
        self.assertIn("cycle time median / P85 over the last 2 sprints", page)
        self.assertIn("Last 2 sprints Sprint 28 Sprint 29", page)

    def test_dm_starts_with_attention_and_private_marker(self):
        page = render_dm("Sprint 29", teams(NOTES), GEN)
        body = text(page[page.index("<body"):])
        self.assertTrue(body.startswith("Private: delivery manager only"))
        self.assertLess(body.index("Needs attention"), body.index("ours vs rest of squad"))
        self.assertLess(body.index("Ivan Holt covered support."), body.index("ours vs rest of squad"))
        self.assertIn("Use this as context, not as a ranking.", body)
        self.assertIn("Maya Brook", body)
        self.assertIn("#103 was carried over with no note in notes.txt.", body)
        self.assertIn("2 of 3 planned work items delivered", body)  # repeats the manager report

    def test_missing_sprint_is_listed(self):
        missing = [{"team": "GAMMA", "missing": True}]
        for render in (render_manager, render_dm):
            self.assertIn("No sprint named Sprint 29 for team GAMMA", render("Sprint 29", missing, GEN))

    def test_titles_are_escaped(self):
        for render in (render_manager, render_dm):
            page = render("Sprint 29", teams(), GEN)
            self.assertNotIn("<b>x</b>", page)
            self.assertIn("Retry &amp; reconcile", page)
        t = teams()
        t[0]["worked_on"] = [["<b>x</b>", 1]]
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", render_manager("Sprint 29", t, GEN))

    def test_no_script_tags_or_external_urls(self):
        for render in (render_manager, render_dm):
            page = render("Sprint 29", teams(NOTES), GEN)
            self.assertNotIn("<script", page.lower())
            self.assertNotIn("http", page)
            self.assertIn("prefers-color-scheme: dark", page)

    def test_person_page(self):
        rows = [{"sprint": "Sprint 28", "available": None},
                {"sprint": "Sprint 29", "available": 9.5, "nominal": 10, "delivered": 2,
                 "in_progress": 1, "median": 4, "items": [101, 102]}]
        page = text(render_person(PEOPLE[0], rows, GEN))
        self.assertIn("Private: delivery manager only", page)
        self.assertIn("Sprint 29 9.5 / 10 2 1 4 d #101 #102", page)
        self.assertIn("not on the team", page)

    def test_folder_name(self):
        self.assertEqual(folder_name('A/B\\C:D*E?F"G<H>I|J'), "A-B-C-D-E-F-G-H-I-J")


def load_vac(d):
    with open(os.path.join(d, "config.json"), encoding="utf-8") as f:
        return json.load(f)["vacations"]


class RunReportTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = os.path.join(tmp.name, "work")
        vac = os.path.join(tmp.name, "vac.xlsx")
        rows = calendar_rows()
        rows[4][4] = "Brook, Maya"
        rows[5] = {4: "Someone Else", 3: "Beta", 7 + 4: "x"}
        build_workbook(vac, rows, ["G2:AJ2", "AK2:BO2"])
        cfg = config()
        cfg.vacations = vac
        cfg.name_aliases = {"Someone Else": "Lena Ford"}
        save_config(self.d, cfg)
        write_team(os.path.join(self.d, "team.csv"), PEOPLE)
        os.makedirs(os.path.join(self.d, "cache"))
        with open(os.path.join(self.d, CACHE_FILE), "w") as f:
            json.dump(board().cache(), f)

    def run_cli(self, *argv):
        out = io.StringIO()
        code = cli.main(["--dir", self.d] + list(argv), out=out)
        return code, out.getvalue()

    def test_report_offline_writes_two_files_and_rerun_is_identical(self):
        contents = []
        for _ in range(2):
            code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
            self.assertEqual(code, 0, out)
            folder = os.path.join(self.d, "Sprint 29")
            paths = [os.path.join(folder, n) for n in ("manager_2026-11-13.html",
                                                        "dm_2026-11-13.html")]
            for p in paths:
                self.assertIn(p, out)
            pages = []
            for p in paths:
                with open(p, encoding="utf-8") as f:
                    pages.append(re.sub(r".*generated .*\n", "", f.read()))
            contents.append(pages)
        self.assertEqual(contents[0], contents[1])
        self.assertTrue(os.path.exists(os.path.join(self.d, "Sprint 29", "notes.txt")))
        self.assertIn("16 of 18 engineer-days available", text(contents[0][0]))  # Maya: 2 days off
        self.assertIn("8 of 9 engineer-days available", text(contents[0][0]))  # alias for Lena

    def test_report_prints_sprint_notes_carryovers_and_delivered(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertIn("sprint: Sprint 29", lines)
        self.assertIn("notes: " + os.path.join(self.d, "Sprint 29", "notes.txt"), lines)
        self.assertTrue([l for l in lines if re.match(r"ALFA delivered: #\d+ \S", l)], out)
        carry = [l for l in lines if l.startswith("ALFA carry-over without note: #")]
        self.assertTrue(carry, out)
        cid = re.match(r"ALFA carry-over without note: #(\d+)", carry[0]).group(1)
        with open(os.path.join(self.d, "Sprint 29", "notes.txt"), "w") as f:
            f.write("#%s agreed: ok\n" % cid)
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertNotIn("carry-over without note: #%s " % cid, out)

    def test_report_prints_notes_warnings_and_reads_notes(self):
        os.makedirs(os.path.join(self.d, "Sprint 28"))
        with open(os.path.join(self.d, "Sprint 28", "notes.txt"), "w") as f:
            f.write("ALFA summary: Retries done.\nhello there\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13",
                                 "--sprint", "Sprint 28")
        self.assertEqual(code, 0, out)
        self.assertIn("notes.txt line 2 ignored: hello there", out)
        with open(os.path.join(self.d, "Sprint 28", "manager_2026-11-13.html")) as f:
            page = f.read()
        self.assertIn("Retries done.", page)
        self.assertIn("No sprint named Sprint 28 for team BETA", page)

    def test_default_sprint_is_last_ended(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-07")
        self.assertEqual(code, 0, out)
        self.assertIn(os.path.join("Sprint 28", "manager_2026-11-07.html"), out)

    def test_offline_without_cache(self):
        os.remove(os.path.join(self.d, CACHE_FILE))
        code, out = self.run_cli("report", "--offline")
        self.assertEqual(code, 1)
        self.assertIn("next: run without --offline", out)

    def test_online_fetch_refreshes_cache(self):
        os.remove(os.path.join(self.d, CACHE_FILE))
        with mock.patch("ledger.render.fetch", return_value=board().cache()) as f:
            code, out = self.run_cli("report", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted(f.call_args[0][2]), sorted([MAYA, IVAN, IVAN2, LENA]))
        self.assertTrue(os.path.exists(os.path.join(self.d, CACHE_FILE)))

    def test_online_fetch_merges_into_the_cache(self):
        smaller = board().cache()
        smaller["revisions"] = [r for r in smaller["revisions"] if r["id"] != 104]
        with mock.patch("ledger.render.fetch", return_value=smaller) as f:
            code, out = self.run_cli("report", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertEqual(f.call_args[0][4], 200)  # history_days default
        with open(os.path.join(self.d, CACHE_FILE)) as fh:
            self.assertIn(104, {r["id"] for r in json.load(fh)["revisions"]})

    def edit(self, name, fn):
        path = os.path.join(self.d, name)
        with open(path, encoding="utf-8") as f:
            new = fn(f.read())
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)

    def test_team_renamed_in_team_csv_is_reported(self):
        self.edit("team.csv", lambda t: t.replace(",BETA,", ",gamma,"))
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertIn("warning: team GAMMA in team.csv has no counting level in config.json; "
                      "it counts stories", out)
        self.assertIn("warning: team BETA in config.json has no one in team.csv; it is not reported", out)
        self.assertNotIn("No sprint named", out)
        with open(os.path.join(self.d, "Sprint 29", "manager_2026-11-13.html"), encoding="utf-8") as f:
            page = text(f.read())
        self.assertIn("GAMMA counts stories", page)
        self.assertNotIn("BETA", page)

    def test_notes_bom_and_unused_lines_are_warnings(self):
        os.makedirs(os.path.join(self.d, "Sprint 29"))
        with open(os.path.join(self.d, "Sprint 29", "notes.txt"), "w", encoding="utf-8-sig") as f:
            f.write("#103 agreed: ok\n#999 agreed: typo\nDELTA summary: nobody\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertNotIn("line 1 ignored", out)
        self.assertNotIn("carry-over without note: #103", out)
        self.assertIn("warning: notes.txt #999 is not a carry-over of any team in Sprint 29", out)
        self.assertIn("warning: notes.txt has a DELTA summary line, but no team DELTA is in this "
                      "report", out)

    def test_calendar_checks(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("warning: no calendar row for Ivan Holt; their leave is not counted", out)
        self.assertNotIn("calendar does not cover", out)
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13", "--sprint", "Sprint 28")
        self.assertIn("warning: the calendar does not cover Sprint 28", out)

    def test_two_calendar_names_for_one_person_are_merged(self):
        vac = os.path.join(os.path.dirname(self.d), "vac2.xlsx")
        rows = calendar_rows()
        rows[4][4] = "Brook, Maya"
        rows[5] = {4: "Maya Brook", 7 + 5: "x"}  # 6 Nov, a second row for Maya
        build_workbook(vac, rows, ["G2:AJ2", "AK2:BO2"])
        old = json.dumps(load_vac(self.d))
        self.edit("config.json", lambda t: t.replace(old, json.dumps(vac)))
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        with open(os.path.join(self.d, "Sprint 29", "manager_2026-11-13.html"), encoding="utf-8") as f:
            self.assertIn("15 of 18 engineer-days available", text(f.read()))  # 2 + 1 days off

    def test_manager_file_names_are_warnings(self):
        self.edit(CACHE_FILE, lambda t: t.replace("Retry & reconcile", "Maya Brook: retry"))
        os.makedirs(os.path.join(self.d, "Sprint 29"))
        with open(os.path.join(self.d, "Sprint 29", "notes.txt"), "w") as f:
            f.write("ALFA summary: Ask IVAN.H@vendor.example.\nALFA note: Lena Ford helped.\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertIn("warning: manager file mentions Maya Brook", out)
        self.assertIn("warning: manager file mentions %s" % IVAN, out)
        self.assertNotIn("mentions Lena Ford", out)  # a note is in the DM file only

    def test_running_sprint_and_board_time(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-05", "--sprint", "Sprint 29")
        self.assertEqual(code, 0, out)
        self.assertIn("warning: Sprint 29 has not ended by 2026-11-05", out)
        for prefix in ("manager", "dm"):
            with open(os.path.join(self.d, "Sprint 29", "%s_2026-11-05.html" % prefix),
                      encoding="utf-8") as f:
                page = f.read()
            self.assertRegex(page, r"<h1>Sprint 29 delivery \w+ \(sprint still running\)</h1>")
            self.assertIn("Board data as of 2026-11-13 01:00 CET", page)
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertNotIn("has not ended", out)

    def test_missing_config(self):
        out = io.StringIO()
        self.assertEqual(cli.main(["--dir", self.d + "x", "report"], out=out), 1)
        self.assertIn("run ledger init first", out.getvalue())

    def test_unmatched_calendar_name_is_a_warning(self):
        cfg_path = os.path.join(self.d, "config.json")
        with open(cfg_path) as f:
            raw = json.load(f)
        raw["name_aliases"] = {}
        with open(cfg_path, "w") as f:
            json.dump(raw, f)
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertIn("Someone Else", out)

    def test_person(self):
        code, out = self.run_cli("person", MAYA, "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        path = os.path.join(self.d, "people", "person_Maya_Brook_2026-11-13.html")
        self.assertIn(path, out)
        with open(path, encoding="utf-8") as f:
            page = text(f.read())
        self.assertIn("Sprint 29 7 / 9 2 0 8 d #101 #102", page)
        code, out = self.run_cli("person", "nobody@vendor.example", "--offline")
        self.assertEqual(code, 1)
        self.assertIn("nobody@vendor.example", out)


if __name__ == "__main__":
    unittest.main()
