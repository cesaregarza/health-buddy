"""scripts/progress_summary.py: loaded by core/projection.py for the weight trend."""

import csv
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.progress_summary import (
    Goal,
    MedicationEvent,
    build_summary,
    estimate_treatment_start_weight,
    first_7_day_average_crossing_below,
    infer_injection_timeline,
    load_daily_weights,
    select_forward_projection_window,
    select_start_estimation_window,
    summarize_goal,
)




class ProgressSummaryTests(unittest.TestCase):
    def test_derived_measurement_file_updates_as_of_without_mutating_log(self):
        from tests.synthetic_workspace import START, csv_file, weight_workspace
        with tempfile.TemporaryDirectory() as directory:
            root = weight_workspace(Path(directory))
            before = (root / "data/measurements.csv").read_bytes()
            alternate = csv_file(root / "alternate.csv", ["measured_at_local", "weight_lb"], [
                {"measured_at_local": "2030-02-01T08:00:00+00:00", "weight_lb": 180},
                {"measured_at_local": "2030-02-02T08:00:00+00:00", "weight_lb": 179},
                {"measured_at_local": "2030-02-03T08:00:00+00:00", "weight_lb": 178}])
            summary = build_summary(root, measurements_file=alternate)
            self.assertEqual(summary["as_of"], "2030-02-03")
            self.assertEqual((root / "data/measurements.csv").read_bytes(), before)

    def test_same_day_measurements_use_median(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "measurements.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(("measured_at_local", "weight_lb"))
                writer.writerow(("2026-01-01T08:00:00", "200"))
                writer.writerow(("2026-01-01T08:01:00", "202"))
                writer.writerow(("2026-01-01T08:02:00", "250"))
                writer.writerow(("2026-01-02T08:00:00", "199"))

            daily = load_daily_weights(path)

        self.assertEqual(
            daily,
            [(date(2026, 1, 1), 202.0), (date(2026, 1, 2), 199.0)],
        )

    def test_injection_sequence_infers_first_date(self) -> None:
        events = [
            MedicationEvent(date(2026, 7, 16), "Example medication", "dose_taken", 5),
            MedicationEvent(date(2026, 7, 23), "Example medication", "dose_taken", 6),
        ]

        timeline = infer_injection_timeline(events, "Example medication", 7)

        self.assertIsNotNone(timeline)
        assert timeline is not None
        self.assertEqual(timeline["inferred_first_injection_date"], "2026-06-18")
        self.assertEqual(timeline["latest_injection_number"], 6)
        self.assertEqual(timeline["candidate_start_date_span_days"], 0)

    def test_start_weight_backcast_recovers_linear_series(self) -> None:
        treatment_start = date(2026, 6, 1)
        daily = []
        for offset in range(14, 28):
            day = treatment_start + timedelta(days=offset)
            weight = 230.0 - (2.0 / 7.0) * offset
            daily.append((day, weight))

        estimate = estimate_treatment_start_weight(daily, treatment_start)

        self.assertAlmostEqual(estimate["ordinary_model_lb"], 230.0, places=8)
        self.assertAlmostEqual(estimate["robust_model_lb"], 230.0, places=8)
        self.assertAlmostEqual(estimate["estimated_weight_lb"], 230.0, places=8)
        self.assertGreaterEqual(
            estimate["tracking_range_high_lb"] - estimate["estimated_weight_lb"],
            2.0,
        )

    def test_start_estimation_window_does_not_grow_forever(self) -> None:
        first_day = date(2026, 1, 1)
        daily = [
            (first_day + timedelta(days=offset), 230.0 - offset / 10)
            for offset in range(60)
        ]

        selected = select_start_estimation_window(daily, 28)

        self.assertEqual(len(selected), 28)
        self.assertEqual(selected[0][0], first_day)
        self.assertEqual(selected[-1][0], date(2026, 1, 28))

    def test_forward_projection_window_uses_only_latest_30_calendar_days(self) -> None:
        first_day = date(2026, 1, 1)
        daily = [
            (first_day + timedelta(days=offset), 230.0 - offset / 10)
            for offset in range(60)
        ]

        selected = select_forward_projection_window(daily, 30)

        self.assertEqual(len(selected), 30)
        self.assertEqual(selected[0][0], date(2026, 1, 31))
        self.assertEqual(selected[-1][0], date(2026, 3, 1))

    def test_weight_goal_calculates_progress_and_projection(self) -> None:
        goal = Goal(
            goal_id="weight-200",
            metric="weight_lb",
            direction="decrease",
            target_value=200.0,
            unit="lb",
            status="active",
            priority="primary",
            created_on=date(2026, 1, 1),
            target_date=None,
            source="test",
            notes="",
        )

        result = summarize_goal(
            goal,
            current_weight=215.0,
            anchor=date(2026, 7, 25),
            estimated_start_weight=230.0,
            forward_ordinary_weekly_rate=3.0,
            forward_robust_weekly_rate=2.0,
        )

        self.assertEqual(result["remaining"], 15.0)
        self.assertEqual(result["lost_from_estimated_start"], 15.0)
        self.assertEqual(result["percent_complete"], 50.0)
        self.assertEqual(
            result["projection_range"],
            {"early": "2026-08-29", "late": "2026-09-16"},
        )

    def test_first_crossing_uses_unrounded_seven_day_average(self) -> None:
        first = date(2026, 9, 1)
        daily = [
            (first + timedelta(days=offset), weight)
            for offset, weight in enumerate([201, 200, 200, 199, 198, 202, 199])
        ]
        # The fourth observation is exactly 200; the fifth is the first
        # strictly-below transition. A later recrossing does not replace it.
        self.assertEqual(
            first_7_day_average_crossing_below(daily, 200),
            date(2026, 9, 5),
        )
        self.assertIsNone(
            first_7_day_average_crossing_below(daily[:4], 200)
        )

    def test_crossing_needs_three_observed_days_within_calendar_week(self) -> None:
        daily = [
            (date(2026, 9, 1), 202),
            (date(2026, 9, 2), 201),
            (date(2026, 9, 3), 201),
            (date(2026, 9, 11), 198),
            (date(2026, 9, 12), 198),
            (date(2026, 9, 13), 198),
        ]
        self.assertEqual(
            first_7_day_average_crossing_below(daily, 200),
            date(2026, 9, 13),
        )

    def test_achieved_goal_has_observed_date_not_projection(self):
        from tests.synthetic_workspace import weight_workspace
        with tempfile.TemporaryDirectory() as directory:
            summary = build_summary(weight_workspace(Path(directory)))
            goal = summary["goals"][0]
            self.assertIsNotNone(goal["first_7_day_average_crossing_below"])
            self.assertLess(goal["current_trend_value"], goal["target_value"])
            self.assertEqual(summary["data_quality"]["daily_values"], 14)

    def test_crossing_uses_same_observed_series_and_preserves_missing_treatment(self):
        from tests.synthetic_workspace import weight_workspace
        with tempfile.TemporaryDirectory() as directory:
            root = weight_workspace(Path(directory))
            summary = build_summary(root)
            self.assertIsNone(summary["treatment"])
            self.assertIsNone(summary["estimated_treatment_start"])
            self.assertIsNotNone(summary["goals"][0]["first_7_day_average_crossing_below"])

    def test_summary_is_reproducible_at_a_bounded_synthetic_date(self):
        from tests.synthetic_workspace import weight_workspace
        with tempfile.TemporaryDirectory() as directory:
            root = weight_workspace(Path(directory))
            result = build_summary(root, as_of=date(2030, 1, 13))
            self.assertEqual(result, build_summary(root, as_of=date(2030, 1, 13)))
            self.assertEqual(result["as_of"], "2030-01-13")
            self.assertEqual(result["data_quality"]["daily_values"], 7)


if __name__ == "__main__":
    unittest.main()
