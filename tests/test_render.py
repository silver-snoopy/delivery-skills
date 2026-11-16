import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from html import unescape
from unittest import mock

from ledger import cli
from ledger.ado import CACHE_FILE
from ledger.config import Config, Notes, Person, save_config, write_team
from ledger.metrics import team_sprint
from ledger.render import folder_name, link_ids, render_dm, render_manager, render_person
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
        self.assertNotRegex(page, r"(?i)\b(story|stories|task|tasks)\b")
        dm = text(render_dm("Sprint 29", teams(), GEN))
        self.assertIn("Counts: stories", dm)
        self.assertIn("Counts: tasks", dm)
        self.assertIn("Claims intake (2 work items)", dm)
        self.assertIn("our convention, not an industry standard", dm)
        self.assertIn("waiting (in a waiting state or blocked)", dm)
        for page in (page, dm):
            self.assertNotIn("on the customer", page)
        stripped = dm.replace("Counts: stories", "").replace("Counts: tasks", "")
        stripped = re.sub(r"Method\..*", "", stripped)
        for word in ("story", "task"):
            self.assertNotRegex(stripped, r"(?i)\b%ss?\b" % word)

    def test_manual_status_and_small_sample(self):
        page = text(render_dm("Sprint 29", teams(NOTES), GEN))
        self.assertIn("AMBER PO asked to hold the release (set by hand)", page)
        page = text(render_manager("Sprint 29", teams(NOTES), GEN))
        self.assertNotIn("AMBER", page)
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
        missing = [{"team": "GAMMA", "missing": "No sprint named Sprint 29."}]
        for render in (render_manager, render_dm):
            self.assertIn('<article class="squad"><div class="top"><h2>GAMMA</h2></div>'
                          '<p class="note">No sprint named Sprint 29.</p></article>',
                          render("Sprint 29", missing, GEN))

    def card(self, page, team):
        """The article for `team` in `page`."""
        return next(a for a in re.findall(r'(?s)<article class="squad">.*?</article>', page)
                    if "<h2>%s</h2>" % team in a)

    def test_manager_card_layout(self):
        card = self.card(render_manager("Sprint 29", teams(NOTES), GEN), "ALFA")
        order = ["<h2>ALFA</h2>", "Sprint 29 (27 Oct – 7 Nov 2026)", "<h3>Delivery highlights</h3>",
                 "Upload is live.", "QA environment was down.", "<h3>Delivery stats</h3>",
                 "work items carried over", "<summary>Delivered items (2)</summary>",
                 "<summary>Carried over items (1)</summary>", "<summary>Last 2 sprints</summary>"]
        self.assertEqual([card.index(o) for o in order], sorted(card.index(o) for o in order))
        self.assertIn('<span class="cap">Sprint 29 (27 Oct – 7 Nov 2026)<br>2 engineers (18 PD '
                      'capacity)</span>', card)
        for absent in ("Counts:", "GREEN", "AMBER", "RED", "Worked on", "worked on", "Age"):
            self.assertNotIn(absent, card)
        self.assertIn("<td>Planned</td>", card)
        dm = self.card(render_dm("Sprint 29", teams(NOTES), GEN).split("<h2>Team figures</h2>")[1],
                       "ALFA")
        self.assertIn("<br>Counts: stories</span>", dm)

    def test_manager_card_without_summary_has_no_highlights(self):
        self.assertNotIn("Delivery highlights", render_manager("Sprint 29", teams(), GEN))

    def test_manager_card_exec_note_without_summary_keeps_highlights(self):
        card = self.card(render_manager("Sprint 29", teams(Notes(exec_note={"ALFA": "Pilot live"})),
                                        GEN), "ALFA")
        self.assertIn("<h3>Delivery highlights</h3><p>Pilot live</p>", card)

    def test_manager_card_multiline_summary_renders_as_bullets(self):
        notes = Notes(summary={"ALFA": "Delivered upload\nIn progress: retries"})
        card = self.card(render_manager("Sprint 29", teams(notes), GEN), "ALFA")
        self.assertIn("<ul><li>Delivered upload</li><li>In progress: retries</li></ul>", card)

    def test_manager_card_single_line_summary_unchanged(self):
        card = self.card(render_manager("Sprint 29", teams(NOTES), GEN), "ALFA")
        self.assertIn("<h3>Delivery highlights</h3><p>Upload is live.</p>", card)

    def test_dm_card_unchanged(self):
        page = render_dm("Sprint 29", teams(NOTES), GEN).split("<h2>Team figures</h2>")[1]
        card = self.card(page, "ALFA")
        order = ['<span class="chip ', "The team worked on", "Upload is live.",
                 '<div class="figs">', "<h3>Carried over</h3>", "<h3>Last 2 sprints</h3>"]
        self.assertEqual([card.index(o) for o in order], sorted(card.index(o) for o in order))
        self.assertNotIn("work items carried over", card)
        self.assertNotIn("<details>", card)
        self.assertIn("<th>Age</th>", card)

    def test_dm_card_multiline_summary_renders_as_bullets(self):
        notes = Notes(summary={"ALFA": "Delivered upload\nIn progress: retries"})
        page = render_dm("Sprint 29", teams(notes), GEN).split("<h2>Team figures</h2>")[1]
        self.assertIn("<ul><li>Delivered upload</li><li>In progress: retries</li></ul>",
                      self.card(page, "ALFA"))

    def test_carry_and_person_show_days_on_hold(self):
        t = teams()
        t[0]["carry"][0]["held"] = 3
        t[0]["people"][0]["held"] = [[103, 3], [7, 1]]
        dm = render_dm("Sprint 29", t, GEN)
        self.assertIn("<h3>Carried over (1 On Hold)</h3>", dm)
        self.assertIn("<td>3 d</td>", dm)
        self.assertIn("<th>On Hold</th>", dm)
        self.assertIn("<td>#103 3 days, #7 1 day</td>", dm)
        self.assertIn("<summary>Carried over items (1) (1 On Hold)</summary>",
                      render_manager("Sprint 29", t, GEN))
        for render in (render_manager, render_dm):
            self.assertIn("On Hold = days the work item spent in a hold state or tagged Blocked",
                          render("Sprint 29", t, GEN))

    def test_manager_empty_carryover_has_visible_panel(self):
        t = teams()
        t[1]["carry"] = []
        page = render_manager("Sprint 29", t, GEN)
        self.assertIn(".carry-empty{", page)
        card = self.card(page, "BETA")
        self.assertIn('<div class="carry-empty"><h3>Carried over items (0)</h3><p class="note">No '
                      'work items carried over.</p></div>', card)
        self.assertNotIn("<summary>Carried over", card)
        dm = self.card(render_dm("Sprint 29", t, GEN).split("<h2>Team figures</h2>")[1], "BETA")
        self.assertIn('<div><h3>Carried over</h3><p class="note">No work items carried over.</p></div>',
                      dm)
        self.assertNotIn("carry-empty", dm)

    def test_work_items_link_to_azure_devops(self):
        page = link_ids(render_manager("Sprint 29", teams(NOTES), GEN),
                        "https://dev.azure.com/acme/", "My Project")
        self.assertIn('<a href="https://dev.azure.com/acme/My%20Project/_workitems/edit/101">#101</a>',
                      page)
        self.assertNotIn("<a ", page[:page.index("</style>")])
        self.assertEqual(link_ids("<style>x</style><p>#1</p>", "", "P"), "<style>x</style><p>#1</p>")
        self.assertEqual(link_ids("<style></style><p>&#39;#2</p>", "https://o", "P"),
                         '<style></style><p>&#39;<a href="https://o/P/_workitems/edit/2">#2</a></p>')

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
        self.assertIn("prefers-color-scheme: dark", render_dm("Sprint 29", teams(NOTES), GEN))

    def test_manager_page_is_light_only(self):
        page = render_manager("Sprint 29", teams(NOTES), GEN)
        self.assertNotIn("prefers-color-scheme", page)
        self.assertNotIn("color-scheme:dark", page)
        self.assertIn("--paper:#FFF", page)

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
        self.assertIn("2 engineers (16 PD capacity)", text(contents[0][0]))  # Maya: 2 days off
        self.assertIn("1 engineer (8 PD capacity)", text(contents[0][0]))  # alias for Lena

    def test_report_prints_sprint_notes_carryovers_and_delivered(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertIn("sprint: Sprint 29", lines)
        self.assertIn("notes: " + os.path.join(self.d, "Sprint 29", "notes.txt"), lines)
        self.assertIn("ALFA worked on: #101 Upload <b>x</b> (delivered)", lines)
        self.assertIn("ALFA worked on: #103 Retry & reconcile (in progress)", lines)
        self.assertFalse([l for l in lines if " delivered: #" in l])
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
        self.assertIn("No sprint named Sprint 28.", page)
        self.assertIn("BETA: No sprint named Sprint 28.", out)

    def test_default_sprint_is_last_ended(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-07")
        self.assertEqual(code, 0, out)
        self.assertIn(os.path.join("Sprint 28", "manager_2026-11-07.html"), out)

    def test_default_takes_each_teams_last_ended_sprint_when_names_differ(self):
        def rename(t):
            data = json.loads(t)
            for it in data["iterations"]:
                if it["path"] == S29:
                    it["path"], it["name"], it["end"] = "Proj\\Pricing 29", "Pricing 29", "2026-11-06"
            for r in data["revisions"]:
                if r["area"] == "Proj\\Pricing" and r["iteration"] == S29:
                    r["iteration"] = "Proj\\Pricing 29"
            data["iterations"].append({"path": S29, "name": "Sprint 29", "start": "2026-10-27",
                                       "end": "2026-11-07"})
            return json.dumps(data)
        self.edit(CACHE_FILE, rename)
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertIn("sprint: Sprints ended by 2026-11-07", out)
        folder = os.path.join(self.d, "Sprints ended by 2026-11-07")
        with open(os.path.join(folder, "manager_2026-11-13.html"), encoding="utf-8") as f:
            page = text(f.read())
        self.assertIn("Sprint 29 (27 Oct – 7 Nov 2026)", page)
        self.assertIn("Pricing 29 (27 Oct – 6 Nov 2026)", page)

    def test_team_without_any_sprint_says_so(self):
        self.edit("team.csv", lambda t: t + "Nadia Kern,nadia@vendor.example,GAMMA,,,1\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        why = ("No work assigned: none of the team's 1 engineer has a work item assigned in the "
               "board history (last 200 days).")
        self.assertIn("GAMMA: " + why, out)
        with open(os.path.join(self.d, "Sprint 29", "manager_2026-11-13.html"), encoding="utf-8") as f:
            self.assertIn(why, unescape(f.read()))
        def backlog_item(t):
            data = json.loads(t)
            row = dict(data["revisions"][0], id=999, rev=1, iteration="Proj",
                       assigned="nadia@vendor.example")
            data["revisions"].append(row)
            return json.dumps(data)
        self.edit(CACHE_FILE, backlog_item)
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("GAMMA: No ended sprint: none of the team's counted work items is on a sprint.",
                      out)

    def test_team_without_summary_is_a_warning(self):
        os.makedirs(os.path.join(self.d, "Sprint 29"))
        with open(os.path.join(self.d, "Sprint 29", "notes.txt"), "w") as f:
            f.write("ALFA summary: Upload is live.\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("warning: BETA has no summary in notes.txt; the manager card shows no "
                      "highlights", out)
        self.assertNotIn("warning: ALFA has no summary", out)

    def test_report_links_ids_when_board_is_known(self):
        self.edit("config.json", lambda t: t.replace('"organization": ""',
                                                     '"organization": "https://dev.azure.com/acme"')
                  .replace('"project": ""', '"project": "Proj"'))
        self.run_cli("report", "--offline", "--today", "2026-11-13")
        for prefix in ("manager", "dm"):
            with open(os.path.join(self.d, "Sprint 29", "%s_2026-11-13.html" % prefix)) as f:
                self.assertIn('href="https://dev.azure.com/acme/Proj/_workitems/edit/101"', f.read())

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
        self.assertIn(104, f.call_args[1]["known"])  # cached ids are fetched again
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
        with open(os.path.join(self.d, "Sprint 29", "dm_2026-11-13.html"), encoding="utf-8") as f:
            page = text(f.read())
        self.assertIn("GAMMA", page)
        self.assertIn("Counts: stories", page)
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
            self.assertIn("2 engineers (15 PD capacity)", text(f.read()))  # 2 + 1 days off

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

    def test_names_match_whole_words(self):
        self.edit("team.csv", lambda t: t + "Ann,ann@vendor.example,ALFA,,,1\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertNotIn("mentions Ann", out)  # "planned" contains "ann"
        with open(os.path.join(self.d, "Sprint 29", "notes.txt"), "w") as f:
            f.write("#103 agreed: ANN is on leave\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("warning: manager file mentions Ann", out)

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
