import io
import json
import os
import re
import tempfile
import unittest
from datetime import date, datetime
from html import unescape
from unittest import mock

from ledger import cli
from ledger.ado import CACHE_FILE
from ledger.config import Config, Notes, Person, parse_notes, save_config, write_team
from ledger.metrics import TREND, team_actions, team_sprint
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
    results = [team_sprint(data, config(), PEOPLE, t, sprint, notes or Notes(), {})
               for t in ("ALFA", "BETA")]
    for result in results:
        result["actions"] = team_actions(data, config(), PEOPLE, result["team"], result, None,
                                         date(2026, 11, 13), {})
    return results


NOTES = Notes(summary={"ALFA": "Upload is live."}, note={"ALFA": "Ivan Holt covered support."},
              exec_note={"ALFA": "QA environment was down."},
              status={"BETA": ("AMBER", "PO asked to hold the release")})


def text(page):
    page = re.sub(r"(?s)<(style|title)>.*?</\1>", " ", page)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


class RenderTest(unittest.TestCase):
    def first_pass_team(self, scored=25, returned=None, share=0.92, tracked=True):
        t = teams()[0]
        t["first_pass"] = {"scored": scored, "returned": [1, 2] if returned is None else returned,
                           "share": share, "tracked": tracked}
        return t

    def test_first_pass_figure_tracked(self):
        for share, percent in ((0.92, "92%"), (0.926, "93%")):
            with self.subTest(share=share):
                page = render_manager("Sprint 29", [self.first_pass_team(share=share)], GEN)
                self.assertIn('<span class="n">' + percent + '</span>'
                              '<span class="l">first-time pass (2 returned)</span>', page)

    def test_first_pass_figure_without_returns_has_no_count(self):
        page = render_manager("Sprint 29", [self.first_pass_team(share=1.0, returned=[])], GEN)
        self.assertIn('<span class="n">100%</span><span class="l">first-time pass</span>', page)
        self.assertNotIn("0 returned", page)

    def test_first_pass_not_tracked(self):
        page = render_manager("Sprint 29", [self.first_pass_team(
            scored=3, returned=[], share=1.0, tracked=False)], GEN)
        self.assertIn('<span class="n">not tracked</span><span class="l">first-time pass: '
                      'no item moved back on the board in the last %d sprints</span>' % TREND, page)
        figures = re.findall(r'<div class="fig[^"]*">(.*?)</div>', page)
        self.assertTrue(any("first-time pass" in f for f in figures))
        self.assertFalse(any("100%" in f for f in figures if "first-time pass" in f))

    def test_first_pass_nothing_delivered(self):
        for tracked in (True, False):
            with self.subTest(tracked=tracked):
                page = render_manager("Sprint 29", [self.first_pass_team(
                    scored=0, returned=[], share=None, tracked=tracked)], GEN)
                self.assertIn('<span class="n">–</span><span class="l">first-time pass</span>', page)

    def test_trend_row_first_pass(self):
        t = self.first_pass_team()
        t["trend"] = [dict(t["trend"][0], first_pass=share) for share in (0.5, None, 1.0)]
        for tracked, cells in ((True, '<td>50%</td><td>–</td><td>100%</td>'),
                               (False, '<td>not tracked</td><td>–</td><td>not tracked</td>')):
            t["first_pass"]["tracked"] = tracked
            for render in (render_manager, render_dm):
                with self.subTest(tracked=tracked, render=render.__name__):
                    page = render("Sprint 29", [t], GEN)
                    self.assertIn('<td>First-time pass</td>' + cells, page)

    def test_first_pass_figure_caps_percent_when_an_item_returned(self):
        for scored in (200, 1000):
            for render in (render_manager, render_dm):
                with self.subTest(scored=scored, render=render.__name__):
                    t = self.first_pass_team(scored=scored, returned=[1],
                                             share=(scored - 1) / scored)
                    page = render("Sprint 29", [t], GEN)
                    figure = re.search(r'<span class="n">([^<]+)</span>'
                                       r'<span class="l">first-time pass \(1 returned\)</span>', page)
                    self.assertIsNotNone(figure)
                    self.assertEqual(figure.group(1), "99%")

    def test_first_pass_trend_caps_nonperfect_share(self):
        t = self.first_pass_team()
        t["trend"] = [dict(t["trend"][0], first_pass=share)
                      for share in (0.995, 0.999, 1.0)]
        for render in (render_manager, render_dm):
            with self.subTest(render=render.__name__):
                page = render("Sprint 29", [t], GEN)
                row = re.search(r'<td>First-time pass</td>(.*?)</tr>', page).group(1)
                self.assertEqual(row, '<td>99%</td><td>99%</td><td>100%</td>')

    def test_dm_page_shows_first_pass(self):
        page = render_dm("Sprint 29", [self.first_pass_team()], GEN)
        people, team = page.split('<h2>Team figures</h2>')
        self.assertIn('<span class="n">92%</span>'
                      '<span class="l">first-time pass (2 returned)</span>', team)
        self.assertNotIn("first-time pass", people.lower())
        person = render_person(PEOPLE[0], [], GEN)
        self.assertNotIn("first-time pass", person.lower())

    def test_footnote_defines_first_pass(self):
        sentence = ('First-time pass = share of delivered work items never sent back from a '
                    'test state and never reopened, counted per team; “not tracked” means no '
                    'item moved back in the last %d sprints.' % TREND)
        for render in (render_manager, render_dm):
            with self.subTest(render=render.__name__):
                page = render("Sprint 29", [self.first_pass_team()], GEN)
                self.assertIn(sentence, text(page))

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

    def test_dm_starts_with_internal_marker_and_notes_before_comparison(self):
        page = render_dm("Sprint 29", teams(NOTES), GEN)
        body = text(page[page.index("<body"):])
        self.assertTrue(body.startswith(
            "Internal: delivery manager and team leads only. Not for the customer."))
        self.assertLess(body.index("Ivan Holt covered support."), body.index("ours vs rest of squad"))
        self.assertLess(body.index("ours vs rest of squad"), body.index("Person Work item Sprint"))
        self.assertLess(body.index("Person Work item Sprint"), body.index("Team figures"))
        self.assertIn("Use this as context, not as a ranking.", body)
        self.assertIn("Maya Brook", body)
        self.assertNotIn("Needs attention", body)
        self.assertNotIn("<h3>People</h3>", page)
        self.assertIn("2 of 3 planned work items delivered", body)

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
        self.assertNotIn("prefers-color-scheme", render_dm("Sprint 29", teams(NOTES), GEN))

    def test_manager_page_is_light_only(self):
        page = render_manager("Sprint 29", teams(NOTES), GEN)
        self.assertNotIn("prefers-color-scheme", page)
        self.assertNotIn("color-scheme:dark", page)
        self.assertIn("--paper:#FFF", page)

    def test_person_page(self):
        rows = [{"sprint": "Sprint 28", "available": None},
                {"sprint": "Sprint 29", "available": 9.5, "nominal": 10, "delivered": 2,
                 "in_progress": 1, "median": 4, "items": [101, 102]}]
        self.assertNotIn("prefers-color-scheme", render_person(PEOPLE[0], rows, GEN))
        page = text(render_person(PEOPLE[0], rows, GEN))
        self.assertIn("Private: delivery manager only", page)
        self.assertIn("Sprint 29 9.5 / 10 2 1 4 d #101 #102", page)
        self.assertIn("not on the team", page)

    def test_action_table_rowspans_order_badges_answers_and_escaping(self):
        t = teams(NOTES)[0]
        def row(i, person, level, badge, **extra):
            return dict(id=i, person=person, title="<img src=x onerror=bad> & title",
                        sprint="Now", sprint_path=S29, level=level, badge=badge,
                        kinds=[], problem="<problem>", ask="suggested action", age=12,
                        open=True, carry=False, **extra)
        t["actions"] = dict(current=dict(sprint=dict(name="Current <sprint>"), day=4, days=10,
                                          planned=[1, 2, 3], delivered_planned=[1], added=[4],
                                          removed=[2], held=[3], waiting=[4, 5]),
                            people=[dict(name="Zed <lead>", available=8.5, nominal=10, onboarding=True),
                                    dict(name="Amy", available=10, nominal=10, onboarding=False),
                                    dict(name="Bea", available=9, nominal=10, onboarding=False),
                                    dict(name="Cal", available=10, nominal=10, onboarding=False)],
                            rows=[row(9, "Amy", "red", "Stuck", handled=True, answer="<answer> & fixed"),
                                  row(8, "Zed <lead>", "green", "OK"),
                                  row(7, "Zed <lead>", "amber", "Waiting", worse=True, answer="chased"),
                                  row(6, "Zed <lead>", "red", "Stuck"),
                                  row(5, "Bea", "grey", "Check")])
        page = link_ids(render_dm("Sprint 29", [t], GEN), "https://dev.azure.com/acme", "Proj")
        before_figures = page.split("<h2>Team figures</h2>")[0]
        self.assertIn("<th>Person</th><th>Work item</th><th>Sprint</th><th>Signal</th>"
                      "<th>Problem</th><th>Action</th><th>Age</th>", before_figures)
        self.assertIn('<td rowspan="3">Zed &lt;lead&gt;', before_figures)
        self.assertIn("8.5 / 10 d available", text(before_figures))
        self.assertIn("onboarding", before_figures)
        self.assertIn("nothing open", before_figures)
        self.assertIn("Current &lt;sprint&gt; · day 4 of 10 working days · planned done 1 of 3"
                      " · +1 added / −1 removed · 1 On Hold · 2 waiting", before_figures)
        positions = [before_figures.index('>#%d</a>' % i) for i in (6, 7, 8, 5, 9)]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('title="worse since your answer">Waiting ↑</span>', before_figures)
        self.assertIn('class="chip grey">Handled</span>', before_figures)
        self.assertIn("&lt;answer&gt; &amp; fixed", before_figures)
        self.assertNotIn("<img", page)
        self.assertNotIn("<problem>", page)
        self.assertIn("&lt;img src=x onerror=bad&gt; &amp; title", page)
        self.assertIn("<td>12 d</td>", before_figures)

    def test_merged_action_asks_are_distinct_sentences_and_answer_is_unchanged(self):
        from tests.test_actions import merged_wording_board, config as action_config, PEOPLE as roster
        b = merged_wording_board()
        t = team_sprint(b.cache(), action_config(), roster, 'ALFA', b.sprints[-2], Notes(), {})
        t['actions'] = team_actions(b.cache(), action_config(), roster, 'ALFA', t,
                                   b.sprints[-1], date(2026, 11, 10), {})
        row = next(r for r in t['actions']['rows'] if r['id'] == 7101)
        expected = ('Raise it with the PO. Ask what is blocking it. '
                    'Confirm with the PO and record it in notes.md.')
        self.assertEqual(row['ask'], expected)
        page = render_dm('Old', [t], GEN).split('<h2>Team figures</h2>')[0]
        self.assertIn('<td>' + expected + '</td>', page)
        self.assertNotIn('.;', page)
        answer = 'DM text.; keep it exactly.  Next step'
        row['answer'] = answer
        page = render_dm('Old', [t], GEN).split('<h2>Team figures</h2>')[0]
        self.assertIn('<td>' + answer + '</td>', page)
        self.assertNotIn('<td>' + expected + '</td>', page)

    def test_handled_and_ok_people_share_one_name_sorted_group(self):
        t = teams()[0]
        t["actions"]["people"] = [dict(name=n, available=10, nominal=10, onboarding=False)
                                    for n in ("Zoe", "Amy")]
        t["actions"]["rows"] = [dict(id=7, person="Zoe", title="item", sprint="Now", level="red",
                                       badge="Stuck", problem="stuck", ask="ask", age=3, open=True,
                                       handled=True, answer="fixed")]
        page = render_dm("Sprint 29", [t], GEN).split("<h2>Team figures</h2>")[0]
        self.assertLess(page.index('>Amy<br>'), page.index('>Zoe<br>'))

    def test_suggestions_and_problems_use_markdown_notes(self):
        page = render_dm("Sprint 29", teams(), GEN)
        self.assertNotIn("notes.txt", page)
        self.assertIn("notes.md", page)

    def test_manager_equivalent_markdown_notes_keep_expected_layout(self):
        notes = parse_notes("# ALFA\n## Summary\nUpload is live.\n## Exec note\n"
                            "QA environment was down.\n## Note\nIvan Holt covered support.\n"
                            "# BETA\n## Status\nAMBER - PO asked to hold the release\n")
        self.assertEqual(render_manager("Sprint 29", teams(notes), GEN),
                         render_manager("Sprint 29", teams(NOTES), GEN))

    def test_folder_name(self):
        self.assertEqual(folder_name('A/B\\C:D*E?F"G<H>I|J'), "A-B-C-D-E-F-G-H-I-J")


def load_vac(d):
    with open(os.path.join(d, "config.json"), encoding="utf-8") as f:
        return json.load(f)["vacations"]


class RunReportTest(unittest.TestCase):
    def test_warning_without_test_states(self):
        warning = ("warning: no test states in config.json; first-time pass counts reopens only")
        self.edit("config.json", lambda s: json.dumps(
            {k: v for k, v in json.loads(s).items() if k != "test_states"}))
        for states in (None, [], ["Ready for QA"]):
            with self.subTest(states=states):
                if states is not None:
                    self.edit("config.json", lambda s: json.dumps(dict(json.loads(s), test_states=states)))
                code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
                self.assertEqual(code, 0, out)
                self.assertEqual(out.splitlines().count(warning), 0 if states else 1, out)

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

    def read_report(self, filename, folder="Sprint 29"):
        with open(os.path.join(self.d, folder, filename), encoding="utf-8") as f:
            return f.read()

    def write_notes(self, content, folder="Sprint 29"):
        os.makedirs(os.path.join(self.d, folder), exist_ok=True)
        with open(os.path.join(self.d, folder, "notes.md"), "w", encoding="utf-8") as f:
            f.write(content)

    def test_default_separates_running_actions_from_closed_manager(self):
        def block(t):
            data = json.loads(t)
            for row in data["revisions"]:
                if row["id"] == 304:
                    row["tags"] = ["Blocked"]
            return json.dumps(data)
        self.edit(CACHE_FILE, block)
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-05")
        self.assertEqual(code, 0, out)
        manager = self.read_report("manager_2026-11-05.html", "Sprint 28")
        dm = self.read_report("dm_2026-11-05.html", "Sprint 28")
        self.assertIn("Sprint 28 delivery summary", manager)
        self.assertNotIn("day 7 of 9", manager)
        self.assertIn("Sprint 29 · day 7 of 9 working days · planned done 2 of 3", text(dm))
        self.assertIn("BETA: ours vs rest of squad", dm)  # BETA has no closed sprint
        beta_actions = dm.split("BETA: ours vs rest of squad")[1].split("<h2>Team figures</h2>")[0]
        self.assertIn("No closed sprint available for comparison.", beta_actions)
        self.assertNotIn("No other delivered work items", beta_actions)
        self.assertIn("Lena Ford", dm)
        self.assertIn("<td>Now</td>", dm)
        self.assertIn("Board data as of 2026-11-13 01:00 CET", manager)
        self.assertIn("BETA action:", out)

    def test_running_rerun_ignores_later_same_day_closure_and_reassignment(self):
        self.run_cli("report", "--offline", "--today", "2026-11-05")
        self.edit("Sprint 28/notes.md", lambda t: t.replace(
            "|  |  |", "| chased: after standup |  |"))
        self.run_cli("report", "--offline", "--today", "2026-11-05")
        first = self.read_report("dm_2026-11-05.html", "Sprint 28").split("<h2>Team figures</h2>")[0]
        notes = self.read_report("notes.md", "Sprint 28")
        state = self.read_report(".notes-state.json", "Sprint 28")
        def close(t):
            data = json.loads(t)
            row = dict(next(r for r in data["revisions"] if r["id"] == 103))
            row.update(rev=3, changed="2026-11-05T10:00:00Z", state="Closed", category="Completed",
                       assigned=PO)
            data["revisions"].append(row)
            return json.dumps(data)
        self.edit(CACHE_FILE, close)
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-05")
        self.assertEqual(code, 0, out)
        second = self.read_report("dm_2026-11-05.html", "Sprint 28").split("<h2>Team figures</h2>")[0]
        self.assertEqual(re.sub(r".*generated .*\n", "", first),
                         re.sub(r".*generated .*\n", "", second))
        self.assertEqual(notes, self.read_report("notes.md", "Sprint 28"))
        self.assertEqual(state, self.read_report(".notes-state.json", "Sprint 28"))
        self.assertIn("Handled", second)

    def test_duplicate_item_ids_use_each_teams_agreement(self):
        b = Board().sprint("Sprint 29", "2026-10-27", "2026-11-07")
        b.item(10).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active")
        b.item(11, "Task", parent=10).at("2026-10-27 09:00", iteration=S29,
                                        assigned=LENA, state="Active")
        self.edit(CACHE_FILE, lambda t: json.dumps(b.cache()))
        self.edit("config.json", lambda t: t.replace('"tasks"', '"stories"'))
        self.write_notes("# ALFA\n## Actions\n| Maya Brook | #10 Item | carry | agreed: split | |\n"
                         "# BETA\n## Actions\n| Lena Ford | #10 Item | carry | not agreed: chase | |\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        page = self.read_report("manager_2026-11-13.html")
        alfa = next(a for a in re.findall(r'(?s)<article.*?</article>', page) if '<h2>ALFA</h2>' in a)
        beta = next(a for a in re.findall(r'(?s)<article.*?</article>', page) if '<h2>BETA</h2>' in a)
        self.assertIn('class="chip yes">Yes', alfa)
        self.assertIn('class="chip no">No', beta)
        self.assertNotIn('carry-over without note: #10', out)

    def test_missing_closed_team_keeps_existing_note_and_running_answer(self):
        self.write_notes("# BETA\n## Note\nKeep the running discussion.\n## Actions\n"
                         "| Lena Ford | #304 Item | Waiting | chased: tomorrow | |\n", "Sprint 28")
        def block(t):
            data = json.loads(t)
            for row in data["revisions"]:
                if row["id"] == 304:
                    row["tags"] = ["Blocked"]
            return json.dumps(data)
        self.edit(CACHE_FILE, block)
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-05")
        self.assertEqual(code, 0, out)
        dm = self.read_report("dm_2026-11-05.html", "Sprint 28")
        self.assertIn("Keep the running discussion.", dm)
        self.assertIn("chased: tomorrow", dm)
        self.assertIn("Handled", dm)
        self.assertIn("chased: tomorrow", self.read_report("notes.md", "Sprint 28"))
        self.assertNotIn("BETA action:", out)

    def test_named_past_sprint_suppresses_running_actions(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-05",
                                 "--sprint", "Sprint 28")
        self.assertEqual(code, 0, out)
        dm = self.read_report("dm_2026-11-05.html", "Sprint 28")
        self.assertNotIn("<td>Now</td>", dm)
        self.assertNotIn("working days · planned done", dm)
        self.assertNotIn("BETA action:", out)

    def test_legacy_notes_are_ignored(self):
        os.makedirs(os.path.join(self.d, "Sprint 29"))
        with open(os.path.join(self.d, "Sprint 29", "notes.txt"), "w") as f:
            f.write("ALFA summary: Legacy highlights.\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertNotIn("Legacy highlights.", self.read_report("manager_2026-11-13.html"))
        self.assertIn("notes.md", out)

    def test_agreement_refresh_does_not_rebase_or_loop_on_rerun(self):
        self.write_notes("# BETA\n## Actions\n"
                         "| Lena Ford | #304 Item 304 | carry | agreed: split later | |\n")
        results = []
        for _ in range(3):
            code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
            self.assertEqual(code, 0, out)
            dm = self.read_report("dm_2026-11-13.html")
            action_card = dm.split("<h2>Team figures</h2>")[0]
            beta = action_card.split("BETA: ours vs rest of squad")[1]
            self.assertNotIn("no PO answer", beta)
            self.assertNotIn("not recorded as agreed", text(dm).split("BETA")[-1])
            self.assertNotIn("BETA action:", out)
            results.append((self.read_report("notes.md"), self.read_report(".notes-state.json")))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertIn("agreed: split later", results[0][0])

    def test_agreement_only_signal_resolves_and_copied_answer_cannot_renew_it(self):
        b = Board().sprint("Sprint 29", "2026-10-27", "2026-11-07")
        carry = b.item(10).at("2026-10-27 09:00", iteration=S29, assigned=MAYA).at(
            "2026-11-06 09:00", state="Active")
        b.item(11).at("2026-10-27 09:00", iteration=S29, assigned=IVAN, state="Active").at(
            "2026-11-04 09:00", state="Closed")
        self.edit(CACHE_FILE, lambda t: json.dumps(b.cache()))
        self.write_notes("# ALFA\n## Actions\n| Maya Brook | #10 Item 10 | carry | agreed: split | |\n")
        results = []
        for _ in range(3):
            code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
            self.assertEqual(code, 0, out)
            dm = self.read_report("dm_2026-11-13.html")
            self.assertNotIn("#10", dm.split("<h2>Team figures</h2>")[0])
            self.assertNotIn("carry-over without note: #10", out)
            saved = parse_notes(self.read_report("notes.md"))
            self.assertFalse(any(r["id"] == 10 for r in saved.teams["ALFA"]["Actions"]))
            self.assertTrue(any(r["id"] == 10 and r["answer"] == "agreed: split"
                                for r in saved.teams["ALFA"]["Resolved"]))
            results.append((self.read_report("notes.md"), self.read_report(".notes-state.json")))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        # Changed closed sprint copies open Actions answers. Bring back an independent
        # signal in the old folder first, so the agreement answer remains an Action there.
        for row in carry.rows:
            row["tags"] = ["Blocked"]
        self.edit(CACHE_FILE, lambda t: json.dumps(b.cache()))
        self.run_cli("report", "--offline", "--today", "2026-11-13")
        b.sprint("Sprint 30", "2026-11-10", "2026-11-21")
        carry.at("2026-11-10 09:00", iteration="Proj\\Sprint 30")
        self.edit(CACHE_FILE, lambda t: json.dumps(b.cache()))
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-23")
        self.assertEqual(code, 0, out)
        dm = self.read_report("dm_2026-11-23.html", "Sprint 30")
        self.assertIn("agreed: split", dm)
        self.assertIn('title="worse since your answer"', dm)
        manager = self.read_report("manager_2026-11-23.html", "Sprint 30")
        self.assertIn('class="chip unk">Unknown', manager)

    def test_all_note_warnings_are_printed_once(self):
        self.write_notes("preamble\n# ALFA\n## Status\nBLUE - invalid\n## Actions\n"
                         "| Ivan Holt | #103 Retry | carry | chased | nonsense |\n"
                         "| broken | table |\n# UNKNOWN\n## Note\nkeep this\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        warnings = [line for line in out.splitlines() if line.startswith("warning: notes.md")]
        for part in ("preamble", "invalid Status", "broken", "UNKNOWN", "invalid Answered"):
            self.assertTrue(any(part in line for line in warnings), out)
        self.assertEqual(len(warnings), len(set(warnings)), out)
        self.assertIn("keep this", self.read_report("notes.md"))

    def test_console_actions_skip_answered_checks_and_ok_but_include_person_rows(self):
        self.write_notes("# ALFA\n## Actions\n"
                         "| Ivan Holt | #103 Retry | carry | chased: tomorrow | |\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        lines = [line for line in out.splitlines() if " action: " in line]
        self.assertTrue(lines, out)
        self.assertFalse(any("#103" in line for line in lines), out)
        self.assertTrue(any(line.startswith("BETA action: Lena Ford #304 ") for line in lines), out)
        self.assertTrue(any(line.startswith("ALFA action: Ivan Holt Nothing delivered - ")
                            for line in lines), out)
        self.assertFalse(any(" OK - " in line or " Check - " in line for line in lines))

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
        self.assertTrue(os.path.exists(os.path.join(self.d, "Sprint 29", "notes.md")))
        self.assertIn("2 engineers (16 PD capacity)", text(contents[0][0]))  # Maya: 2 days off
        self.assertIn("1 engineer (8 PD capacity)", text(contents[0][0]))  # alias for Lena

    def test_report_prints_sprint_notes_carryovers_and_delivered(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertIn("sprint: Sprint 29", lines)
        self.assertIn("notes: " + os.path.join(self.d, "Sprint 29", "notes.md"), lines)
        self.assertIn("ALFA worked on: #101 Upload <b>x</b> (delivered)", lines)
        self.assertIn("ALFA worked on: #103 Retry & reconcile (in progress)", lines)
        self.assertFalse([l for l in lines if " delivered: #" in l])
        carry = [l for l in lines if l.startswith("ALFA carry-over without note: #")]
        self.assertTrue(carry, out)
        cid = re.match(r"ALFA carry-over without note: #(\d+)", carry[0]).group(1)
        with open(os.path.join(self.d, "Sprint 29", "notes.md"), "w") as f:
            f.write("# ALFA\n## Actions\n| Ivan Holt | #%s Retry | carry | agreed: ok | |\n" % cid)
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertNotIn("carry-over without note: #%s " % cid, out)

    def test_report_prints_notes_warnings_and_reads_notes(self):
        os.makedirs(os.path.join(self.d, "Sprint 28"))
        with open(os.path.join(self.d, "Sprint 28", "notes.md"), "w") as f:
            f.write("hello there\n# ALFA\n## Summary\nRetries done.\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13",
                                 "--sprint", "Sprint 28")
        self.assertEqual(code, 0, out)
        self.assertIn("notes.md line 1 preserved: hello there", out)
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
        with open(os.path.join(self.d, "Sprint 29", "notes.md"), "w") as f:
            f.write("# ALFA\n## Summary\nUpload is live.\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("warning: BETA has no summary in notes.md; the manager card shows no "
                      "highlights", out)
        self.assertNotIn("warning: ALFA has no summary", out)

    def test_report_links_ids_when_board_is_known(self):
        self.edit("config.json", lambda t: t.replace('"organization": ""',
                                                     '"organization": "https://dev.azure.com/acme"')
                  .replace('"project": ""', '"project": "Proj"'))
        self.run_cli("report", "--offline", "--today", "2026-11-13")
        for prefix in ("manager", "dm"):
            with open(os.path.join(self.d, "Sprint 29", "%s_2026-11-13.html" % prefix)) as f:
                self.assertIn('href="https://dev.azure.com/acme/Proj/_workitems/edit/103"', f.read())

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
        with open(os.path.join(self.d, "Sprint 29", "notes.md"), "w", encoding="utf-8-sig") as f:
            f.write("# ALFA\n## Actions\n| Ivan Holt | #103 Retry | carry | agreed: ok | |\n"
                    "| Ivan Holt | #999 Typo | carry | agreed: typo | |\n# DELTA\n## Summary\nnobody\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertNotIn("line 1 ignored", out)
        self.assertNotIn("carry-over without note: #103", out)
        self.assertIn("#999 Typo", self.read_report("notes.md"))
        self.assertIn("warning: notes.md team DELTA is not in this report; preserved and excluded", out)

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
        with open(os.path.join(self.d, "Sprint 29", "notes.md"), "w") as f:
            f.write("# ALFA\n## Summary\nAsk IVAN.H@vendor.example.\n## Note\nLena Ford helped.\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertIn("warning: manager file mentions Maya Brook", out)
        self.assertIn("warning: manager file mentions %s" % IVAN, out)
        self.assertNotIn("mentions Lena Ford", out)  # a note is in the DM file only

    def test_names_match_whole_words(self):
        self.edit("team.csv", lambda t: t + "Ann,ann@vendor.example,ALFA,,,1\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertNotIn("mentions Ann", out)  # "planned" contains "ann"
        with open(os.path.join(self.d, "Sprint 29", "notes.md"), "w") as f:
            f.write("# ALFA\n## Actions\n| Ivan Holt | #103 Retry | carry | agreed: ANN is on leave | |\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("warning: manager file mentions Ann", out)

    def test_named_running_or_future_sprint_fails(self):
        for today in ("2026-11-05", "2026-10-26", "2026-11-07"):
            code, out = self.run_cli("report", "--offline", "--today", today,
                                     "--sprint", "Sprint 29")
            self.assertEqual(code, 1, out)
            self.assertIn("error: sprint 'Sprint 29' has not ended before " + today, out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "Sprint 29")))

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
