"""Rolling cadence boundaries; all records are fabricated."""
import unittest
from datetime import date

from build_dashboard import tracking_schedule


def waist(stamp, value="38", tz="America/Chicago"):
    return {"measured_at_local": stamp, "waist_in": value, "timezone": tz}


class TrackingScheduleTests(unittest.TestCase):
    def test_latest_actual_dates_and_independent_cadences(self):
        result = tracking_schedule(
            [waist("2026-08-19T09:00"), waist("2026-08-01T09:00")],
            ["data/progress_photos/2026-08-10-front.jpeg",
             "data/progress_photos/2026-08-10-back.jpeg",
             "data/progress_photos/2026-07-01-front.jpeg"], date(2026, 8, 20))
        self.assertEqual(result[0]["due_date"], "2026-08-26")
        self.assertEqual(result[1]["due_date"], "2026-09-10")
        updated = tracking_schedule([waist("2026-08-20T09:00")],
                                    ["data/progress_photos/2026-08-10-front.jpeg"], date(2026, 8, 20))
        self.assertEqual(updated[0]["due_date"], "2026-08-27")
        self.assertEqual(updated[1], result[1])

    def test_calendar_month_clamps_and_rolls_year(self):
        for last, due in (("2026-01-31", "2026-02-28"), ("2024-01-31", "2024-02-29"),
                          ("2026-12-31", "2027-01-31"), ("2026-08-31", "2026-09-30")):
            with self.subTest(last=last):
                result = tracking_schedule([], [f"data/progress_photos/{last}-front.jpeg"], date.fromisoformat(last))
                self.assertEqual(result[1]["due_date"], due)

    def test_missing_invalid_and_future_records_have_no_due_date(self):
        result = tracking_schedule(
            [waist("broken"), waist("2026-08-21"), waist("2026-08-01", "0"),
             waist("2026-08-01", "nan"), waist("2026-08-01", "inf")],
            ["data/progress_photos/README.md", "data/progress_photos/2026-08-21-front.jpeg",
             "data/progress_photos/2026-02-31-front.jpeg", "data/progress_photos/2026-08-01.txt",
             "data/other/2026-08-01-front.jpeg"], date(2026, 8, 20))
        for item in result:
            self.assertIsNone(item["last_recorded"])
            self.assertIsNone(item["due_date"])

    def test_timestamp_is_normalized_to_chicago(self):
        result = tracking_schedule([waist("2026-08-20T01:00:00+00:00")], [], date(2026, 8, 20))
        self.assertEqual(result[0]["last_recorded"], "2026-08-19")
        self.assertEqual(result[0]["due_date"], "2026-08-26")
