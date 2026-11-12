import unittest

from ledger import history
from ledger.testing.builder import PortfolioBuilder


def cfg(**thresholds):
    b = PortfolioBuilder(tz="Europe/Zurich", thresholds=thresholds)
    b.team("Claims", area="Demo\\Claims")
    return b.config()


# S31..S38, each a two-week sprint of ten working days
_STARTS = ["2026-08-03", "2026-08-17", "2026-08-31", "2026-09-14",
           "2026-09-28", "2026-10-12", "2026-10-26", "2026-11-09"]
_FINISH = ["2026-08-14", "2026-08-28", "2026-09-11", "2026-09-25",
           "2026-10-09", "2026-10-23", "2026-11-06", "2026-11-20"]


def person(**kw):
    p = {"delivered_worked_on": 1, "handed_off": 0, "available_days": 10.0,
         "nominal_days": 10.0, "days_nothing_in_progress": 0.0,
         "board_change_days": 5, "comparison_break": False,
         "is_joiner": False}
    p.update(kw)
    return p


def ticket(**kw):
    t = {"outcome": "delivered", "involvement": "worked",
         "sprints_carried": 0, "held_by_vendor_at_end": False,
         "holder_at_end": None, "returns": [], "backward_moves": 0,
         "handed_off_in_sprint": False, "outside_teams": False}
    t.update(kw)
    return t


def metrics(i, delivered=1, people=None, tickets=None, returned_tickets=0):
    """Metrics for sprint index i (0 -> S31 ... 7 -> S38)."""
    return {
        "sprint": {"id": "s%d" % (31 + i), "name": "S%d" % (31 + i),
                   "start": _STARTS[i], "finish": _FINISH[i]},
        "figures": {"delivered": delivered,
                    "returns": {"events": returned_tickets,
                                "tickets": returned_tickets,
                                "handed_off": 0}},
        "people": {"anna": person()} if people is None else people,
        "tickets": tickets or {},
    }


def ledger_for(**people):
    out = {}
    for pid, d in people.items():
        out["people.%s.returns" % pid] = d.get("returns", [])
        out["people.%s.backward_moves" % pid] = d.get("backward_moves", [])
    return out


def chain(docs, c=None):
    """Apply history to each document in order, like analyze does."""
    c = c or cfg()
    done = []
    for m in docs:
        history.apply(m, ledger_for(anna={}, bence={}), list(done), c)
        done.append(m)
    return done[-1]


class FlagTests(unittest.TestCase):
    def apply_one(self, m, ledger=None, earlier=None, c=None):
        history.apply(m, ledger or ledger_for(anna={}), earlier or [],
                      c or cfg())
        return m

    def test_carried_more_than_two_sprints_flag(self):
        for carried, expect in ((3, True), (2, False)):
            m = metrics(6, tickets={"1": ticket(
                outcome="carried_in_progress", holder_at_end="anna",
                held_by_vendor_at_end=True, sprints_carried=carried)})
            self.apply_one(m)
            self.assertEqual("Carried over more than 2 sprints"
                             in m["people"]["anna"]["flags"], expect)

    def test_carried_flag_needs_holder_and_vendor_hold(self):
        m = metrics(6, tickets={
            "1": ticket(outcome="carried_in_progress", holder_at_end="other",
                        held_by_vendor_at_end=True, sprints_carried=5),
            "2": ticket(outcome="carried_in_progress", holder_at_end="anna",
                        held_by_vendor_at_end=False, sprints_carried=5)})
        self.apply_one(m)
        self.assertEqual(m["people"]["anna"]["flags"], [])

    def test_awaiting_acceptance_never_flags_person(self):
        m = metrics(6, tickets={"1": ticket(
            outcome="awaiting_acceptance", holder_at_end="anna",
            held_by_vendor_at_end=True, sprints_carried=4,
            returns=[{"rev": 3, "at": "2026-10-27T08:00:00+00:00",
                      "reopen": False}] * 3, backward_moves=4)})
        led = ledger_for(anna={"returns": [{"ticket": 1, "rev": 3}],
                               "backward_moves": [{"ticket": 1, "rev": 3}]})
        self.apply_one(m, led)
        self.assertEqual(m["people"]["anna"]["flags"], [])

    def test_returned_repeatedly_and_moving_backward(self):
        ret = {"rev": 3, "at": "2026-10-27T08:00:00+00:00", "reopen": False}
        m = metrics(6, tickets={
            "1": ticket(outcome="carried_in_progress", returns=[ret, ret],
                        backward_moves=2),
            "2": ticket(outcome="delivered", returns=[ret],
                        backward_moves=1)})
        led = ledger_for(anna={"returns": [{"ticket": 1, "rev": 3}],
                               "backward_moves": [{"ticket": 1, "rev": 3}]})
        self.apply_one(m, led)
        self.assertEqual(m["people"]["anna"]["flags"],
                         ["Ticket returned repeatedly",
                          "Ticket moving backward"])
        # below the thresholds: no flags
        led = ledger_for(anna={"returns": [{"ticket": 2, "rev": 3}],
                               "backward_moves": [{"ticket": 2, "rev": 3}]})
        m2 = metrics(6, tickets=m["tickets"])
        self.apply_one(m2, led)
        self.assertEqual(m2["people"]["anna"]["flags"], [])

    def test_fewer_delivered_than_usual_needs_six_sprints(self):
        docs = [metrics(i, delivered=v,
                        people={"anna": person(delivered_worked_on=v)})
                for i, v in enumerate([3, 3, 3, 2, 2, 2])]
        m = chain(docs)
        self.assertEqual(m["figures"]["trend_delivered"], [3, 3, 3, 2, 2, 2])
        self.assertIn("Fewer delivered than usual",
                      m["people"]["anna"]["flags"])
        self.assertIn("Fewer delivered than usual", m["status"]["rules"])
        docs = [metrics(i, delivered=v,
                        people={"anna": person(delivered_worked_on=v)})
                for i, v in enumerate([3, 2, 2, 2])]
        m = chain(docs)
        self.assertNotIn("Fewer delivered than usual",
                         m["people"]["anna"]["flags"])
        self.assertNotIn("Fewer delivered than usual", m["status"]["rules"])

    def test_fewer_needs_each_run_value_below_its_own_mean(self):
        # last value 3 is not below mean(3, 2, 2) = 2.33
        docs = [metrics(i, delivered=v) for i, v in
                enumerate([3, 3, 3, 2, 2, 3])]
        self.assertNotIn("Fewer delivered than usual",
                         chain(docs)["status"]["rules"])

    def test_trend_does_not_cross_comparison_break(self):
        vals = [3, 3, 3, 2, 2, 2]
        docs = [metrics(i, delivered=v, people={"anna": person(
            delivered_worked_on=v, comparison_break=(i == 4))})
            for i, v in enumerate(vals)]
        m = chain(docs)
        self.assertNotIn("Fewer delivered than usual",
                         m["people"]["anna"]["flags"])
        # the team rule is unaffected by a person's break
        self.assertIn("Fewer delivered than usual", m["status"]["rules"])

    def test_person_trend_none_when_absent_blocks_flag(self):
        docs = [metrics(i, people={"anna": person(delivered_worked_on=v)}
                        if i != 1 else {})
                for i, v in enumerate([3, 3, 3, 2, 2, 2])]
        m = chain(docs)
        self.assertEqual(m["people"]["anna"]["trend_delivered"],
                         [3, None, 3, 2, 2, 2])
        self.assertNotIn("Fewer delivered than usual",
                         m["people"]["anna"]["flags"])

    def test_trend_limited_to_window(self):
        docs = [metrics(i, delivered=i) for i in range(8)]
        m = chain(docs)
        self.assertEqual(m["figures"]["trend_delivered"], [2, 3, 4, 5, 6, 7])

    def test_nothing_delivered_two_sprints_with_availability(self):
        idle = dict(delivered_worked_on=0, handed_off=0)
        docs = [metrics(i, people={"anna": person(**idle)}) for i in (5, 6)]
        self.assertIn("Nothing delivered or handed off",
                      chain(docs)["people"]["anna"]["flags"])
        # on leave 60% of the last sprint -> availability 0.4 < 0.5
        docs = [metrics(5, people={"anna": person(**idle)}),
                metrics(6, people={"anna": person(available_days=4.0,
                                                  **idle)})]
        self.assertNotIn("Nothing delivered or handed off",
                         chain(docs)["people"]["anna"]["flags"])
        # only one sprint of history -> not flagged
        docs = [metrics(6, people={"anna": person(**idle)})]
        self.assertNotIn("Nothing delivered or handed off",
                         chain(docs)["people"]["anna"]["flags"])
        # a hand-off counts
        docs = [metrics(5, people={"anna": person(**idle)}),
                metrics(6, people={"anna": person(
                    delivered_worked_on=0, handed_off=1)})]
        self.assertNotIn("Nothing delivered or handed off",
                         chain(docs)["people"]["anna"]["flags"])

    def test_no_recorded_board_changes(self):
        m = metrics(6, people={"anna": person(board_change_days=0)})
        self.assertEqual(self.apply_one(m)["people"]["anna"]["flags"],
                         ["No recorded board changes"])
        m = metrics(6, people={"anna": person(board_change_days=0,
                                              available_days=4.0)})
        self.assertEqual(self.apply_one(m)["people"]["anna"]["flags"], [])

    def test_no_flags_and_context(self):
        m = metrics(6, people={"anna": person(available_days=6.0)})
        p = self.apply_one(m)["people"]["anna"]
        self.assertEqual(p["flags"], [])
        self.assertIn("4 days leave", p["context"])

    def test_context_strings_and_order(self):
        earlier = [metrics(5, people={"bence": person()})]
        history.apply(earlier[0], ledger_for(bence={}), [], cfg())
        m = metrics(6, people={"anna": person(
            available_days=3.5, nominal_days=5.0, is_joiner=True,
            days_nothing_in_progress=3.0, comparison_break=True)})
        self.apply_one(m, earlier=earlier)
        self.assertEqual(m["people"]["anna"]["context"], [
            "1.5 days leave", "Joined in S37",
            "3 days with nothing in progress", "Allocation 0.5",
            "Moved team"])

    def test_joined_names_first_sprint_present(self):
        docs = [metrics(5, people={"anna": person(is_joiner=True)}),
                metrics(6, people={"anna": person(is_joiner=True)})]
        self.assertIn("Joined in S36",
                      chain(docs)["people"]["anna"]["context"])

    def test_full_time_has_no_allocation_context(self):
        p = self.apply_one(metrics(6))["people"]["anna"]
        self.assertEqual(p["context"], [])


class StatusTests(unittest.TestCase):
    def test_team_status_levels(self):
        chronic = {"1": ticket(outcome="carried_in_progress",
                               held_by_vendor_at_end=True, sprints_carried=3,
                               holder_at_end="anna")}
        m = metrics(6)
        history.apply(m, ledger_for(anna={}), [], cfg())
        self.assertEqual(m["status"], {"label": "On track", "rules": [],
                                       "override": None})
        m = metrics(6, tickets=chronic)
        history.apply(m, ledger_for(anna={}), [], cfg())
        self.assertEqual(m["status"], {"label": "Needs attention",
                                       "rules": ["Chronic carryover"],
                                       "override": None})
        m = metrics(6, tickets=chronic, returned_tickets=2)
        history.apply(m, ledger_for(anna={}), [], cfg())
        self.assertEqual(m["status"], {
            "label": "At risk", "rules": ["Chronic carryover", "Returns"],
            "override": None})

    def test_one_returned_ticket_is_not_a_rule(self):
        m = metrics(6, returned_tickets=1)
        history.apply(m, ledger_for(anna={}), [], cfg())
        self.assertEqual(m["status"]["label"], "On track")

    def test_return_cohort(self):
        def ret(at):
            return [{"rev": 4, "at": at, "reopen": False}]
        prev = metrics(5, tickets={
            "1": ticket(handed_off_in_sprint=True),
            "2": ticket(handed_off_in_sprint=True),
            "3": ticket(handed_off_in_sprint=True,
                        returns=ret("2026-09-01T08:00:00+00:00")),
            "4": ticket()})
        prev_ledger = ledger_for(anna={})
        history.apply(prev, prev_ledger, [], cfg())
        self.assertIsNone(prev["figures"]["returns"]["cohort"])
        self.assertNotIn("figures.returns.cohort.handed_off", prev_ledger)
        self.assertNotIn("figures.returns.cohort.returned", prev_ledger)
        self.assertEqual(
            prev_ledger["figures.returns.cohort_so_far.handed_off"],
            [1, 2, 3])
        cur = metrics(6, tickets={
            "1": ticket(returns=ret("2026-10-28T08:00:00+00:00")),
            "5": ticket(handed_off_in_sprint=True),
            "6": ticket(handed_off_in_sprint=True,
                        returns=ret("2026-11-02T08:00:00+00:00"))})
        led = ledger_for(anna={})
        history.apply(cur, led, [prev], cfg())
        self.assertEqual(cur["figures"]["returns"]["cohort"],
                         {"sprint": "S36", "handed_off": 3, "returned": 1})
        self.assertEqual(cur["figures"]["returns"]["cohort_so_far"],
                         {"sprint": "S37", "handed_off": 2, "returned": 1})
        self.assertEqual(led["figures.returns.cohort.handed_off"], [1, 2, 3])
        self.assertEqual(led["figures.returns.cohort.returned"], [1])
        self.assertEqual(led["figures.returns.cohort_so_far.handed_off"],
                         [5, 6])
        self.assertEqual(led["figures.returns.cohort_so_far.returned"], [6])


if __name__ == "__main__":
    unittest.main()
