import json
import unittest
from datetime import date, datetime, timezone

from ledger.replay import History, day_end, day_start, local_tz, parse_ts
from tests.fixture import Board

UTC = timezone.utc


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def history(item, nd=()):
    return History(item.rows, nd)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.b = Board()
        self.tz = self.b.tz

    def test_first_active_and_done(self):
        it = self.b.item(1).at("2026-11-02 10:00").at("2026-11-03 09:00", state="Active") \
            .at("2026-11-06 17:00", state="Closed", reason="Done")
        h = history(it, ["Cut"])
        self.assertEqual(h.first_active, utc(2026, 11, 3, 8, 0))
        self.assertEqual(h.done_at, utc(2026, 11, 6, 16, 0))
        self.assertFalse(h.reopened)

    def test_not_delivered_reason_is_not_done(self):
        it = self.b.item(1).at("2026-11-02 10:00", state="Active") \
            .at("2026-11-04 10:00", state="Closed", reason="Cut")
        self.assertIsNone(history(it, ["Cut"]).done_at)

    def test_reopen_keeps_first_done(self):
        it = self.b.item(1).at("2026-11-02 10:00", state="Active") \
            .at("2026-11-05 10:00", state="Closed", reason="Done") \
            .at("2026-11-06 10:00", state="Active", reason="Reopened") \
            .at("2026-11-10 10:00", state="Closed", reason="Done")
        h = history(it)
        self.assertEqual(h.done_at, utc(2026, 11, 5, 9, 0))
        self.assertTrue(h.reopened)

    def test_waiting_seconds_states_and_blocked_tag(self):
        it = self.b.item(1).at("2026-11-02 00:00", state="Active") \
            .at("2026-11-03 00:00", state="Ready for QA") \
            .at("2026-11-04 00:00", state="Active", tags=["blocked"]) \
            .at("2026-11-05 00:00", tags=[]).at("2026-11-06 00:00", state="Closed")
        h = history(it)
        s, e = day_start(date(2026, 11, 2), self.tz), day_start(date(2026, 11, 7), self.tz)
        self.assertEqual(h.waiting_seconds(s, e, ["Ready for QA"]), 2 * 86400)
        # Blocked and waiting at once counts once; window clips
        it2 = self.b.item(2).at("2026-11-02 00:00", state="Ready for QA", tags=["Blocked"]) \
            .at("2026-11-04 00:00", state="Active", tags=[])
        h2 = history(it2)
        self.assertEqual(h2.waiting_seconds(s, day_start(date(2026, 11, 3), self.tz), ["Ready for QA"]),
                         86400)

    def test_timezone_boundary(self):
        tz = local_tz("Europe/Zurich")
        it = self.b.item(1).at("2026-11-07 00:30", state="Closed", reason="Done")
        self.assertEqual(it.rows[0]["changed"], "2026-11-06T23:30:00Z")
        h = History(it.rows, [])
        self.assertGreater(h.done_at, day_end(date(2026, 11, 6), tz))
        self.assertEqual(day_end(date(2026, 11, 6), tz), day_start(date(2026, 11, 7), tz))

    def test_at_and_was_in(self):
        it = self.b.item(1).at("2026-11-02 10:00", iteration="Proj\\S1") \
            .at("2026-11-05 10:00", iteration="Proj\\S2")
        h = history(it)
        self.assertIsNone(h.at(utc(2026, 11, 1)))
        self.assertTrue(h.was_in("Proj\\S1", utc(2026, 11, 4)))
        self.assertFalse(h.was_in("Proj\\S1", utc(2026, 11, 6)))
        self.assertTrue(h.was_in("Proj\\S2", utc(2026, 11, 5, 9, 0)))
        self.assertEqual(h.last_change(utc(2026, 11, 4)), utc(2026, 11, 2, 9, 0))

    def test_assignees_active_and_metadata(self):
        it = self.b.item(7, parent=3, title="T").at("2026-11-02 10:00", assigned="a@v.example") \
            .at("2026-11-03 10:00", state="Active") \
            .at("2026-11-04 10:00", assigned="b@v.example") \
            .at("2026-11-09 10:00", assigned="c@v.example")
        h = history(it)
        self.assertEqual(h.assignees_active(utc(2026, 11, 5)), {"a@v.example", "b@v.example"})
        self.assertEqual((h.id, h.type, h.title, h.parent), (7, "User Story", "T", 3))

    def test_parse_ts_and_cache_json(self):
        self.assertEqual(parse_ts("2026-11-06T23:30:00.1234567Z"), datetime(2026, 11, 6, 23, 30, 0, 123456, tzinfo=UTC))
        self.assertEqual(parse_ts("2026-11-06T23:30:00+01:00"), utc(2026, 11, 6, 22, 30))
        self.b.sprint("S1", "2026-11-02", "2026-11-13")
        self.b.item(1, parent=9).at("2026-11-02 10:00", state="Active", iteration="Proj\\S1")
        c = json.loads(json.dumps(self.b.cache()))
        self.assertEqual(c["parents"], {"9": "Parent 9"})
        self.assertEqual(c["iterations"][0]["path"], "Proj\\S1")
        self.assertEqual(c["revisions"][0]["category"], "InProgress")


if __name__ == "__main__":
    unittest.main()
