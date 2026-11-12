import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ledger.errors import LedgerError
from ledger.workcal import WorkCalendar, display_days

ZURICH = ZoneInfo("Europe/Zurich")


def z(y, m, d, h=0):
    """Local Europe/Zurich time as an aware UTC datetime."""
    return datetime(y, m, d, h, tzinfo=ZURICH).astimezone(timezone.utc)


cal = WorkCalendar("Europe/Zurich", [])


class WorkCalendarTests(unittest.TestCase):
    def test_full_week_is_five(self):
        self.assertEqual(cal.working_days(z(2026, 11, 2), z(2026, 11, 9)), 5.0)

    def test_weekend_counts_zero(self):
        self.assertEqual(cal.working_days(z(2026, 11, 7, 9), z(2026, 11, 8, 18)), 0.0)

    def test_half_day(self):
        self.assertAlmostEqual(cal.working_days(z(2026, 11, 3, 6), z(2026, 11, 3, 18)), 0.5)

    def test_holiday_excluded(self):
        c = WorkCalendar("Europe/Zurich", [date(2026, 11, 4)])
        self.assertFalse(c.is_working(date(2026, 11, 4)))
        self.assertEqual(c.working_days(z(2026, 11, 2), z(2026, 11, 7)), 4.0)

    def test_dst_day_counts_one(self):
        self.assertAlmostEqual(cal.working_days(z(2026, 10, 23), z(2026, 10, 27)), 2.0)

    def test_zurich_clock_change_weekend_between_working_days(self):
        # Zurich clock changes fall on Sundays, so these only check that the
        # surrounding working days are unaffected; see the Cairo tests for
        # clock changes that land on a working day.
        self.assertEqual(cal.working_days(z(2026, 10, 26), z(2026, 10, 27)), 1.0)
        self.assertEqual(cal.working_days(z(2026, 3, 27), z(2026, 3, 31)), 2.0)

    def _cairo_day(self, d, hours):
        c = WorkCalendar("Africa/Cairo", [])
        length = (c.day_start(d + timedelta(days=1)) - c.day_start(d)).total_seconds()
        if length != hours * 3600:
            self.skipTest("tzdata has no %d-hour Africa/Cairo day on %s" % (hours, d))
        return c

    def test_23_hour_working_day_counts_one(self):
        d = date(2023, 4, 28)  # Friday, clocks go forward
        c = self._cairo_day(d, 23)
        self.assertTrue(c.is_working(d))
        start, end = c.day_start(d), c.day_start(d + timedelta(days=1))
        self.assertEqual(c.working_days(start, end), 1.0)
        noon = start + timedelta(hours=11)  # local 12:00 after the 1h gap
        self.assertEqual(c.local_date(noon), d)
        self.assertAlmostEqual(c.working_days(noon, end), 12 / 23)

    def test_25_hour_working_day_counts_one(self):
        d = date(2023, 10, 26)  # Thursday, clocks go back
        c = self._cairo_day(d, 25)
        self.assertTrue(c.is_working(d))
        start, end = c.day_start(d), c.day_start(d + timedelta(days=1))
        self.assertEqual(c.working_days(start, end), 1.0)
        half = start + timedelta(hours=12, minutes=30)
        self.assertAlmostEqual(c.working_days(half, end), 0.5)

    def test_end_not_after_start_is_zero(self):
        self.assertEqual(cal.working_days(z(2026, 11, 3, 9), z(2026, 11, 3, 9)), 0.0)
        self.assertEqual(cal.working_days(z(2026, 11, 4), z(2026, 11, 3)), 0.0)

    def test_naive_datetime_raises(self):
        with self.assertRaises(ValueError):
            cal.working_days(datetime(2026, 11, 2), z(2026, 11, 9))
        with self.assertRaises(ValueError):
            cal.local_date(datetime(2026, 11, 2))

    def test_returns_are_utc(self):
        self.assertEqual(cal.day_start(date(2026, 11, 2)), z(2026, 11, 2))
        self.assertEqual(cal.day_start(date(2026, 11, 2)).utcoffset(), timedelta(0))

    def test_local_date_near_midnight(self):
        self.assertEqual(
            cal.local_date(datetime(2026, 11, 5, 23, 30, tzinfo=timezone.utc)),
            date(2026, 11, 6),
        )

    def test_sprint_bounds(self):
        self.assertEqual(
            cal.sprint_bounds(date(2026, 10, 26), date(2026, 11, 6)),
            (z(2026, 10, 26), z(2026, 11, 7)),
        )

    def test_add_working_days_skips_weekend(self):
        self.assertEqual(cal.add_working_days(date(2026, 11, 6), 1), date(2026, 11, 9))

    def test_add_working_days_zero_and_holiday(self):
        self.assertEqual(cal.add_working_days(date(2026, 11, 6), 0), date(2026, 11, 6))
        c = WorkCalendar("Europe/Zurich", [date(2026, 11, 9)])
        self.assertEqual(c.add_working_days(date(2026, 11, 6), 1), date(2026, 11, 10))
        self.assertEqual(c.add_working_days(date(2026, 11, 2), 5), date(2026, 11, 10))

    def test_working_dates_inclusive_working_only(self):
        c = WorkCalendar("Europe/Zurich", [date(2026, 11, 4)])
        self.assertEqual(
            c.working_dates(date(2026, 11, 2), date(2026, 11, 9)),
            [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 5),
             date(2026, 11, 6), date(2026, 11, 9)],
        )
        self.assertEqual(c.working_dates(date(2026, 11, 9), date(2026, 11, 2)), [])

    def test_display_days(self):
        self.assertEqual(
            [display_days(x) for x in (0.2, 0.26, 4.74, 4.76)], [0.0, 0.5, 4.5, 5.0]
        )

    def test_display_days_rounds_half_up(self):
        self.assertEqual([display_days(x) for x in (0.25, 0.75, 1.25)], [0.5, 1.0, 1.5])

    def test_unknown_zone_raises_ledger_error(self):
        with self.assertRaises(LedgerError) as ctx:
            WorkCalendar("Nowhere/Land", [])
        self.assertIn("tzdata", ctx.exception.hint)
        self.assertIn("nothing may be installed", ctx.exception.hint)


if __name__ == "__main__":
    unittest.main()
