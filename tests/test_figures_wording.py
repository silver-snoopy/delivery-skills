"""Figures and wording an executive reads: the return cohort (I1), one
meaning per "carried" label (I7), names-check coverage (I10), risk wording
(I11), text changes bounded by the sprint (I12) and the missing outputs of
I6."""
import unittest

from ledger import history, risks
from ledger.config import Person, Roster
from ledger.metrics import RULES_VERSION, analyze_team_sprint
from ledger.names_check import _terms, names_check
from ledger.render.fmt import planned_text
from ledger.testing.builder import PortfolioBuilder
from ledger.timeline import build_timeline
from ledger.phases import PhaseMapper
try:  # run by discovery (tests/ on the path) or as tests.<module>
    import test_metrics
    import test_render
except ImportError:
    from tests import test_metrics, test_render


class _Rendered(unittest.TestCase):
    """The rendered portfolio of test_render, without its tests: ticket 12
    is in progress with another party, 10 and 13 are carried by the
    vendor, 11 was returned twice."""
    setUpClass = classmethod(test_render.RenderTests.setUpClass.__func__)
    tearDownClass = classmethod(
        test_render.RenderTests.tearDownClass.__func__)
    full_report = test_render.RenderTests.full_report

SPRINTS = [
    ("S35", "2026-09-28", "2026-10-09"),
    ("S36", "2026-10-12", "2026-10-23"),
    ("S37", "2026-10-26", "2026-11-06"),
]
CUTOFF = "2026-11-07 08:00"


def base():
    b = PortfolioBuilder(tz="Europe/Zurich")
    b.team("A", area="Demo\\A", sprints=SPRINTS)
    eva = b.person("eva", "Eva M.", team="A", start="2026-01-05")
    return b, eva, b.other("qa")


def chain(b, team="A", cutoff=CUTOFF):
    snap = b.snapshot(cutoff=cutoff)
    cfg, roster = b.config(), b.roster()
    done = []
    for sp in snap.sprints[team]:
        m, led = analyze_team_sprint(snap, cfg, roster, team, sp)
        history.apply(m, led, list(done), cfg)
        m["_ledger"] = led
        done.append(m)
    return done


class CohortTests(unittest.TestCase):
    def test_return_before_the_hand_off_is_not_in_that_sprints_cohort(self):
        b, eva, qa = base()
        b.ticket(1, team="A").on(
            "2026-10-12 09:00", state="Active", assignee=eva,
            iteration="S36").on(
            "2026-10-20 09:00", state="In QA", assignee=qa).on(
            "2026-10-27 09:00", state="Active", assignee=eva,
            iteration="S37").on(
            "2026-10-30 09:00", state="In QA", assignee=qa)
        m = chain(b)[-1]
        r = m["figures"]["returns"]
        self.assertEqual(r["cohort"], {"sprint": "S36", "handed_off": 1,
                                       "returned": 1})
        self.assertEqual(r["cohort_so_far"], {"sprint": "S37",
                                              "handed_off": 1,
                                              "returned": 0})
        self.assertEqual(
            m["_ledger"]["figures.returns.cohort_so_far.returned"], [])
        self.assertEqual(m["tickets"]["1"]["handed_off_at"],
                         "2026-10-30T08:00:00+00:00")

    def test_return_after_the_hand_off_counts(self):
        b, eva, qa = base()
        b.ticket(1, team="A").on(
            "2026-10-26 09:00", state="Active", assignee=eva,
            iteration="S37").on(
            "2026-10-27 09:00", state="In QA", assignee=qa).on(
            "2026-10-29 09:00", state="Active", assignee=eva)
        r = chain(b)[-1]["figures"]["returns"]
        self.assertEqual(r["cohort_so_far"], {"sprint": "S37",
                                              "handed_off": 1,
                                              "returned": 1})


class PlannedLineTests(unittest.TestCase):
    def test_planned_line_separates_vendor_held_from_other_party(self):
        b, anna, bence = test_metrics.make()
        test_metrics.rich(b, anna, bence)
        m, l, _ = test_metrics.run(b)
        p = m["figures"]["planned"]
        self.assertEqual(p, {"total": 8, "delivered": 2, "awaiting": 1,
                             "carried": 2, "with_other_party": 2, "left": 0,
                             "not_delivered": 1})
        self.assertEqual(p["total"], p["delivered"] + p["awaiting"]
                         + p["carried"] + p["with_other_party"] + p["left"]
                         + p["not_delivered"])
        self.assertEqual(sorted(l["figures.planned.carried"]), [4, 6])
        self.assertEqual(sorted(l["figures.planned.with_other_party"]),
                         [7, 8])
        c = m["figures"]["carried"]
        self.assertLessEqual(p["carried"],
                             c["in_progress"] + c["not_started"])

    def test_planned_text(self):
        self.assertEqual(
            planned_text({"total": 8, "delivered": 2, "awaiting": 1,
                          "carried": 2, "with_other_party": 2, "left": 0,
                          "not_delivered": 1}),
            "8 planned: 2 delivered, 1 awaiting, 2 carried, 2 with another "
            "party, 0 left, 1 not delivered")
        # a sprint analysed before the split has no such number
        self.assertEqual(
            planned_text({"total": 3, "delivered": 2, "awaiting": 0,
                          "carried": 1, "left": 0, "not_delivered": 0}),
            "3 planned: 2 delivered, 0 awaiting, 1 carried, 0 left, "
            "0 not delivered")


class CarriedLabelRenderTests(_Rendered):

    def test_other_party_ticket_is_never_labelled_carried(self):
        for doc in (self.exec_html, self.dm_html, self.exec_md):
            line = [ln for ln in doc.replace("<li>", "\n<li>").splitlines()
                    if "Exposure curve lookup" in ln and "edit/12" in ln][0]
            self.assertIn("In progress with another party", line)
            self.assertNotIn("Carried over", line)

    def test_planned_cell_matches_the_carried_column(self):
        import json
        m = json.loads((self.home.path(
            "teams", "claims", "sprints", "demo-claims-s37", "metrics.json"
        )).read_text(encoding="utf-8"))
        p, c = m["figures"]["planned"], m["figures"]["carried"]
        self.assertEqual(p["with_other_party"], 1)
        self.assertEqual(p["carried"], c["in_progress"] + c["not_started"])
        for doc in (self.exec_html, self.exec_md):
            self.assertIn("%d awaiting, %d carried, 1 with another party"
                          % (p["awaiting"], p["carried"]), doc)

    def test_markdown_has_the_sections_of_the_html(self):
        md = self.exec_md.split("### Claims")[1].split("\n### ")[0]
        self.assertIn("**Where ticket time went**", md)
        self.assertIn("| In progress with a vendor engineer |", md)
        self.assertIn("Work as received: 6 received: 6 with no criteria at "
                      "receipt", md)
        self.assertIn("**In progress with another party**", md)
        other = md.split("**In progress with another party**")[1].split(
            "**")[0]
        self.assertIn("edit/12", other)
        self.assertNotIn("edit/10", other)
        carried = md.split("**Carried over**")[1].split("**")[0]
        self.assertNotIn("edit/12", carried)

    def test_markdown_lists_active_outside_a_sprint(self):
        from ledger.render.markdown import _team as md_team
        from ledger.report import exec_projection
        import copy
        proj = exec_projection(self.full_report())
        team = copy.deepcopy(proj["groups"][0]["teams"][0])
        t = team["metrics"]["tickets"]["13"]
        t.update(outcome="active_outside_sprint")
        md = "\n".join(md_team(proj, team))
        self.assertIn("**Active outside a sprint**", md)
        self.assertIn("edit/13", md.split("**Active outside a sprint**")[1])


class NamesCheckCoverageTests(unittest.TestCase):
    def roster(self):
        return Roster([
            Person("eva", "Eva M.", (), ("eva.molnar@vendor.example",), (),
                   ()),
            Person("ian", "Ian", (), ("CORP\\ian.kovacs", "Li Wu"), (), ()),
            Person("anna", "Anna K.", (), ("akiss@mail.example.com",), (),
                   ())])

    def test_terms_include_three_letter_parts_and_alias_parts(self):
        terms = {t.lower() for t in _terms(self.roster())}
        for want in ("eva", "ian", "molnar", "kovacs", "anna", "akiss",
                     "eva m.", "eva.molnar@vendor.example"):
            self.assertIn(want, terms)
        # mail domains, domain words and parts under three letters are not
        # name parts
        for no in ("vendor", "example", "com", "mail", "corp", "li", "wu",
                   "m", "k"):
            self.assertNotIn(no, terms)

    def test_short_name_and_alias_surname_are_found(self):
        hits = names_check({"executive.md": "Ask Eva about it. Kovacs "
                                            "reviewed the vendor mail."},
                           self.roster(), [])
        self.assertEqual(sorted(h["term"].lower() for h in hits),
                         ["eva", "kovacs"])

    def test_part_inside_a_longer_word_is_not_a_hit(self):
        self.assertEqual(names_check(
            {"executive.md": "Evaluation of the Brian report"},
            self.roster(), []), [])


class RiskWordingTests(unittest.TestCase):
    def candidates(self, m):
        return {c["detector"]: c for c in risks._team_candidates(
            "A", m, self._th())}

    def metrics(self):
        return {
            "sprint": {"name": "S37"}, "figures": {},
            "tickets": {
                "1": {"outcome": "carried_in_progress",
                      "held_by_vendor_at_end": True, "sprints_carried": 3,
                      "workers": {"eva": 3.0}},
                "2": {"outcome": "awaiting_acceptance", "days_waiting": 7.0,
                      "sprints_waiting": 1, "wait_label": "review",
                      "workers": {"eva": 1.0}},
                "3": {"outcome": "awaiting_acceptance", "days_waiting": 6.0,
                      "sprints_waiting": 0, "wait_label": None,
                      "workers": {"eva": 1.0}}},
            "people": {"eva": {"days_nothing_in_progress": 4.0}}}

    def test_carryover_is_a_count_not_a_run(self):
        c = self.candidates(self.metrics())["chronic_carryover"]
        self.assertEqual(c["text"], "A: #1 was still in progress at the end "
                                    "of S37 and has been carried over in 3 "
                                    "sprints.")
        self.assertEqual(c["title"], "#1 carried over in 3 sprints")
        self.assertNotIn("in a row", c["text"])

    def test_wait_names_the_recorded_wait(self):
        found = [c for c in risks._team_candidates(
            "A", self.metrics(), self._th()) if c["detector"] == "long_wait"]
        by = {c["tickets"][0]: c for c in found}
        self.assertEqual(by["2"]["title"], "#2 waiting for review")
        self.assertEqual(by["2"]["text"],
                         "A: #2 was finished and has waited for review for "
                         "7 working days, across 1 sprint end.")
        self.assertEqual(by["3"]["title"], "#3 waiting for acceptance")
        self.assertIn("has waited for acceptance for 6 working days",
                      by["3"]["text"])

    def _th(self):
        b = PortfolioBuilder(tz="Europe/Zurich")
        b.team("A", area="Demo\\A")
        return b.config().thresholds

    def test_nothing_in_progress_ask_presumes_no_cause(self):
        c = self.candidates(self.metrics())["nothing_in_progress"]
        self.assertEqual(c["ask"], "Review the board's ready queue for A "
                                   "before the next sprint.")
        for c in self.candidates(self.metrics()).values():
            for k in ("title", "text", "ask"):
                self.assertNotIn("eva", c[k].lower())
                self.assertNotIn("every engineer", c[k])


class TextChangeBoundTests(unittest.TestCase):
    def build(self):
        b, eva, qa = base()
        b.ticket(1, team="A", description="first", acceptance="ac").on(
            "2026-10-12 09:00", state="Active", assignee=eva,
            iteration="S36").edit(
            "2026-10-14 09:00", description="second").on(
            "2026-10-20 09:00", state="Closed").edit(
            "2026-10-28 09:00", description="third").edit(
            "2026-11-03 09:00", description="fourth")
        return b

    def test_edits_after_the_sprint_are_not_counted_for_it(self):
        ms = chain(self.build())
        self.assertEqual(
            ms[1]["tickets"]["1"]["received"]["text_changes_after"], 1)
        self.assertEqual(ms[1]["figures"]["received"]["text_changed"], 1)

    def test_timeline_keeps_only_revisions_before_the_cut_off(self):
        b = self.build()
        snap = b.snapshot(cutoff="2026-10-21 08:00")
        cfg = b.config()
        tl = build_timeline(snap.tickets[0], PhaseMapper(cfg.team("A"), cfg),
                            b.parse_time("2026-10-21 08:00"), set())
        self.assertEqual([r.rev for r in tl.revisions], [1, 2, 3])
        self.assertEqual(len(snap.tickets[0].revisions), 5)


class MissingOutputTests(_Rendered):
    def test_people_table_shows_assigned_not_started(self):
        import copy
        from ledger.render.html import render_html
        rep = self.full_report()
        rep = copy.deepcopy(rep)
        from ledger.render.evidence_cards import evidence_cards
        rep["evidence"] = evidence_cards(self.home, self.cfg, self.roster,
                                         rep)
        team = rep["groups"][0]["teams"][0]
        p = team["metrics"]["people"]["eng_anna"]
        p.update(days_nothing_in_progress=3.0, todo_assigned_max=2)
        doc = render_html(rep, "dm")
        self.assertIn("3 (2 assigned, not started)", doc)
        p.update(todo_assigned_max=0)
        self.assertNotIn("assigned, not started)", render_html(rep, "dm"))

    def test_returns_split_by_recorded_reason(self):
        from ledger import annotations
        sid = "demo-claims-s37"
        items = [i for i in annotations.pending(
            self.home.read_json("teams", "claims", "sprints", sid,
                                "metrics.json"),
            annotations.load(self.home, "Claims", sid))
            if i["kind"] == "return"]
        self.assertEqual(len(items), 2)
        import copy
        from ledger.render.fmt import returns_by_reason, \
            returns_reason_text
        rep = self.full_report()
        team = copy.deepcopy(rep["groups"][0]["teams"][0])
        self.assertEqual(returns_by_reason(team), {
            "defect": 0, "requirement changed": 0, "other": 0,
            "not recorded": 2})
        team["annotations"][items[0]["key"]] = {
            "key": items[0]["key"], "kind": "return", "ticket": 11,
            "reason": "defect", "note": ""}
        self.assertEqual(returns_reason_text(team),
                         "Returns in the sprint by recorded reason: "
                         "1 defect, 0 requirement changed, 0 other, "
                         "1 not recorded")
        for doc in (self.exec_html, self.dm_html, self.exec_md):
            self.assertIn("Returns in the sprint by recorded reason: "
                          "0 defect, 0 requirement changed, 0 other, "
                          "2 not recorded", doc)

    def test_team_brief_shows_the_split(self):
        from datetime import date
        from ledger.evidence import build_team_brief
        from ledger.render.evidence_html import render_team_brief
        brief = build_team_brief(self.home, self.cfg, self.roster, "Claims",
                                 as_of=date(2026, 11, 7))
        self.assertIn("Returns in the sprint by recorded reason: 0 defect, "
                      "0 requirement changed, 0 other, 2 not recorded.",
                      render_team_brief(brief))

    def test_account_and_cut_off_on_the_dm_report_only(self):
        self.assertIn("Collected as: builder; snapshot cut-off "
                      "2026-11-23 08:00 UTC.", self.dm_html)
        for doc in (self.exec_html, self.exec_md):
            self.assertNotIn("Collected as", doc)
            self.assertNotIn("builder", doc)

    def test_note_when_sprints_were_computed_under_earlier_rules(self):
        import copy
        from ledger.render.html import render_html
        from ledger.render.markdown import render_markdown
        from ledger.report import exec_projection, rules_note
        rep = copy.deepcopy(self.full_report())
        self.assertEqual(rules_note(rep), "")
        self.assertNotIn("earlier calculation rules", self.exec_html)
        rep["groups"][0]["teams"][0]["metrics"]["rules_version"] = "0"
        rep["rules_note"] = rules_note(rep)
        self.assertEqual(
            rep["rules_note"],
            "Claims (S37) was computed under earlier calculation rules "
            "(version 0; current version %s). Its figures are shown as "
            "computed then; re-analyse and issue a new edition to compare "
            "like with like." % RULES_VERSION)
        proj = exec_projection(rep)
        self.assertIn("computed under earlier calculation rules",
                      render_html(proj, "exec"))
        self.assertIn("computed under earlier calculation rules",
                      render_markdown(proj))


if __name__ == "__main__":
    unittest.main()
