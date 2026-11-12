import unittest

from ledger.config import Config, PhaseRule, Team
from ledger.errors import LedgerError
from ledger.model import Phase
from ledger.phases import PhaseMapper


def make(rules, states=(), reasons=()):
    team = Team("Claims", "Claims Team", ("P\\Claims",), tuple(rules))
    cfg = Config(org_url="https://x", project="P", timezone="UTC",
                 teams=(team,), unit_level="stories", unit_types=(),
                 not_delivered_states=frozenset(states),
                 not_delivered_reasons=frozenset(reasons),
                 service_identities=(), holidays=frozenset(), thresholds={},
                 vendor_label="Vendor", agent_text_access="full")
    return PhaseMapper(team, cfg)


class PhaseMapperTests(unittest.TestCase):
    def test_first_matching_rule_wins(self):
        m = make([PhaseRule("Active", "handoff", column="QA", wait_label="QA"),
                  PhaseRule("Active", "doing")])
        self.assertEqual(m.map("Active", "QA", None), (Phase.HANDOFF, "QA"))
        self.assertEqual(m.map("Active", "Dev", None), (Phase.DOING, None))

    def test_matching_ignores_case_and_whitespace(self):
        m = make([PhaseRule("Active", "handoff", column="QA", wait_label="QA")])
        self.assertEqual(m.map("  active ", "qa", None), (Phase.HANDOFF, "QA"))

    def test_split_column_done_half_is_handoff(self):
        m = make([PhaseRule("Active", "handoff", column="Dev",
                            column_done=True, wait_label="review"),
                  PhaseRule("Active", "doing")])
        self.assertEqual(m.map("Active", "Dev", True), (Phase.HANDOFF, "review"))
        self.assertEqual(m.map("Active", "Dev", False), (Phase.DOING, None))
        self.assertEqual(m.map("Active", "Dev", None), (Phase.DOING, None))

    def test_unmapped_state_is_an_error(self):
        m = make([PhaseRule("Active", "doing")])
        with self.assertRaises(LedgerError) as c:
            m.map("Ready for UAT", None, None)
        self.assertIn("Ready for UAT", c.exception.message)
        self.assertIn("Claims", c.exception.message)
        self.assertIn("phases", c.exception.hint)

    def test_unmapped_message_names_column(self):
        m = make([PhaseRule("Active", "doing", column="Dev")])
        with self.assertRaises(LedgerError) as c:
            m.map("Active", "Ops", None)
        self.assertIn("Ops", c.exception.message)

    def test_not_delivered_by_reason_or_state(self):
        m = make([], states={"Removed"}, reasons={"Duplicate"})
        self.assertTrue(m.not_delivered("Closed", "Duplicate"))
        self.assertTrue(m.not_delivered("closed", "duplicate"))
        self.assertFalse(m.not_delivered("Closed", "Completed"))
        self.assertTrue(m.not_delivered("Removed", ""))


if __name__ == "__main__":
    unittest.main()
