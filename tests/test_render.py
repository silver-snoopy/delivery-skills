import contextlib
import copy
import io
import os
import re
import shutil
import stat
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ledger import annotations
from ledger.analyze import trace_data
from ledger.cli import main
from ledger.config import load_config, load_roster
from ledger.home import Home
from ledger.render import render_html, render_markdown
from ledger.render.evidence_cards import evidence_cards
from ledger.render.fmt import clip, days, person_days, ratio
from ledger.render.html_team import team_block
from ledger.render.markdown import _team as md_team
from ledger.report import build_report, exec_projection
from ledger.testing.builder import PortfolioBuilder

AS_OF = "2026-11-07"
HOSTILE = '<script>alert(1)</script> | `x` \U0001F642 ' + 'y' * 400
NOTE = "Criteria changed <b>each</b> time | twice"
FORBIDDEN = ("hands-on", "delivering", "safe to send", "starvation",
             "sole engineer")
UNESCAPED_TAG = re.compile(r"(?<!\\)<[a-zA-Z/]")
PSEUDONYM = re.compile(r"\b[pos]:[0-9a-f]{32}\b")


def dates(prefix, shift=0):
    base = [("35", "2026-09-28", "2026-10-09"),
            ("36", "2026-10-12", "2026-10-23"),
            ("37", "2026-10-26", "2026-11-06"),
            ("38", "2026-11-09", "2026-11-20")]
    out = []
    for n, s, f in base:
        s2 = date.fromordinal(date.fromisoformat(s).toordinal() - 7 * shift)
        f2 = date.fromordinal(date.fromisoformat(f).toordinal() - 7 * shift)
        out.append((prefix + n, s2.isoformat(), f2.isoformat()))
    return out


def portfolio():
    """Claims and Data share sprint dates; Payments ends a week earlier.
    Data is an empty team."""
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("Claims", area="Demo\\Claims", sprints=dates("S"))
    b.team("Data", area="Demo\\Data", sprints=dates("D-"))
    b.team("Payments", area="Demo\\Payments", sprints=dates("P", 1))
    anna = b.person("eng_anna", "Anna K.", team="Claims", start="2026-01-05")
    bence = b.person("eng_bence", "Bence T.", team="Claims",
                     start="2026-01-05")
    dora = b.person("eng_dora", "Dora M.", team="Payments",
                    start="2026-01-05")
    b.absence("eng_bence", "2026-10-28", "2026-10-30", "leave")
    qa, dev, bot = b.other("qa"), b.other("dev"), b.service("bot")

    def delivered(tid, team, it, who, start, parent=""):
        b.ticket(tid, team=team, title="Story %d" % tid,
                 parent_title=parent).on(
            start + " 08:00", iteration=it).on(
            start + " 09:00", state="Active", assignee=who).on(
            start + " 15:00", state="Closed")

    delivered(1, "Claims", "S37", anna, "2026-10-27", "Billing export")
    delivered(2, "Claims", "S37", bence, "2026-10-27", "Billing export")
    # finished and awaiting acceptance
    b.ticket(3, team="Claims", title="Upload audit trail").on(
        "2026-10-26 08:00", iteration="S37").on(
        "2026-10-27 09:00", state="Active", assignee=anna).on(
        "2026-11-04 09:00", state="In QA", assignee=qa)
    # carried three sprints by a roster person, moved by automation
    b.ticket(10, team="Claims", title="Rate table versioning",
             parent_title="Search rework").on(
        "2026-09-28 08:00", iteration="S35").on(
        "2026-09-29 09:00", state="Active", assignee=anna).on(
        "2026-10-12 07:00", iteration="S36", by=bot).on(
        "2026-10-26 07:00", iteration="S37", by=bot)
    # returned twice
    b.ticket(11, team="Claims", title="Broker file upload").on(
        "2026-10-26 08:00", iteration="S37").on(
        "2026-10-27 09:00", state="Active", assignee=anna).on(
        "2026-10-28 09:00", state="In QA", assignee=qa).on(
        "2026-10-29 09:00", state="Active", assignee=anna, by=qa).on(
        "2026-10-30 09:00", state="In QA", assignee=qa).on(
        "2026-11-02 09:00", state="Active", assignee=anna, by=qa).on(
        "2026-11-05 09:00", state="Closed")
    # in progress with another party
    b.ticket(12, team="Claims", title="Exposure curve lookup").on(
        "2026-10-26 08:00", iteration="S37").on(
        "2026-10-27 09:00", state="Active", assignee=anna).on(
        "2026-10-29 09:00", assignee=dev)
    # hostile title, carried in progress by the person on leave
    b.ticket(13, team="Claims", title=HOSTILE).on(
        "2026-10-26 08:00", iteration="S37").on(
        "2026-11-02 09:00", state="Active", assignee=bence)
    for i in (101, 102, 103):
        b.squad_ticket(i, team="Claims").on(
            "2026-10-26 08:00", iteration="S37").on(
            "2026-10-27 09:00", state="Active").on(
            "2026-10-30 09:00", state="Closed")
    delivered(51, "Payments", "P37", dora, "2026-10-21")
    b.ticket(52, team="Payments", title="Story 52").on(
        "2026-10-19 08:00", iteration="P37").on(
        "2026-10-20 09:00", state="Active", assignee=dora)
    for team, tid in (("Claims", 90), ("Data", 91), ("Payments", 93)):
        b.ticket(tid, team=team).on("2026-11-23 09:00")
    return b


def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def read(p):
    return Path(p).read_text(encoding="utf-8")


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.home = Home(Path(cls.tmp))
        cls.builder = portfolio()
        cls.builder.write(cls.home)
        code, _, err = run_main(["--home", cls.tmp, "analyze", "--all"])
        assert code == 0, err
        sid = "demo-claims-s37"
        annotations.record(cls.home, "Claims", sid, "carry:10:" + sid,
                           agreed="no", reason="requirement changed",
                           note=NOTE)
        annotations.record(cls.home, "Claims", sid, "carry:13:" + sid,
                           agreed="yes", reason="planned leave")
        code, out, err = run_main([
            "--home", cls.tmp, "--json", "report", "--as-of", AS_OF,
            "--override", "Claims", "At risk",
            "--reason", "customer escalation"])
        assert code == 0, err
        import json
        cls.result = json.loads(out)
        draft = cls.home.path("reports", AS_OF, "draft")
        cls.exec_html = read(draft / "executive.html")
        cls.exec_md = read(draft / "executive.md")
        cls.dm_html = read(draft / "delivery-manager.html")
        cls.cfg = load_config(cls.home)
        cls.roster = load_roster(cls.home)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def full_report(self):
        return build_report(self.home, self.cfg, self.roster,
                            date.fromisoformat(AS_OF))

    # ---- the command ------------------------------------------------------

    def test_command_writes_three_files_private(self):
        files = self.result["files"]
        self.assertEqual(sorted(files), ["delivery_manager_html",
                                         "executive_html", "executive_md"])
        for p in files.values():
            self.assertTrue(Path(p).is_file(), p)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertEqual(Path(p).parent,
                             self.home.path("reports", AS_OF, "draft"))
        self.assertEqual(self.result["as_of"], AS_OF)
        self.assertEqual(self.result["groups"], 2)
        self.assertEqual(self.result["missing_teams"], [])
        self.assertTrue(self.result["summary"])

    def test_every_team_missing_is_an_error(self):
        d = tempfile.mkdtemp()
        try:
            portfolio().write(Home(Path(d)))  # collected, never analysed
            code, _, err = run_main(["--home", d, "report", "--as-of",
                                     AS_OF])
            self.assertEqual(code, 2)
            self.assertIn("analyze", err)
            self.assertIn("collect", err)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ---- self-contained, no person data -----------------------------------

    def test_exec_html_has_no_external_requests(self):
        for doc in (self.exec_html, self.dm_html):
            for needle in ("http://", "https://fonts", "<link", "src=",
                           "@import", "url("):
                self.assertNotIn(needle, doc)

    def test_exec_html_has_no_script_and_no_person_data(self):
        self.assertNotIn("<script", self.exec_html)
        for doc in (self.exec_html, self.exec_md):
            for p in self.roster.people:
                self.assertNotIn(p.display_name, doc)
                self.assertNotIn(p.id, doc)
            self.assertNotIn("p:eng_", doc)
            self.assertNotIn("Restricted: delivery manager", doc)
            self.assertNotIn("Delivery manager view", doc)
            self.assertNotIn(">Engineer<", doc)
            self.assertNotIn("| Engineer |", doc)
            self.assertNotIn("dm-only", doc)

    def test_no_pseudonym_or_roster_id_in_either_file(self):
        b = self.builder
        for doc in (self.exec_html, self.exec_md, self.dm_html):
            self.assertIsNone(PSEUDONYM.search(doc))
            for name in ("qa", "dev", "bot"):
                self.assertNotIn(b.other(name)[2:], doc)
                self.assertNotIn(b.service(name)[2:], doc)
            for p in self.roster.people:
                self.assertNotIn(p.id, doc)

    def test_exactly_one_script_in_dm_and_none_in_exec(self):
        self.assertEqual(self.dm_html.count("<script"), 1)
        self.assertEqual(self.exec_html.count("<script"), 0)
        self.assertIsNone(UNESCAPED_TAG.search(self.exec_md))

    def test_dark_theme_block_present(self):
        for doc in (self.exec_html, self.dm_html):
            self.assertIn("@media (prefers-color-scheme: dark)", doc)
            self.assertIn("--bg:", doc)
            self.assertIn("system-ui", doc)
            self.assertEqual(doc.count("<style>"), 1)

    # ---- variants ---------------------------------------------------------

    def test_exec_renderer_refuses_dm_dataset(self):
        full = self.full_report()
        with self.assertRaises(ValueError):
            render_html(full, "exec")
        with self.assertRaises(ValueError):
            render_markdown(full)
        proj = exec_projection(full)
        render_html(proj, "exec")  # the projection is accepted
        with self.assertRaises(ValueError):
            render_html(proj, "dm")
        leaked = copy.deepcopy(proj)
        leaked["groups"][0]["teams"][0]["metrics"]["people"] = {}
        with self.assertRaises(ValueError):
            render_html(leaked, "exec")
        with self.assertRaises(ValueError):
            render_markdown(leaked)
        for key in ("workers", "holder_at_end"):
            leaked = copy.deepcopy(proj)
            t = leaked["groups"][0]["teams"][0]["metrics"]["tickets"]
            next(iter(t.values()))[key] = {}
            with self.assertRaises(ValueError):
                render_html(leaked, "exec")
        with self.assertRaises(ValueError):
            render_html(proj, "both")

    def test_dm_html_has_mark_people_table_and_switch(self):
        dm = self.dm_html
        self.assertIn("Restricted: delivery manager", dm)
        for p in self.roster.people:
            self.assertIn(p.display_name, dm)
        self.assertIn("No flags raised", dm)
        self.assertIn("Executive view", dm)
        self.assertIn("Delivery manager view", dm)
        self.assertIn('<body class="view-dm">', dm)
        self.assertIn("dm-only", dm)
        self.assertIn("Two people can both count the same delivered "
                      "ticket.", dm)
        self.assertIn("3 days leave", dm)
        self.assertIn(">Owner<", dm)

    def test_labels_exact_and_forbidden_words_absent(self):
        for doc in (self.exec_html, self.exec_md, self.dm_html):
            self.assertIn("Finished, awaiting acceptance", doc)
            self.assertIn("In progress with a vendor engineer", doc)
            self.assertIn("Not delivered", doc)
            self.assertIn("Delivery report: Vendor team contribution", doc)
            for word in FORBIDDEN:
                self.assertNotIn(word, doc.lower())
        for doc in (self.exec_html, self.dm_html):
            self.assertIn("In progress with another party", doc)
            self.assertIn("Waiting: QA", doc)
        for flag in ("Carried over more than 2 sprints",
                     "Ticket returned repeatedly", "Ticket moving backward"):
            self.assertIn(flag, self.dm_html)
            self.assertNotIn(flag, self.exec_html)

    def test_hostile_text_is_escaped_and_clipped(self):
        for doc in (self.exec_html, self.dm_html):
            self.assertNotIn("<script>alert(1)", doc)
            self.assertIn("&lt;script&gt;", doc)
            self.assertIn("…", doc)
            self.assertNotIn("y" * 100, doc)
            self.assertNotIn("<b>each</b>", doc)
            self.assertIn("&lt;b&gt;each&lt;/b&gt;", doc)
        md = self.exec_md
        self.assertIn("\\|", md)
        self.assertIn("\\<script\\>", md)
        self.assertIn("\\`x\\`", md)
        self.assertNotIn("y" * 100, md)
        self.assertNotIn("<b>", md)

    def test_empty_team_row_renders_dashes(self):
        for doc in (self.exec_html, self.exec_md, self.dm_html):
            self.assertNotIn("None", doc)
        row = re.search(r"<tr[^>]*><td><b>Data</b>.*?</tr>", self.exec_html,
                        re.S)
        self.assertIsNotNone(row)
        self.assertIn("–", row.group(0))
        md_row = next(l for l in self.exec_md.splitlines()
                      if l.startswith("| Data "))
        self.assertIn("–", md_row)

    def test_small_denominator_is_count(self):
        self.assertEqual(ratio(2, 3), "2 of 3")
        self.assertEqual(ratio(21, 58), "21 of 58 (36%)")
        self.assertEqual(ratio(0, 0), "0 of 0")
        self.assertEqual(days(None), "–")
        self.assertEqual(days(2.0), "2")
        self.assertEqual(days(1.5), "1.5")
        self.assertEqual(days(2.25), "2.5")
        self.assertEqual(days(2.75), "3")
        self.assertEqual(days(2.2), "2")
        self.assertEqual(clip("abc", 90), "abc")
        self.assertEqual(clip("x" * 100, 90), "x" * 89 + "…")
        self.assertEqual(len(clip("x" * 100, 90)), 90)

    def test_unchanged_and_backfilled_markers(self):
        full = self.full_report()
        claims = full["groups"][0]["teams"][0]
        claims["unchanged"] = True
        claims["backfilled"] = True
        dm = render_html(full, "dm")
        proj = exec_projection(full)
        for doc in (dm, render_html(proj, "exec"), render_markdown(proj)):
            self.assertIn("unchanged since the last report", doc)
            self.assertIn("backfilled from history", doc)
        self.assertNotIn("unchanged since the last report", self.exec_html)

    def test_markdown_has_tables_per_group(self):
        md = self.exec_md
        self.assertTrue(md.startswith(
            "# Delivery report: Vendor team contribution\n"))
        self.assertIn("## Sprints ending 6 Nov 2026", md)
        self.assertIn("## Sprints ending 30 Oct 2026", md)
        self.assertEqual(md.count("| Team | Sprint | Engineers |"), 2)
        self.assertIsNone(UNESCAPED_TAG.search(md))
        self.assertIn("### Claims", md)
        self.assertIn("Carried over", md)
        self.assertIn("[#10](https://dev.azure.com/demo/Demo/_workitems/"
                      "edit/10)", md)

    def test_override_shown_with_reason_in_both(self):
        for doc in (self.exec_html, self.exec_md, self.dm_html):
            self.assertIn("set by the delivery manager: customer escalation",
                          doc)
            self.assertIn("At risk", doc)
            self.assertIn("computed: Needs attention", doc)
            self.assertIn("Chronic carryover", doc)
        self.assertEqual(self.home.read_json(
            "reports", AS_OF, "overrides.json")["Claims"]["label"], "At risk")

    def test_clear_override_rebuilds_without_it(self):
        d = tempfile.mkdtemp()
        try:
            shutil.copytree(self.tmp, d, dirs_exist_ok=True)
            code, _, err = run_main(["--home", d, "report", "--as-of", AS_OF,
                                     "--clear-override", "Claims"])
            self.assertEqual(code, 0, err)
            doc = read(Path(d) / "reports" / AS_OF / "draft" /
                       "executive.html")
            self.assertNotIn("set by the delivery manager", doc)
            self.assertIn("Needs attention", doc)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # ---- content ----------------------------------------------------------

    def test_key_figures_and_team_table(self):
        doc = self.exec_html
        self.assertIn("<title>Delivery report 2026-11-07</title>", doc)
        self.assertIn("Teams are grouped by sprint dates", doc)
        self.assertIn("Delivered by Vendor team", doc)
        self.assertIn("3 of 6 (50%)", doc)  # vendor delivered of squad
        self.assertIn("1 agreed in advance, 1 not, 0 not yet answered", doc)
        self.assertIn("Available person-days", doc)
        self.assertIn("17 of 20", doc)
        for col in ("Team", "Sprint", "Engineers", "Delivered, last sprints",
                    "Squad total", "Planned", "Carried over",
                    "With another party", "Returns",
                    "Days with a vendor engineer (median)", "Status"):
            self.assertIn(">%s<" % col, doc)
        self.assertIn("6 received: 6 with no criteria at receipt", doc)
        self.assertNotIn("Risks and asks", doc)  # no risks recorded
        self.assertIn("Figures count tickets", doc)

    def test_ticket_links(self):
        for doc in (self.exec_html, self.dm_html):
            self.assertIn('href="https://dev.azure.com/demo/Demo/_workitems/'
                          'edit/10"', doc)

    def test_trend_bars_are_percentage_heights_with_text(self):
        self.assertIn('style="height:100%"', self.exec_html)
        self.assertIn('aria-label="Delivered per sprint: 0, 0, 3"',
                      self.exec_html)
        self.assertNotIn("<svg", self.exec_html)

    def test_carried_table_lists_vendor_held_only(self):
        sec = self.exec_html.split('id="team-claims"')[1].split(
            '<section')[0]
        carried = sec.split("Carried over</h4>")[1].split("</table>")[0]
        self.assertIn("edit/10", carried)
        self.assertIn("edit/13", carried)
        self.assertNotIn("edit/12", carried)  # with another party
        self.assertNotIn("edit/3", carried)   # awaiting acceptance
        self.assertIn(">No<", carried)
        self.assertIn(">Yes<", carried)
        self.assertIn("requirement changed", carried)
        self.assertIn("S35", carried)
        other = sec.split("In progress with another party</h4>")[1].split(
            "</table>")[0]
        self.assertIn("edit/12", other)

    def test_risks_section_rendered_and_escaped_when_present(self):
        proj = exec_projection(self.full_report())
        proj["risks"] = [{"kind": "Risk", "title": "Review <slow>",
                          "text": "Two | tickets", "ask": "A named reviewer",
                          "tickets": [11]}]
        doc = render_html(proj, "exec")
        self.assertIn("Risks and asks", doc)
        self.assertIn("Review &lt;slow&gt;", doc)
        self.assertIn("A named reviewer", doc)
        self.assertIn("edit/11", doc.split("Risks and asks")[1])
        md = render_markdown(proj)
        self.assertIn("## Risks and asks", md)
        self.assertIn("Two \\| tickets", md)

    # ---- ticket lists without narrative text ------------------------------

    def test_lists_show_titles_when_no_text(self):
        for doc in (self.exec_html, self.dm_html, self.exec_md):
            self.assertIn("Broker file upload", doc)      # delivered
            self.assertIn("Upload audit trail", doc)      # awaiting
            self.assertIn("waiting 2.5 working days · QA", doc)
            self.assertIn("Exposure curve lookup", doc)   # not delivered
            self.assertIn("Carried over, in progress", doc)
        md = self.exec_md
        self.assertIn("  - [#11](https://dev.azure.com/demo/Demo/_workitems/"
                      "edit/11) Broker file upload", md)
        self.assertIsNone(UNESCAPED_TAG.search(md))
        html_list = self.exec_html.split("<h4>Delivered</h4>")[1].split(
            "</ul>\n")[0]
        self.assertIn("<ul><li>", html_list)  # nested list

    def _synthetic(self, entries):
        proj = exec_projection(self.full_report())
        team = copy.deepcopy(proj["groups"][0]["teams"][0])
        tickets = team["metrics"]["tickets"]
        tickets["700"] = dict(tickets["1"], title="Fix <crash> " + "z" * 200,
                              type="Bug")
        team["narrative"] = {"delivered": entries, "awaiting": [],
                             "not_delivered": []}
        return proj, team

    def test_title_list_type_clip_and_missing_ticket(self):
        proj, team = self._synthetic([{"heading": "H", "text": "",
                                       "tickets": [1, 2, 700, 999]}])
        doc = team_block(proj, team, "exec", [])
        self.assertIn("(Bug)", doc)
        self.assertNotIn("(User Story)", doc)
        self.assertIn("Fix &lt;crash&gt;", doc)
        line = [l for l in doc.split("<li>") if "edit/700" in l][0]
        self.assertIn("…", line)
        self.assertNotIn("z" * 110, line)
        self.assertIn('edit/999">#999</a></li>', doc)
        md = "\n".join(md_team(proj, team))
        self.assertIn("(Bug)", md)
        self.assertIn("\\<crash\\>", md)
        self.assertIn("edit/999)", md)

    def test_agent_text_keeps_links_only(self):
        proj, team = self._synthetic([{"heading": "H", "text": "Did things.",
                                       "tickets": [1, 700]}])
        doc = team_block(proj, team, "exec", [])
        self.assertIn("Did things.", doc)
        self.assertNotIn("Fix &lt;crash&gt;", doc)
        self.assertNotIn("Story 1", doc)
        md = "\n".join(md_team(proj, team))
        self.assertNotIn("crash", md)

    def test_owner_cell_shows_display_name(self):
        sec = self.dm_html.split('id="team-claims"')[1]
        carried = sec.split("Carried over</h4>")[1].split("</table>")[0]
        row = [r for r in carried.split("<tr>") if "edit/10" in r][0]
        self.assertIn('<td class="dm-only">Anna K.</td>', row)

    def test_markdown_paragraphs_and_person_days(self):
        self.assertIn("As of 7 Nov 2026.\n\nTeams are grouped", self.exec_md)
        self.assertEqual(person_days(17.0, 20.0), "17 of 20 (85%)")
        self.assertEqual(person_days(7.5, 10.0), "7.5 of 10")
        self.assertIn("Available person-days: 17 of 20 (85%)", self.exec_md)
        self.assertIn("17 of 20 (85%)", self.exec_html)

    # ---- evidence ---------------------------------------------------------

    def test_evidence_card_dm_only(self):
        dm = self.dm_html
        self.assertIn('id="evidence-10"', dm)
        self.assertIn('id="evidence-11"', dm)
        self.assertNotIn('id="evidence-13"', dm)
        card = dm.split('id="evidence-10"')[1].split("</article>")[0]
        self.assertIn("In progress with a vendor engineer for", card)
        self.assertIn("automation", card)
        self.assertIn("Anna K.", card)
        self.assertIn("&lt;b&gt;each&lt;/b&gt;", card)  # DM's note
        card11 = dm.split('id="evidence-11"')[1].split("</article>")[0]
        self.assertIn("another party", card11)
        self.assertIn("Waiting: QA", card11)
        for doc in (self.exec_html, self.exec_md):
            self.assertNotIn("evidence-", doc)
            self.assertNotIn("Evidence", doc)

    def test_evidence_cards_function(self):
        full = self.full_report()
        cards = evidence_cards(self.home, self.cfg, self.roster, full)
        ids = [c["ticket"] for c in cards["Claims"]]
        self.assertEqual(ids[0], 10)  # most sprints carried first
        self.assertEqual(sorted(ids), [10, 11])
        self.assertEqual(cards.get("Data", []), [])
        c10 = cards["Claims"][0]
        self.assertGreater(c10["vendor_days"], 0)
        labels = {s["holder"] for s in c10["segments"]}
        self.assertIn("Anna K.", labels)
        self.assertIsNone(PSEUDONYM.search(repr(cards)))
        c11 = cards["Claims"][1]
        self.assertNotEqual(c11["segments"][-1]["phase"], "done")

    def test_person_counts_read_naturally(self):
        self.assertIn("1 (longest 1 sprint)", self.dm_html)
        self.assertIn("1 (longest 3 sprints)", self.dm_html)

    def test_trace_data_is_structural(self):
        d = trace_data(self.home, self.cfg, self.roster, 11)
        self.assertEqual(d["ticket"], 11)
        self.assertEqual(d["team"], "Claims")
        self.assertTrue(d["intervals"])
        for key in ("start", "days", "phase", "state", "holder", "sprint"):
            self.assertIn(key, d["intervals"][0])
        self.assertEqual(sum(1 for e in d["events"] if "returned"
                             in e["flags"]), 2)
        self.assertIn("vendor", d["time_split"])
        self.assertTrue(d["changes"])


if __name__ == "__main__":
    unittest.main()
