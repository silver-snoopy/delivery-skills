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
from ledger.render import folder_name, link_ids, render_dm, render_manager, render_person, report_folder
from tests.fixture import Board
from tests.test_xlsx import build_workbook, calendar_rows

MAYA, IVAN, LENA = "maya@vendor.example", "ivan.h@vendor.example", "lena@vendor.example"
IVAN2, PO = "ivan.holt@vendor.example", "po@customer.example"
PEOPLE = [
    Person("Maya Brook", [MAYA], "ALFA", None, None),
    Person("Ivan Holt", [IVAN, IVAN2], "ALFA", None, None),
    Person("Lena Ford", [LENA], "BETA", None, None),
]
S28, S29 = "Proj\\Sprint 28", "Proj\\Sprint 29"
GEN = datetime(2026, 11, 13, 9, 12)


def board():
    b = (
        Board()
        .sprint("Sprint 28", "2026-10-13", "2026-10-24")
        .sprint("Sprint 29", "2026-10-27", "2026-11-07")
    )
    for i in (101, 102):
        b.item(i, parent=900, title="Upload <b>x</b>").at(
            "2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active"
        ).at(f"2026-11-0{i - 98} 10:00", state="Closed")
    b.item(103, title="Retry & reconcile").at(
        "2026-10-14 09:00", iteration=S28, assigned=IVAN2, state="Active"
    ).at("2026-10-27 08:00", iteration=S29)
    b.item(104).at("2026-10-28 09:00", iteration=S29, assigned=PO, state="Active").at(
        "2026-11-04 09:00", state="Closed"
    )
    b.item(900, "Epic", title="Claims intake")
    b.item(301, title="Quote PDF", area="Proj\\Pricing").at(
        "2026-10-27 09:00", iteration=S29, assigned=LENA, state="Active"
    )
    for i in (302, 303, 304):
        b.item(i, "Task", parent=301, area="Proj\\Pricing").at(
            "2026-10-27 09:00", iteration=S29, assigned=LENA, state="Active"
        ).at("2026-11-04 09:00", state="Closed" if i < 304 else "Active")
    return b


def config():
    return Config(
        timezone="Europe/Zurich",
        waiting_states=["Ready for QA"],
        teams={"ALFA": {"count": "stories"}, "BETA": {"count": "tasks"}},
    )


def teams(notes=None):
    data = board().cache()
    sprint = next(s for s in data["iterations"] if s["name"] == "Sprint 29")
    results = [team_sprint(data, config(), PEOPLE, t, sprint, notes or Notes(), {}) for t in ("ALFA", "BETA")]
    for result in results:
        result["actions"] = team_actions(
            data, config(), PEOPLE, result["team"], result, None, date(2026, 11, 13), {}
        )
    return results


NOTES = Notes(
    summary={"ALFA": "Upload is live."},
    note={"ALFA": "Ivan Holt covered support."},
    exec_note={"ALFA": "QA environment was down."},
    status={"BETA": ("AMBER", "PO asked to hold the release")},
)


def text(page):
    page = re.sub(r"(?s)<(style|title)>.*?</\1>", " ", page)
    return " ".join(re.sub(r"<[^>]+>", " ", page).split())


class ManagerWindowTest(unittest.TestCase):
    def team(self):
        t = teams()[0]
        t.update(
            windows={"now": ["S40", "S41", "S42"], "before": ["S37", "S38", "S39"]},
            build={"median": 3.54, "n": 25, "before": 5.5},
            throughput={"per10": 3.4, "before": 4.2},
            first_pass={"scored": 25, "returned": [1], "share": 0.96, "tracked": True, "before": 0.92},
            flow={
                "total": 12,
                "building": 35,
                "waiting": 50,
                "other": 15,
                "states": ["Ready for QA", "PO Review"],
            },
        )
        t["trend"] = [dict(t["trend"][0], build=v, per10=v) for v in (None, 3.54)]
        return t

    def page(self, t=None):
        return render_manager("Sprint 29", [self.team() if t is None else t], GEN)

    def test_manager_figures_order_and_labels(self):
        page = self.page()
        labels = re.findall(r'<span class="l">(.*?)</span>', page)
        self.assertEqual(
            labels,
            [
                "planned work items delivered",
                "work items delivered in total",
                "work items per 10 person-days (last 3 sprints)",
                "build time, median (last 3 sprints)",
                "first-time pass (last 3 sprints, 1 of 25 returned)",
            ],
        )
        self.assertIn("3.5 <small>days</small>", page)

    def test_manager_drops_old_figures(self):
        page = self.page()
        for old in (
            "cycle time median",
            "of cycle time spent in waiting states",
            "completed after the sprint ended",
            ">work items carried over</span>",
            ">work items added / removed mid-sprint</span>",
        ):
            self.assertNotIn(old, page)

    def test_scope_line(self):
        t = self.team()
        t.update(added=[1, 2], removed=[3])
        self.assertIn(
            "Scope: +2 added / −1 removed mid-sprint · 1 planned work item carried over", self.page(t)
        )
        t["carry"] *= 2
        self.assertIn("2 planned work items carried over", self.page(t))
        t.update(added=[], removed=[], carry=[])
        self.assertIn(
            '<p class="note">Scope: no changes mid-sprint · no planned work items carried over</p>',
            self.page(t),
        )

    def test_arrows(self):
        page = self.page()
        self.assertIn('<span class="d up">▼ from 5.5 days vs sprints S37–S39</span>', page)
        self.assertIn('<span class="d down">▼ from 4.2 vs sprints S37–S39</span>', page)
        self.assertIn('<span class="d up">▲ from 92% vs sprints S37–S39</span>', page)
        t = self.team()
        t["build"]["before"] = 3.51
        t["throughput"]["before"] = None
        t["first_pass"]["before"] = 0.961
        self.assertNotIn('class="d ', self.page(t))
        t["first_pass"].update(tracked=False, before=0.8)
        self.assertNotIn('class="d ', self.page(t))
        t["windows"]["before"] = ["S39"]
        t["build"].update(median=6, before=5.5)
        self.assertIn('<span class="d down">▲ from 5.5 days vs sprints S39</span>', self.page(t))
        t["build"]["median"] = None
        t["throughput"]["per10"] = None
        self.assertNotIn('class="d ', self.page(t))

    def test_arrow_sprint_prefix_is_not_repeated(self):
        for first, last in (("Sprint 30", "Sprint 32"), ("sPrInT 30", "sPrInT 32"), ("S37", "S39")):
            with self.subTest(first=first):
                t = self.team()
                t["windows"]["before"] = [first, last]
                comparison = ("sprints " if first == "S37" else "") + first + "–" + last
                self.assertIn("from 5.5 days vs " + comparison + "</span>", self.page(t))

    def test_manager_day_counts_use_singular_only_for_one(self):
        for value, days in ((0, "0 days"), (1, "1 day"), (2, "2 days")):
            with self.subTest(value=value):
                t = self.team()
                t["flow"] = dict(total=value, building=40, waiting=35, other=25, states=[])
                t["build"].update(median=value, before=1 if value != 1 else 2)
                page = self.page(t)
                self.assertIn(
                    days + " typical from start to done: 40% building · 35% waiting · 25% other", text(page)
                )
                figure = str(value) if value in (0, 1) else f"{value:.1f}"
                unit = "day" if value == 1 else "days"
                self.assertIn('<span class="n">' + figure + " <small>" + unit + "</small>", page)
                if value != 1:
                    self.assertIn("from 1 day vs sprints S37–S39", page)

    def test_manager_footnote_does_not_use_dm_trend_convention(self):
        from ledger.render import _footnote

        with mock.patch("ledger.render.TREND", None):
            footnote = _footnote(False)
        self.assertIn("Build time =", footnote)
        self.assertNotIn("Cycle time =", footnote)

    def test_where_the_time_goes(self):
        t = self.team()
        self.assertIn("Where the time goes (last 6 sprints)", self.page(t))
        self.assertIn(
            "12 days typical from start to done: 35% building · 50% waiting "
            "(Ready for QA, PO Review) · 15% other",
            text(self.page(t)),
        )
        for color in ("var(--accent)", "var(--warn)", "#B8C0CA"):
            self.assertIn("background:" + color, self.page(t))
        t["flow"]["states"] = []
        self.assertIn("50% waiting · 15% other", text(self.page(t)))
        t["flow"] = None
        self.assertNotIn("<h3>Where the time goes", self.page(t))

    def test_delivered_items_late_badge(self):
        t = self.team()
        t["delivered_items"] = [dict(t["delivered_items"][0], id=i, late=i < 2) for i in range(9)]
        page = self.page(t)
        self.assertIn("Delivered items (9, 2 late)", page)
        self.assertEqual(page.count('<span class="chip amber">Late</span>'), 2)
        self.assertIn("<th>Late</th>", page)
        t["delivered_items"] = [dict(d, late=False) for d in t["delivered_items"][:3]]
        page = self.page(t)
        self.assertIn("Delivered items (3)", page)
        self.assertNotIn("<th>Late</th>", page)

    def test_trend_rows(self):
        page = self.page()
        self.assertIn("<td>Work items per 10 PD</td><td>–</td><td>3.5</td>", page)
        self.assertIn("<td>Build time median (days)</td><td>–</td><td>3.5</td>", page)
        self.assertNotIn("Cycle time P85", page)

    def test_dm_unchanged_by_build_time(self):
        page = render_dm("Sprint 29", [self.team()], GEN)
        for label in ("build time", "per 10 person-days", "Where the time goes"):
            self.assertNotIn(label, page)

    def test_manager_footnote_definitions(self):
        page = text(self.page())
        for definition in (
            "Arrows",
            "Work items per 10 person-days =",
            "Build time =",
            "pooled",
            "Where the time goes",
            "Late =",
            "own past",
        ):
            self.assertIn(definition, page)
        self.assertNotIn("Cycle time =", page)
        self.assertIn("Parts are whole percentages of that time, with the remainder in other.", page)

    def test_custom_metrics_subset_and_order(self):
        t = self.team()
        t["manager_metrics"] = ["build_time", "planned", "delivered"]
        page = self.page(t)
        labels = re.findall(r'<span class="l">(.*?)</span>', page)
        self.assertEqual(
            labels,
            [
                "build time, median (last 3 sprints)",
                "planned work items delivered",
                "work items delivered in total",
            ],
        )
        self.assertIn('<div class="fig key"><span class="n">', page)
        trend_html = page.split("<summary>Delivery stats per sprint (last 2 sprints)</summary>")[1].split(
            "</details>"
        )[0]
        row_labels = re.findall(r"<tr><td>([^<]+)</td>", trend_html)
        self.assertEqual(
            row_labels,
            [
                "Build time median (days)",
                "Planned work items delivered",
                "Work items delivered in total",
            ],
        )

    def test_show_flow_per_team_and_footnote(self):
        t1, t2 = self.team(), self.team()
        t1["team"], t2["team"] = "ALFA", "BETA"
        t1["show_flow"], t2["show_flow"] = False, True
        page = render_manager("Sprint 29", [t1, t2], GEN)
        articles = re.findall(r'(?s)<article class="squad">.*?</article>', page)
        self.assertNotIn("Where the time goes", articles[0])
        self.assertIn("Where the time goes (last 6 sprints)", articles[1])
        self.assertIn("Where the time goes =", page)

        t2["show_flow"] = False
        page_none = render_manager("Sprint 29", [t1, t2], GEN)
        self.assertNotIn("Where the time goes", page_none)

    def test_empty_metrics_omits_figs_and_trend(self):
        t = self.team()
        t["manager_metrics"] = []
        page = self.page(t)
        self.assertNotIn('<div class="figs">', page)
        self.assertNotIn("<summary>Last ", page)
        self.assertIn('<p class="note">Scope:', page)
        self.assertNotIn("Arrows compare", page)
        self.assertNotIn("Work items per 10 person-days =", page)
        self.assertNotIn("Build time =", page)
        self.assertNotIn("First-time pass =", page)

    def test_po_satisfaction_tile_bands_arrows_and_feedback(self):
        expected_bands = {
            5: ("Very satisfied", "green"),
            4: ("Satisfied", "green"),
            3: ("Mixed", "amber"),
            2: ("Dissatisfied", "red"),
            1: ("Very dissatisfied", "red"),
        }
        for score, (label, colour) in expected_bands.items():
            with self.subTest(score=score):
                t = self.team()
                t["manager_metrics"] = ["planned", "po_satisfaction"]
                t["po_satisfaction"] = {
                    "score": score,
                    "comment": "Smooth <release> & QA",
                    "before": None,
                    "before_sprint": None,
                }
                page = self.page(t)
                self.assertIn(f'<span class="n">{score} <small>/ 5</small></span>', page)
                self.assertIn('<span class="l">PO satisfaction</span>', page)
                self.assertIn(f'<span class="chip {colour}">{label}</span>', page)
                self.assertNotIn('class="d ', page)
                self.assertIn('<p class="note">PO feedback: Smooth &lt;release&gt; &amp; QA</p>', page)
                self.assertLess(page.index("Scope:"), page.index("PO feedback:"))
                self.assertNotIn(
                    "PO feedback:", page.split('<div class="figs">')[1].split('<p class="note">Scope:')[0]
                )

        # Up arrow (including 2 -> 3 amber improvement), down arrow, equal no arrow
        t = self.team()
        t["manager_metrics"] = ["po_satisfaction"]
        t["po_satisfaction"] = {"score": 3, "comment": "", "before": 2, "before_sprint": "Sprint 28"}
        self.assertIn('<span class="d up">▲ from 2 vs Sprint 28</span>', self.page(t))
        self.assertNotIn("PO feedback:", self.page(t))

        t["po_satisfaction"] = {"score": 2, "comment": "", "before": 4, "before_sprint": "Sprint 28"}
        self.assertIn('<span class="d down">▼ from 4 vs Sprint 28</span>', self.page(t))

        t["po_satisfaction"] = {"score": 4, "comment": "", "before": 4, "before_sprint": "Sprint 28"}
        self.assertNotIn('class="d ', self.page(t))

    def test_po_satisfaction_unrated_and_disabled(self):
        t = self.team()
        t["manager_metrics"] = ["planned", "po_satisfaction"]
        t["po_satisfaction"] = None
        t["trend"] = [dict(t["trend"][0], po_satisfaction=4), dict(t["trend"][1], po_satisfaction=None)]
        page = self.page(t)
        self.assertIn('<span class="n">–</span><span class="l">PO satisfaction</span>', page)
        self.assertIn("<td>PO satisfaction</td><td>4/5</td><td>–</td>", page)
        self.assertIn("PO satisfaction = product owner's 1–5 sprint rating", page)

        # Disabled hides tile, trend row, PO feedback, and footnote even when data exists
        t["manager_metrics"] = ["planned"]
        t["po_satisfaction"] = {
            "score": 5,
            "comment": "Great sprint",
            "before": 4,
            "before_sprint": "Sprint 28",
        }
        page_off = self.page(t)
        self.assertNotIn("PO satisfaction", page_off)
        self.assertNotIn("PO feedback:", page_off)
        self.assertNotIn("Great sprint", page_off)
        self.assertIn("Figures are recomputed", page_off)
        self.assertNotIn("Board figures are recomputed", page_off)

    def test_dynamic_footnote_canonical_order_and_arrow_wording(self):
        from ledger.render import _footnote

        cases = [
            (["throughput"], "green means better (higher throughput), red worse", "Arrows compare"),
            (["build_time"], "green means better (lower build time), red worse", "Arrows compare"),
            (["first_pass"], "green means better (higher first-time pass), red worse", "Arrows compare"),
            (
                ["throughput", "build_time"],
                "green means better (higher throughput, lower build time), red worse",
                "Arrows compare",
            ),
            (
                ["first_pass", "build_time"],
                "green means better (higher first-time pass, lower build time), red worse",
                "Arrows compare",
            ),
            (
                ["throughput", "first_pass"],
                "green means better (higher throughput or first-time pass), red worse",
                "Arrows compare",
            ),
            (
                ["throughput", "build_time", "first_pass"],
                "green means better (higher throughput or first-time pass, lower build time), red worse",
                "Arrows compare",
            ),
            (
                ["build_time", "po_satisfaction"],
                "green means better (lower build time), red worse",
                "Delivery-metric arrows compare",
            ),
        ]
        for metrics, better, arrow_prefix in cases:
            with self.subTest(metrics=metrics):
                foot = _footnote(False, [{"manager_metrics": metrics, "show_flow": False}])
                self.assertIn(better, foot)
                self.assertIn(arrow_prefix, foot)
        # Canonical order regardless of reversed team metric order
        foot = _footnote(
            False,
            [
                {
                    "manager_metrics": ["po_satisfaction", "first_pass", "build_time", "throughput"],
                    "show_flow": True,
                }
            ],
        )
        order = [
            "<b>Method.</b>",
            "Work items per 10 person-days =",
            "Build time =",
            "First-time pass =",
            "Delivery-metric arrows compare",
            "Where the time goes =",
            "PO satisfaction =",
            "On Hold =",
            "Board figures are recomputed",
        ]
        positions = [foot.index(s) for s in order]
        self.assertEqual(positions, sorted(positions))
        # Only po_satisfaction enabled -> no Delivery-metric arrows, still PO definition and Board figures
        foot_po_only = _footnote(
            False, [{"manager_metrics": ["planned", "po_satisfaction"], "show_flow": False}]
        )
        self.assertNotIn("compare the last 3 sprints", foot_po_only)
        self.assertIn("PO satisfaction =", foot_po_only)
        self.assertIn("Board figures are recomputed", foot_po_only)

    def test_default_config_byte_for_byte_identical_and_dm_unchanged(self):
        t_default = self.team()
        t_explicit = dict(
            t_default,
            manager_metrics=["planned", "delivered", "throughput", "build_time", "first_pass"],
            show_flow=True,
        )
        self.assertEqual(
            render_manager("Sprint 29", [t_default], GEN), render_manager("Sprint 29", [t_explicit], GEN)
        )
        # No-flow and all-missing cases also match default footnote identically
        t_noflow = dict(t_default, flow=None)
        t_noflow_exp = dict(t_explicit, flow=None)
        self.assertEqual(
            render_manager("Sprint 29", [t_noflow], GEN), render_manager("Sprint 29", [t_noflow_exp], GEN)
        )
        missing_default = [{"team": "GAMMA", "missing": "No sprint."}]
        missing_explicit = [
            {
                "team": "GAMMA",
                "missing": "No sprint.",
                "manager_metrics": ["planned", "delivered", "throughput", "build_time", "first_pass"],
                "show_flow": True,
            }
        ]
        self.assertEqual(
            render_manager("Sprint 29", missing_default, GEN),
            render_manager("Sprint 29", missing_explicit, GEN),
        )
        # dm_.html is byte-for-byte unchanged regardless of manager_metrics / show_flow
        t_custom = dict(
            t_default,
            manager_metrics=["po_satisfaction"],
            show_flow=False,
            po_satisfaction={"score": 5, "comment": "Great", "before": 4, "before_sprint": "Sprint 28"},
        )
        self.assertEqual(render_dm("Sprint 29", [t_default], GEN), render_dm("Sprint 29", [t_custom], GEN))


class RenderTest(unittest.TestCase):
    def first_pass_team(self, scored=25, returned=None, share=0.92, tracked=True):
        t = teams()[0]
        t["first_pass"] = {
            "scored": scored,
            "returned": [1, 2] if returned is None else returned,
            "share": share,
            "tracked": tracked,
            "before": None,
        }
        return t

    def test_first_pass_figure_tracked(self):
        for share, percent in ((0.92, "92%"), (0.926, "93%")):
            with self.subTest(share=share):
                page = render_manager("Sprint 29", [self.first_pass_team(share=share)], GEN)
                self.assertIn(
                    '<span class="n">' + percent + "</span>"
                    '<span class="l">first-time pass (last 3 sprints, 2 of 25 returned)</span>',
                    page,
                )

    def test_first_pass_figure_without_returns_shows_zero_count(self):
        page = render_manager("Sprint 29", [self.first_pass_team(share=1.0, returned=[])], GEN)
        self.assertIn(
            '<span class="n">100%</span><span class="l">first-time pass (last 3 sprints, 0 of 25 returned)</span>',
            page,
        )

    def test_first_pass_not_tracked(self):
        page = render_manager(
            "Sprint 29", [self.first_pass_team(scored=3, returned=[], share=1.0, tracked=False)], GEN
        )
        self.assertIn(
            '<span class="n">not tracked</span><span class="l">first-time pass: '
            f"too few items went through a test state in the last {TREND} sprints</span>",
            page,
        )
        figures = re.findall(r'<div class="fig[^"]*">(.*?)</div>', page)
        self.assertTrue(any("first-time pass" in f for f in figures))
        self.assertFalse(any("100%" in f for f in figures if "first-time pass" in f))

    def test_first_pass_nothing_delivered(self):
        for tracked in (True, False):
            with self.subTest(tracked=tracked):
                page = render_manager(
                    "Sprint 29",
                    [self.first_pass_team(scored=0, returned=[], share=None, tracked=tracked)],
                    GEN,
                )
                self.assertIn('<span class="n">–</span><span class="l">first-time pass</span>', page)

    def test_trend_row_first_pass(self):
        t = self.first_pass_team()
        t["trend"] = [dict(t["trend"][0], first_pass=share) for share in (0.5, None, 1.0)]
        for tracked, cells in (
            (True, "<td>50%</td><td>–</td><td>100%</td>"),
            (False, "<td>not tracked</td><td>–</td><td>not tracked</td>"),
        ):
            t["first_pass"]["tracked"] = tracked
            for render in (render_manager,):
                with self.subTest(tracked=tracked, render=render.__name__):
                    page = render("Sprint 29", [t], GEN)
                    self.assertIn("<td>First-time pass</td>" + cells, page)

    def test_first_pass_figure_caps_percent_when_an_item_returned(self):
        for scored in (200, 1000):
            for render in (render_manager,):
                with self.subTest(scored=scored, render=render.__name__):
                    t = self.first_pass_team(scored=scored, returned=[1], share=(scored - 1) / scored)
                    page = render("Sprint 29", [t], GEN)
                    figure = re.search(
                        r'<span class="n">([^<]+)</span>'
                        rf'<span class="l">first-time pass \(last 3 sprints, 1 of {scored} returned\)</span>',
                        page,
                    )
                    self.assertIsNotNone(figure)
                    self.assertEqual(figure.group(1), "99%")

    def test_first_pass_trend_caps_nonperfect_share(self):
        t = self.first_pass_team()
        t["trend"] = [dict(t["trend"][0], first_pass=share) for share in (0.995, 0.999, 1.0)]
        for render in (render_manager,):
            with self.subTest(render=render.__name__):
                page = render("Sprint 29", [t], GEN)
                row = re.search(r"<td>First-time pass</td>(.*?)</tr>", page).group(1)
                self.assertEqual(row, "<td>99%</td><td>99%</td><td>100%</td>")

    def test_dm_page_has_no_team_figures(self):
        page = render_dm("Sprint 29", [self.first_pass_team()], GEN)
        self.assertNotIn("Team figures", page)
        self.assertNotIn('<div class="figs">', page)
        self.assertNotIn('<span class="l">first-time pass', page)
        person = render_person(PEOPLE[0], [], GEN)
        self.assertNotIn("first-time pass", person.lower())

    def test_footnote_defines_first_pass(self):
        sentence = (
            "First-time pass = share of delivered work items never sent back from a "
            "test state and never reopened, counted per team; “not tracked” means no "
            "item moved back and fewer than half of the delivered items went through a "
            f"test state in the last {TREND} sprints."
        )
        for render in (render_dm,):
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
        self.assertNotIn("Counts:", dm)
        self.assertIn("our convention, not an industry standard", dm)
        self.assertIn("waiting (in a waiting state or blocked)", dm)
        for doc in (page, dm):
            self.assertNotIn("on the customer", doc)
        stripped = re.sub(r"Method\..*", "", dm)
        for word in ("story", "task"):
            self.assertNotRegex(stripped, rf"(?i)\b{word}s?\b")

    def test_manual_status_is_dm_only_and_manager_shows_build_and_trend(self):
        page = text(render_dm("Sprint 29", teams(NOTES), GEN))
        self.assertIn(
            "BETA AMBER Last sprint: Sprint 29 (27 Oct – 7 Nov 2026) PO asked to hold the "
            "release (set by hand)",
            page,
        )
        page = text(render_manager("Sprint 29", teams(NOTES), GEN))
        self.assertNotIn("AMBER", page)
        self.assertIn("build time, median (last 3 sprints)", page)
        self.assertIn("Delivery stats per sprint (last 2 sprints) Sprint 28 Sprint 29", page)

    def test_dm_starts_with_internal_marker_and_notes_before_comparison(self):
        page = render_dm("Sprint 29", teams(NOTES), GEN)
        body = text(page[page.index("<body") :])
        self.assertTrue(
            body.startswith("Internal: delivery manager and team leads only. Not for the customer.")
        )
        self.assertLess(body.index("Ivan Holt covered support."), body.index("Last sprint:"))
        self.assertLess(body.index("Last sprint:"), body.index("Our engineers"))
        self.assertLess(body.index("Our engineers"), body.index("Person Work item Sprint"))
        self.assertNotIn("Team figures", body)
        self.assertIn("Use this as context, not as a ranking.", body)
        self.assertIn("Maya Brook", body)
        self.assertNotIn("Needs attention", body)
        self.assertNotIn("<h3>People</h3>", page)
        self.assertNotIn("2 of 3 planned work items delivered", body)

    def test_missing_sprint_is_listed(self):
        missing = [{"team": "GAMMA", "missing": "No sprint named Sprint 29."}]
        for render in (render_manager, render_dm):
            self.assertIn(
                '<article class="squad"><div class="top"><h2>GAMMA</h2></div>'
                '<p class="note">No sprint named Sprint 29.</p></article>',
                render("Sprint 29", missing, GEN),
            )

    def card(self, page, team):
        """The article for `team` in `page`."""
        return next(
            a
            for a in re.findall(r'(?s)<article class="squad">.*?</article>', page)
            if re.search(rf"<h2>{re.escape(team)}(</h2>| <span)", a)
        )

    def test_manager_card_layout(self):
        card = self.card(render_manager("Sprint 29", teams(NOTES), GEN), "ALFA")
        order = [
            "<h2>ALFA</h2>",
            "Sprint 29 (27 Oct – 7 Nov 2026)",
            "<h3>Delivery highlights</h3>",
            "Upload is live.",
            "QA environment was down.",
            "<h3>Delivery stats</h3>",
            "1 planned work item carried over",
            "<summary>Delivered items (2)</summary>",
            "<summary>Planned items carried over (1)</summary>",
            "<summary>Delivery stats per sprint (last 2 sprints)</summary>",
        ]
        self.assertEqual([card.index(o) for o in order], sorted(card.index(o) for o in order))
        self.assertIn(
            '<span class="cap">Sprint 29 (27 Oct – 7 Nov 2026)<br>2 engineers (18 PD capacity)</span>', card
        )
        for absent in (
            "Counts:",
            "GREEN",
            "AMBER",
            "RED",
            "Worked on",
            "worked on",
            "Age",
            "Now in",
            "Agreed with PO",
        ):
            self.assertNotIn(absent, card)
        self.assertIn("<th>Times carried</th><th>At sprint end</th><th>On Hold</th><th>Reason</th>", card)
        self.assertIn("<td>Planned</td>", card)

    def test_manager_card_without_summary_has_no_highlights(self):
        self.assertNotIn("Delivery highlights", render_manager("Sprint 29", teams(), GEN))

    def test_manager_card_exec_note_without_summary_keeps_highlights(self):
        card = self.card(
            render_manager("Sprint 29", teams(Notes(exec_note={"ALFA": "Pilot live"})), GEN), "ALFA"
        )
        self.assertIn("<h3>Delivery highlights</h3><p>Pilot live</p>", card)

    def test_manager_card_multiline_summary_renders_as_bullets(self):
        notes = Notes(summary={"ALFA": "Delivered upload\nIn progress: retries"})
        card = self.card(render_manager("Sprint 29", teams(notes), GEN), "ALFA")
        self.assertIn("<ul><li>Delivered upload</li><li>In progress: retries</li></ul>", card)

    def test_manager_card_single_line_summary_unchanged(self):
        card = self.card(render_manager("Sprint 29", teams(NOTES), GEN), "ALFA")
        self.assertIn("<h3>Delivery highlights</h3><p>Upload is live.</p>", card)

    def test_dm_card_status_first_and_no_team_figures(self):
        card = self.card(render_dm("Sprint 29", teams(NOTES), GEN), "BETA")
        order = [
            '<h2>BETA <span class="chip AMBER">AMBER</span></h2>',
            "<h3>Last sprint: Sprint 29 (27 Oct – 7 Nov 2026)</h3>",
            "<p>PO asked to hold the release (set by hand)</p>",
            "<th>Person</th>",
        ]
        self.assertEqual([card.index(o) for o in order], sorted(card.index(o) for o in order))
        for absent in (
            "The team worked on",
            "Upload is live.",
            '<div class="figs">',
            "Carried over",
            "Delivery stats per sprint",
        ):
            self.assertNotIn(absent, card)

    def test_carry_and_person_show_days_on_hold(self):
        t = teams()
        page_without_hold = render_manager("Sprint 29", t, GEN)
        self.assertNotIn("Agreed with PO", page_without_hold)
        self.assertIn("<th>At sprint end</th><th>On Hold</th><th>Reason</th>", page_without_hold)
        self.assertIn("<td>Active</td><td>–</td><td>–</td></tr>", page_without_hold)
        t[0]["carry"][0]["held"] = 3
        t[0]["carry"][0]["sprints"] = 2
        t[0]["carry"].append(
            dict(
                t[0]["carry"][0],
                id=109,
                title="Sibling carry",
                state="Ready for QA",
                waiting=True,
                held=0,
                reason="API delay",
            )
        )
        t[0]["people"][0]["held"] = [[103, 3], [7, 1]]
        page_with_hold = render_manager("Sprint 29", t, GEN)
        self.assertIn("<summary>Planned items carried over (2) (1 On Hold)</summary>", page_with_hold)
        self.assertIn(
            "Carried over = planned work items still open on this or a later sprint at sprint end; "
            "items added mid-sprint are excluded.",
            page_with_hold,
        )
        alfa_card = self.card(page_with_hold, "ALFA")
        beta_card = self.card(page_with_hold, "BETA")
        self.assertIn("<th>At sprint end</th><th>On Hold</th><th>Reason</th>", alfa_card)
        self.assertIn("<td>2</td><td>Active</td><td>3 d</td><td>–</td>", alfa_card)
        self.assertIn("<td>Ready for QA</td><td>–</td><td>API delay</td>", alfa_card)
        self.assertNotIn("chip wait", alfa_card)
        self.assertNotIn('class="hot"', alfa_card)
        self.assertIn("<th>At sprint end</th><th>On Hold</th><th>Reason</th>", beta_card)
        self.assertNotIn("Agreed with PO", page_with_hold)
        dm_page = render_dm("Sprint 29", t, GEN)
        self.assertIn("(or no carry-over when P85 is unavailable)", dm_page)
        self.assertIn("unfinished planned item still on a sprint at sprint end", dm_page)
        self.assertNotIn("PO agreement", dm_page)
        for render in (render_manager, render_dm):
            self.assertIn(
                "On Hold = days the work item spent in a hold state or tagged Blocked",
                render("Sprint 29", t, GEN),
            )

    def test_manager_empty_carryover_has_visible_panel(self):
        t = teams()
        t[1]["carry"] = []
        page = render_manager("Sprint 29", t, GEN)
        self.assertIn(".carry-empty{", page)
        card = self.card(page, "BETA")
        self.assertIn(
            '<div class="carry-empty"><h3>Planned items carried over (0)</h3><p class="note">No '
            "planned items carried over.</p></div>",
            card,
        )
        self.assertNotIn("<summary>Planned items carried over", card)

    def test_work_items_link_to_azure_devops(self):
        page = link_ids(
            render_manager("Sprint 29", teams(NOTES), GEN), "https://dev.azure.com/acme/", "My Project"
        )
        self.assertIn('<a href="https://dev.azure.com/acme/My%20Project/_workitems/edit/101">#101</a>', page)
        self.assertNotIn("<a ", page[: page.index("</style>")])
        self.assertEqual(link_ids("<style>x</style><p>#1</p>", "", "P"), "<style>x</style><p>#1</p>")
        self.assertEqual(
            link_ids("<style></style><p>&#39;#2</p>", "https://o", "P"),
            '<style></style><p>&#39;<a href="https://o/P/_workitems/edit/2">#2</a></p>',
        )

    def test_titles_are_escaped(self):
        for render in (render_manager, render_dm):
            page = render("Sprint 29", teams(), GEN)
            self.assertNotIn("<b>x</b>", page)
            self.assertIn("Retry &amp; reconcile", page)
        t = teams()
        t[0]["worked_on"] = [["<b>x</b>", 1]]
        t[0]["carry"][0]["reason"] = "<script>x</script> & delay"
        escaped_manager = render_manager("Sprint 29", t, GEN)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", escaped_manager)
        self.assertIn("&lt;script&gt;x&lt;/script&gt; &amp; delay", escaped_manager)
        self.assertNotIn("<script>x</script>", escaped_manager)

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
        rows = [
            {"sprint": "Sprint 28", "available": None},
            {
                "sprint": "Sprint 29",
                "available": 9.5,
                "nominal": 10,
                "delivered": 2,
                "in_progress": 1,
                "median": 4,
                "items": [101, 102],
            },
        ]
        self.assertNotIn("prefers-color-scheme", render_person(PEOPLE[0], rows, GEN))
        page = text(render_person(PEOPLE[0], rows, GEN))
        self.assertIn("Private: delivery manager only", page)
        self.assertIn("Sprint 29 9.5 / 10 2 1 4 d #101 #102", page)
        self.assertIn("not on the team", page)

    def test_action_table_rowspans_order_badges_answers_and_escaping(self):
        t = teams(NOTES)[0]

        def row(i, person, level, badge, open=True, **extra):
            return dict(
                id=i,
                person=person,
                title="<img src=x onerror=bad> & title",
                sprint="Now",
                sprint_path=S29,
                level=level,
                badge=badge,
                kinds=[],
                problem="<problem>",
                ask="suggested action",
                age=12,
                open=open,
                carry=False,
                **extra,
            )

        t["actions"] = dict(
            current=dict(
                sprint=dict(name="Current <sprint>"),
                day=4,
                days=10,
                planned=[1, 2, 3],
                delivered_planned=[1],
                added=[4],
                removed=[2],
                held=[3],
                waiting=[4, 5],
            ),
            people=[
                dict(name="Zed <lead>", available=8.5, nominal=10, onboarding=True),
                dict(name="Amy", available=10, nominal=10, onboarding=False),
                dict(name="Bea", available=9, nominal=10, onboarding=False),
                dict(name="Cal", available=10, nominal=10, onboarding=False),
            ],
            rows=[
                row(9, "Amy", "red", "Stuck", handled=True, answer="<answer> & fixed"),
                row(4, "Zed <lead>", "green", "Done", open=False),
                row(3, "Zed <lead>", "green", "Done", open=False),
                row(8, "Zed <lead>", "green", "On track"),
                row(7, "Zed <lead>", "amber", "Waiting", worse=True, answer="chased"),
                row(6, "Zed <lead>", "red", "Stuck"),
                row(5, "Bea", "grey", "Check"),
            ],
        )
        page = link_ids(render_dm("Sprint 29", [t], GEN), "https://dev.azure.com/acme", "Proj")
        before_figures = page.split('<div class="foot">')[0]
        self.assertIn(
            "<th>Person</th><th>Work item</th><th>Sprint</th><th>Signal</th>"
            "<th>Problem</th><th>Action</th><th>Age</th>",
            before_figures,
        )
        self.assertIn('<td rowspan="4">Zed &lt;lead&gt;', before_figures)
        self.assertIn("8.5 / 10 d available", text(before_figures))
        self.assertIn("onboarding", before_figures)
        self.assertIn("nothing open", before_figures)
        self.assertIn(
            "Current &lt;sprint&gt; · day 4 of 10 working days · planned done 1 of 3"
            " · +1 added / −1 removed · 1 On Hold · 2 waiting",
            before_figures,
        )
        positions = [before_figures.index(f">#{i}</a>") for i in (6, 7, 8, 4, 5, 9)]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('title="worse since your answer">Waiting ↑</span>', before_figures)
        self.assertIn('class="chip grey">Handled</span>', before_figures)
        self.assertIn('class="chip green">Done (2)</span>', before_figures)
        self.assertIn(">#3</a> &lt;img src=x onerror=bad&gt; &amp; title<br><a", before_figures)
        self.assertIn('class="chip track">On track</span>', before_figures)
        self.assertIn("&lt;answer&gt; &amp; fixed", before_figures)
        self.assertNotIn("<img", page)
        self.assertNotIn("<problem>", page)
        self.assertIn("&lt;img src=x onerror=bad&gt; &amp; title", page)
        self.assertIn("<td>12 d</td>", before_figures)

    def test_merged_action_asks_are_distinct_sentences_and_answer_is_unchanged(self):
        from tests.test_actions import PEOPLE as roster
        from tests.test_actions import config as action_config
        from tests.test_actions import merged_wording_board

        b = merged_wording_board()
        t = team_sprint(b.cache(), action_config(), roster, "ALFA", b.sprints[-2], Notes(), {})
        t["actions"] = team_actions(
            b.cache(), action_config(), roster, "ALFA", t, b.sprints[-1], date(2026, 11, 10), {}
        )
        row = next(r for r in t["actions"]["rows"] if r["id"] == 7101)
        expected = (
            "Raise it with the PO. Ask what is blocking it. Record why it was carried over in notes.md."
        )
        self.assertEqual(row["ask"], expected)
        page = render_dm("Old", [t], GEN).split('<div class="foot">')[0]
        self.assertIn("<td>" + expected + "</td>", page)
        self.assertNotIn(".;", page)
        answer = "DM text.; keep it exactly.  Next step"
        row["answer"] = answer
        page = render_dm("Old", [t], GEN).split('<div class="foot">')[0]
        self.assertIn("<td>" + answer + "</td>", page)
        self.assertNotIn("<td>" + expected + "</td>", page)

    def test_handled_and_ok_people_share_one_name_sorted_group(self):
        t = teams()[0]
        t["actions"]["people"] = [
            dict(name=n, available=10, nominal=10, onboarding=False) for n in ("Zoe", "Amy")
        ]
        t["actions"]["rows"] = [
            dict(
                id=7,
                person="Zoe",
                title="item",
                sprint="Now",
                level="red",
                badge="Stuck",
                problem="stuck",
                ask="ask",
                age=3,
                open=True,
                handled=True,
                answer="fixed",
            )
        ]
        page = render_dm("Sprint 29", [t], GEN).split('<div class="foot">')[0]
        self.assertLess(page.index(">Amy<br>"), page.index(">Zoe<br>"))

    def test_suggestions_and_problems_use_markdown_notes(self):
        page = render_dm("Sprint 29", teams(), GEN)
        self.assertNotIn("notes.txt", page)
        self.assertIn("notes.md", page)

    def test_manager_equivalent_markdown_notes_keep_expected_layout(self):
        notes = parse_notes(
            "# ALFA\n## Summary\nUpload is live.\n## Exec note\n"
            "QA environment was down.\n## Note\nIvan Holt covered support.\n"
            "# BETA\n## Status\nAMBER - PO asked to hold the release\n"
        )
        self.assertEqual(
            render_manager("Sprint 29", teams(notes), GEN), render_manager("Sprint 29", teams(NOTES), GEN)
        )

    def test_folder_name(self):
        self.assertEqual(folder_name('A/B\\C:D*E?F"G<H>I|J'), "A-B-C-D-E-F-G-H-I-J")


def load_vac(d):
    with open(os.path.join(d, "config.json"), encoding="utf-8") as f:
        return json.load(f)["vacations"]


class RunReportTest(unittest.TestCase):
    def test_warning_without_test_states(self):
        warning = "warning: no test states in config.json; first-time pass counts reopens only"
        self.edit(
            "config.json",
            lambda s: json.dumps({k: v for k, v in json.loads(s).items() if k != "test_states"}),
        )
        for states in (None, [], ["Ready for QA"]):
            with self.subTest(states=states):
                if states is not None:
                    self.edit(
                        "config.json", lambda s, st=states: json.dumps(dict(json.loads(s), test_states=st))
                    )
                code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
                self.assertEqual(code, 0, out)
                self.assertEqual(out.splitlines().count(warning), 0 if states else 1, out)

    def test_no_test_states_warning_suppressed_when_first_pass_disabled(self):
        warning = "warning: no test states in config.json; first-time pass counts reopens only"
        self.edit(
            "config.json",
            lambda s: json.dumps(
                dict(json.loads(s), test_states=[], manager_metrics=["planned", "delivered"])
            ),
        )
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertNotIn(warning, out)
        # Enabling first_pass for even one team restores the warning
        self.edit(
            "config.json",
            lambda s: json.dumps(
                dict(
                    json.loads(s),
                    teams={
                        "ALFA": {"count": "stories", "metrics": ["planned", "first_pass"]},
                        "BETA": {"count": "tasks"},
                    },
                )
            ),
        )
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines().count(warning), 1, out)

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
        code = cli.main(["--dir", self.d, *list(argv)], out=out)
        return code, out.getvalue()

    def read_report(self, filename, folder="sprint-29"):
        with open(os.path.join(self.d, "reports", folder, filename), encoding="utf-8") as f:
            return f.read()

    def write_notes(self, content, folder="sprint-29"):
        os.makedirs(os.path.join(self.d, "reports", folder), exist_ok=True)
        with open(os.path.join(self.d, "reports", folder, "notes.md"), "w", encoding="utf-8") as f:
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
        manager = self.read_report("manager_2026-11-05.html", "sprint-28")
        dm = self.read_report("dm_2026-11-05.html", "sprint-28")
        self.assertIn("Sprint 28 delivery summary", manager)
        self.assertNotIn("day 7 of 9", manager)
        self.assertIn("Sprint 29 · day 7 of 9 working days · planned done 2 of 3", text(dm))
        self.assertIn("<h2>BETA</h2>", dm)  # BETA has no closed sprint, so no status chip
        self.assertIn("<h3>Current sprint</h3>", dm)
        beta_actions = dm.split("<h2>BETA</h2>")[1].split('<div class="foot">')[0]
        self.assertIn("No closed sprint available for comparison.", beta_actions)
        self.assertNotIn("No other delivered work items", beta_actions)
        self.assertIn("Lena Ford", dm)
        self.assertIn("<td>Now</td>", dm)
        self.assertIn("Board data as of 2026-11-13 01:00 CET", manager)
        self.assertIn("BETA action:", out)

    def test_running_rerun_ignores_later_same_day_closure_and_reassignment(self):
        self.run_cli("report", "--offline", "--today", "2026-11-05")
        self.edit(
            "reports/sprint-28/notes.md", lambda t: t.replace("|  |  |", "| chased: after standup |  |")
        )
        self.run_cli("report", "--offline", "--today", "2026-11-05")
        first = self.read_report("dm_2026-11-05.html", "sprint-28").split('<div class="foot">')[0]
        notes = self.read_report("notes.md", "sprint-28")
        state = self.read_report(".notes-state.json", "sprint-28")

        def close(t):
            data = json.loads(t)
            row = dict(next(r for r in data["revisions"] if r["id"] == 103))
            row.update(
                rev=3, changed="2026-11-05T10:00:00Z", state="Closed", category="Completed", assigned=PO
            )
            data["revisions"].append(row)
            return json.dumps(data)

        self.edit(CACHE_FILE, close)
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-05")
        self.assertEqual(code, 0, out)
        second = self.read_report("dm_2026-11-05.html", "sprint-28").split('<div class="foot">')[0]

        # The closed sprint's status is recomputed from the full history; the running rows are not.
        def same(page):
            page = re.sub(r".*generated .*\n", "", page)
            page = re.sub(r' <span class="chip (GREEN|AMBER|RED)">\w+</span>', "", page)
            return re.sub(r"(<h3>Last sprint[^<]*</h3>\n)<p>.*?</p>\n", r"\1", page)

        self.assertEqual(same(first), same(second))
        self.assertEqual(notes, self.read_report("notes.md", "sprint-28"))
        self.assertEqual(state, self.read_report(".notes-state.json", "sprint-28"))
        self.assertIn("Handled", second)

    def test_duplicate_item_ids_use_each_teams_carry_reason(self):
        b = Board().sprint("Sprint 29", "2026-10-27", "2026-11-07")
        b.item(10).at("2026-10-27 09:00", iteration=S29, assigned=MAYA, state="Active")
        b.item(11, "Task", parent=10).at("2026-10-27 09:00", iteration=S29, assigned=LENA, state="Active")
        self.edit(CACHE_FILE, lambda t: json.dumps(b.cache()))
        self.edit("config.json", lambda t: t.replace('"tasks"', '"stories"'))
        self.write_notes(
            "# ALFA\n## Actions\n| Maya Brook | #10 Item | carry | split in refinement | |\n"
            "# BETA\n## Actions\n| Lena Ford | #10 Item | carry | blocked on pricing API | |\n"
        )
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        page = self.read_report("manager_2026-11-13.html")
        alfa = next(a for a in re.findall(r"(?s)<article.*?</article>", page) if "<h2>ALFA</h2>" in a)
        beta = next(a for a in re.findall(r"(?s)<article.*?</article>", page) if "<h2>BETA</h2>" in a)
        self.assertIn("<td>split in refinement</td>", alfa)
        self.assertIn("<td>blocked on pricing API</td>", beta)
        self.assertNotIn("Agreed with PO", page)
        self.assertNotIn("carry-over without note: #10", out)

    def test_missing_closed_team_keeps_existing_note_and_running_answer(self):
        self.write_notes(
            "# BETA\n## Note\nKeep the running discussion.\n## Actions\n"
            "| Lena Ford | #304 Item | Waiting | chased: tomorrow | |\n",
            "sprint-28",
        )

        def block(t):
            data = json.loads(t)
            for row in data["revisions"]:
                if row["id"] == 304:
                    row["tags"] = ["Blocked"]
            return json.dumps(data)

        self.edit(CACHE_FILE, block)
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-05")
        self.assertEqual(code, 0, out)
        dm = self.read_report("dm_2026-11-05.html", "sprint-28")
        self.assertIn("Keep the running discussion.", dm)
        self.assertIn("chased: tomorrow", dm)
        self.assertIn("Handled", dm)
        self.assertIn("chased: tomorrow", self.read_report("notes.md", "sprint-28"))
        self.assertNotIn("BETA action:", out)

    def test_named_past_sprint_suppresses_running_actions(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-05", "--sprint", "Sprint 28")
        self.assertEqual(code, 0, out)
        dm = self.read_report("dm_2026-11-05.html", "sprint-28")
        self.assertNotIn("<td>Now</td>", dm)
        self.assertNotIn("working days · planned done", dm)
        self.assertNotIn("BETA action:", out)

    def test_legacy_notes_are_ignored(self):
        os.makedirs(os.path.join(self.d, "reports", "sprint-29"))
        with open(os.path.join(self.d, "reports", "sprint-29", "notes.txt"), "w") as f:
            f.write("ALFA summary: Legacy highlights.\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertNotIn("Legacy highlights.", self.read_report("manager_2026-11-13.html"))
        self.assertIn("notes.md", out)

    def test_carry_reason_refresh_does_not_rebase_or_loop_on_rerun(self):
        self.write_notes("# BETA\n## Actions\n| Lena Ford | #304 Item 304 | carry | split later | |\n")
        results = []
        for _ in range(3):
            code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
            self.assertEqual(code, 0, out)
            dm = self.read_report("dm_2026-11-13.html")
            action_card = dm.split('<div class="foot">')[0]
            beta = action_card.split("<h2>BETA")[1]
            self.assertIn('class="chip grey">Handled</span>', beta)
            self.assertNotIn("BETA action:", out)
            results.append((self.read_report("notes.md"), self.read_report(".notes-state.json")))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        self.assertIn("split later", results[0][0])

    def test_carry_reason_handled_and_copied_answer_cannot_supply_next_sprint_reason(self):
        b = Board().sprint("Sprint 29", "2026-10-27", "2026-11-07")
        carry = (
            b.item(10)
            .at("2026-10-27 09:00", iteration=S29, assigned=MAYA)
            .at("2026-11-06 09:00", state="Active")
        )
        b.item(11).at("2026-10-27 09:00", iteration=S29, assigned=IVAN, state="Active").at(
            "2026-11-04 09:00", state="Closed"
        )
        self.edit(CACHE_FILE, lambda t: json.dumps(b.cache()))
        self.write_notes("# ALFA\n## Actions\n| Maya Brook | #10 Item 10 | carry | split in refinement | |\n")
        results = []
        for _ in range(3):
            code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
            self.assertEqual(code, 0, out)
            dm = self.read_report("dm_2026-11-13.html")
            self.assertIn('class="chip grey">Handled</span>', dm.split('<div class="foot">')[0])
            self.assertNotIn("carry-over without note: #10", out)
            self.assertIn("<td>split in refinement</td>", self.read_report("manager_2026-11-13.html"))
            saved = parse_notes(self.read_report("notes.md"))
            self.assertTrue(
                any(
                    r["id"] == 10 and r["answer"] == "split in refinement"
                    for r in saved.teams["ALFA"]["Actions"]
                )
            )
            results.append((self.read_report("notes.md"), self.read_report(".notes-state.json")))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])
        b.sprint("Sprint 30", "2026-11-10", "2026-11-21")
        carry.at("2026-11-10 09:00", iteration="Proj\\Sprint 30")
        self.edit(CACHE_FILE, lambda t: json.dumps(b.cache()))
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-23")
        self.assertEqual(code, 0, out)
        dm = self.read_report("dm_2026-11-23.html", "sprint-30")
        self.assertIn("split in refinement", dm)
        self.assertIn('title="worse since your answer"', dm)
        manager = self.read_report("manager_2026-11-23.html", "sprint-30")
        self.assertNotIn("split in refinement", manager)
        self.assertIn("carry-over without note: #10", out)

    def test_all_note_warnings_are_printed_once(self):
        self.write_notes(
            "preamble\n# ALFA\n## Status\nBLUE - invalid\n## Actions\n"
            "| Ivan Holt | #103 Retry | carry | chased | nonsense |\n"
            "| broken | table |\n# UNKNOWN\n## Note\nkeep this\n"
        )
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        warnings = [line for line in out.splitlines() if line.startswith("warning: notes.md")]
        for part in ("preamble", "invalid Status", "broken", "UNKNOWN", "invalid Answered"):
            self.assertTrue(any(part in line for line in warnings), out)
        self.assertEqual(len(warnings), len(set(warnings)), out)
        self.assertIn("keep this", self.read_report("notes.md"))

    def test_console_actions_skip_answered_checks_and_ok_but_include_person_rows(self):
        self.write_notes("# ALFA\n## Actions\n| Ivan Holt | #103 Retry | carry | chased: tomorrow | |\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        lines = [line for line in out.splitlines() if " action: " in line]
        self.assertTrue(lines, out)
        self.assertFalse(any("#103" in line for line in lines), out)
        self.assertTrue(any(line.startswith("BETA action: Lena Ford #304 ") for line in lines), out)
        self.assertTrue(
            any(line.startswith("ALFA action: Ivan Holt Nothing delivered - ") for line in lines), out
        )
        self.assertFalse(any(" OK - " in line or " Check - " in line for line in lines))

    def test_report_offline_writes_two_files_and_rerun_is_identical(self):
        contents = []
        for _ in range(2):
            code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
            self.assertEqual(code, 0, out)
            folder = os.path.join(self.d, "reports", "sprint-29")
            paths = [os.path.join(folder, n) for n in ("manager_2026-11-13.html", "dm_2026-11-13.html")]
            for p in paths:
                self.assertIn(p, out)
            pages = []
            for p in paths:
                with open(p, encoding="utf-8") as f:
                    pages.append(re.sub(r".*generated .*\n", "", f.read()))
            contents.append(pages)
        self.assertEqual(contents[0], contents[1])
        self.assertTrue(os.path.exists(os.path.join(self.d, "reports", "sprint-29", "notes.md")))
        self.assertIn("2 engineers (16 PD capacity)", text(contents[0][0]))  # Maya: 2 days off
        self.assertIn("1 engineer (8 PD capacity)", text(contents[0][0]))  # alias for Lena

    def test_report_prints_sprint_notes_carryovers_and_delivered(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertIn("sprint: Sprint 29", lines)
        self.assertIn("notes: " + os.path.join(self.d, "reports", "sprint-29", "notes.md"), lines)
        self.assertIn("ALFA worked on: #101 Upload <b>x</b> (delivered)", lines)
        self.assertIn("ALFA worked on: #103 Retry & reconcile (in progress)", lines)
        self.assertFalse([line for line in lines if " delivered: #" in line])
        carry = [line for line in lines if line.startswith("ALFA carry-over without note: #")]
        self.assertTrue(carry, out)
        cid = re.match(r"ALFA carry-over without note: #(\d+)", carry[0]).group(1)
        with open(os.path.join(self.d, "reports", "sprint-29", "notes.md"), "w") as f:
            f.write(f"# ALFA\n## Actions\n| Ivan Holt | #{cid} Retry | carry | agreed: ok | |\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertNotIn(f"carry-over without note: #{cid} ", out)

    def test_report_prints_notes_warnings_and_reads_notes(self):
        os.makedirs(os.path.join(self.d, "reports", "sprint-28"))
        with open(os.path.join(self.d, "reports", "sprint-28", "notes.md"), "w") as f:
            f.write("hello there\n# ALFA\n## Summary\nRetries done.\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13", "--sprint", "Sprint 28")
        self.assertEqual(code, 0, out)
        self.assertIn("notes.md line 1 preserved: hello there", out)
        with open(os.path.join(self.d, "reports", "sprint-28", "manager_2026-11-13.html")) as f:
            page = f.read()
        self.assertIn("Retries done.", page)
        self.assertIn("No sprint named Sprint 28.", page)
        self.assertIn("BETA: No sprint named Sprint 28.", out)

    def test_report_folder_is_kebab_case_under_reports(self):
        self.assertEqual(report_folder("Sprints ended by 2026-11-07"), "sprints-ended-by-2026-11-07")
        self.assertEqual(report_folder("Sprint 29"), "sprint-29")
        self.assertEqual(report_folder(" PI 3 / Sprint_2 "), "pi-3-sprint-2")
        self.assertEqual(report_folder("***"), "report")

    def test_report_says_where_the_data_came_from(self):
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertRegex(out, r"(?m)^data: cached board from \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_notes_guide_is_written_and_never_read_back(self):
        self.run_cli("report", "--offline", "--today", "2026-11-13")
        text = self.read_report("notes.md")
        self.assertIn("<!-- guide: Edit this file, then run `bin/ledger report --offline`", text)
        self.assertEqual(
            text.count("## Summary\n<!-- guide: Manager report, Delivery highlights."), text.count("\n# ")
        )
        self.assertEqual(text.count("<!-- guide: "), 3 + 7 * text.count("\n# "))
        self.edit(
            "reports/sprint-29/notes.md",
            lambda t: t.replace("## Summary\n<!-- guide:", "## Summary\n- Shipped retries.\n<!-- guide:", 1),
        )
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertNotIn("preserved", out)
        again = self.read_report("notes.md")
        self.assertEqual(again.count("<!-- guide: "), text.count("<!-- guide: "))
        manager = self.read_report("manager_2026-11-13.html")
        self.assertIn("Shipped retries.", manager)
        self.assertNotIn("guide:", manager)
        self.assertNotIn("guide:", self.read_report("dm_2026-11-13.html"))

    def test_notes_carry_forward_from_old_top_level_folder(self):
        os.makedirs(os.path.join(self.d, "Sprint 28"))
        with open(os.path.join(self.d, "Sprint 28", "notes.md"), "w") as f:
            f.write(f"<!-- ledger notes: ALFA={S28} -->\n# ALFA\n## Note\nOld layout note.\n")
        self.run_cli("report", "--offline", "--today", "2026-11-07")
        self.assertIn("Old layout note.", self.read_report("notes.md", "sprint-28"))

    def test_default_sprint_is_last_ended(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-07")
        self.assertEqual(code, 0, out)
        self.assertIn(os.path.join("reports", "sprint-28", "manager_2026-11-07.html"), out)

    def test_default_takes_each_teams_last_ended_sprint_when_names_differ(self):
        def rename(t):
            data = json.loads(t)
            for it in data["iterations"]:
                if it["path"] == S29:
                    it["path"], it["name"], it["end"] = "Proj\\Pricing 29", "Pricing 29", "2026-11-06"
            for r in data["revisions"]:
                if r["area"] == "Proj\\Pricing" and r["iteration"] == S29:
                    r["iteration"] = "Proj\\Pricing 29"
            data["iterations"].append(
                {"path": S29, "name": "Sprint 29", "start": "2026-10-27", "end": "2026-11-07"}
            )
            return json.dumps(data)

        self.edit(CACHE_FILE, rename)
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertIn("sprint: Sprints ended by 2026-11-07", out)
        folder = os.path.join(self.d, "reports", "sprints-ended-by-2026-11-07")
        with open(os.path.join(folder, "manager_2026-11-13.html"), encoding="utf-8") as f:
            page = text(f.read())
        self.assertIn("Sprint 29 (27 Oct – 7 Nov 2026)", page)
        self.assertIn("Pricing 29 (27 Oct – 6 Nov 2026)", page)

    def test_team_without_any_sprint_says_so(self):
        self.edit("team.csv", lambda t: t + "Nadia Kern,nadia@vendor.example,GAMMA,,,1\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        why = (
            "No work assigned: none of the team's 1 engineer has a work item assigned in the "
            "board history (last 200 days)."
        )
        self.assertIn("GAMMA: " + why, out)
        with open(
            os.path.join(self.d, "reports", "sprint-29", "manager_2026-11-13.html"), encoding="utf-8"
        ) as f:
            self.assertIn(why, unescape(f.read()))

        def backlog_item(t):
            data = json.loads(t)
            row = dict(data["revisions"][0], id=999, rev=1, iteration="Proj", assigned="nadia@vendor.example")
            data["revisions"].append(row)
            return json.dumps(data)

        self.edit(CACHE_FILE, backlog_item)
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("GAMMA: No ended sprint: none of the team's counted work items is on a sprint.", out)

    def test_team_without_summary_is_a_warning(self):
        os.makedirs(os.path.join(self.d, "reports", "sprint-29"))
        with open(os.path.join(self.d, "reports", "sprint-29", "notes.md"), "w") as f:
            f.write("# ALFA\n## Summary\nUpload is live.\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("warning: BETA has no summary in notes.md; the manager card shows no highlights", out)
        self.assertNotIn("warning: ALFA has no summary", out)

    def test_report_links_ids_when_board_is_known(self):
        self.edit(
            "config.json",
            lambda t: t.replace('"organization": ""', '"organization": "https://dev.azure.com/acme"').replace(
                '"project": ""', '"project": "Proj"'
            ),
        )
        self.run_cli("report", "--offline", "--today", "2026-11-13")
        for prefix in ("manager", "dm"):
            with open(os.path.join(self.d, "reports", "sprint-29", f"{prefix}_2026-11-13.html")) as f:
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
        self.assertIn(
            "warning: team GAMMA in team.csv has no counting level in config.json; it counts stories", out
        )
        self.assertIn("warning: team BETA in config.json has no one in team.csv; it is not reported", out)
        self.assertNotIn("No sprint named", out)
        with open(os.path.join(self.d, "reports", "sprint-29", "dm_2026-11-13.html"), encoding="utf-8") as f:
            page = text(f.read())
        self.assertIn("GAMMA", page)
        self.assertIn("GAMMA RED Last sprint: Sprint 29", page)
        self.assertNotIn("BETA", page)

    def test_notes_bom_and_unused_lines_are_warnings(self):
        os.makedirs(os.path.join(self.d, "reports", "sprint-29"))
        with open(os.path.join(self.d, "reports", "sprint-29", "notes.md"), "w", encoding="utf-8-sig") as f:
            f.write(
                "# ALFA\n## Actions\n| Ivan Holt | #103 Retry | carry | agreed: ok | |\n"
                "| Ivan Holt | #999 Typo | carry | agreed: typo | |\n# DELTA\n## Summary\nnobody\n"
            )
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertNotIn("line 1 ignored", out)
        self.assertNotIn("carry-over without note: #103", out)
        self.assertIn("#999 Typo", self.read_report("notes.md"))
        self.assertIn("warning: notes.md team DELTA is not in this report; preserved and excluded", out)

    def test_calendar_checks(self):
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0)
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
        with open(
            os.path.join(self.d, "reports", "sprint-29", "manager_2026-11-13.html"), encoding="utf-8"
        ) as f:
            self.assertIn("2 engineers (15 PD capacity)", text(f.read()))  # 2 + 1 days off

    def test_manager_file_names_are_warnings(self):
        self.edit(CACHE_FILE, lambda t: t.replace("Retry & reconcile", "Maya Brook: retry"))
        os.makedirs(os.path.join(self.d, "reports", "sprint-29"))
        with open(os.path.join(self.d, "reports", "sprint-29", "notes.md"), "w") as f:
            f.write("# ALFA\n## Summary\nAsk IVAN.H@vendor.example.\n## Note\nLena Ford helped.\n")
        code, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertEqual(code, 0, out)
        self.assertIn("warning: manager file mentions Maya Brook", out)
        self.assertIn(f"warning: manager file mentions {IVAN}", out)
        self.assertNotIn("mentions Lena Ford", out)  # a note is in the DM file only

    def test_names_match_whole_words(self):
        self.edit("team.csv", lambda t: t + "Ann,ann@vendor.example,ALFA,,,1\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertNotIn("mentions Ann", out)  # "planned" contains "ann"
        with open(os.path.join(self.d, "reports", "sprint-29", "notes.md"), "w") as f:
            f.write("# ALFA\n## Actions\n| Ivan Holt | #103 Retry | carry | agreed: ANN is on leave | |\n")
        _, out = self.run_cli("report", "--offline", "--today", "2026-11-13")
        self.assertIn("warning: manager file mentions Ann", out)

    def test_named_running_or_future_sprint_fails(self):
        for today in ("2026-11-05", "2026-10-26", "2026-11-07"):
            code, out = self.run_cli("report", "--offline", "--today", today, "--sprint", "Sprint 29")
            self.assertEqual(code, 1, out)
            self.assertIn("error: sprint 'Sprint 29' has not ended before " + today, out)
        self.assertFalse(os.path.exists(os.path.join(self.d, "reports", "sprint-29")))

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
