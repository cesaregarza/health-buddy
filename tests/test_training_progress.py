import csv
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from scripts.strength_identity import UNVERIFIED_COMPARISONS, comparable_identity, comparison_is_unverified
from scripts.training_progress import build_summary, is_working_set, load_sets

SESSION_HEADERS = (
    "session_id",
    "date",
    "workout_type",
    "status",
    "duration_min",
    "bodyweight_lb",
    "notes",
)
SET_HEADERS = (
    "session_id",
    "session_date",
    "exercise",
    "equipment",
    "set_number",
    "set_count",
    "load_lb",
    "load_basis",
    "reps",
    "rir",
    "form_quality",
    "status",
    "notes",
)
CARDIO_HEADERS = (
    "session_id",
    "session_date",
    "activity",
    "equipment",
    "segment_number",
    "duration_seconds",
)


class TrainingProgressTests(unittest.TestCase):
    def test_shared_identity_requires_explicit_aliases(self) -> None:
        self.assertFalse(UNVERIFIED_COMPARISONS)
        self.assertTrue(comparison_is_unverified("example_lift", "machine"))
        self.assertTrue(comparison_is_unverified("example_lift", ""))
        self.assertFalse(comparison_is_unverified("example_lift", "station_new"))
        first = comparable_identity("Example lift", "station_old", "machine_stack")
        second = comparable_identity("example_lift", "station_new", "total_stack")
        self.assertNotEqual(first, second)
        aliases = {"example_lift": {"station_old": "station_new"}}
        self.assertEqual(
            comparable_identity("Example lift", "station_old", "machine_stack", equipment_aliases=aliases),
            second,
        )
        self.assertNotEqual(
            comparable_identity("Example lift", "station_old", "per_hand", equipment_aliases=aliases),
            second,
        )

    def test_working_set_note_may_mention_an_omitted_practice_set(self) -> None:
        from scripts.training_progress import StrengthSet

        base = StrengthSet(
            "s",
            date(2026, 9, 10),
            "chest_press",
            "machine",
            1,
            40,
            "per_hand",
            10,
            2,
            "completed",
            "Primary working set; planned practice set omitted",
        )
        self.assertTrue(is_working_set(base))
        self.assertFalse(is_working_set(replace(base, notes="Practice set")))

    def write_fixture(self, root: Path) -> None:
        data = root / "data"
        data.mkdir()
        with (data / "sessions.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(SESSION_HEADERS)
            writer.writerow(("upper-1", "2026-08-01", "upper_body", "complete"))
            writer.writerow(("cardio-1", "2026-08-03", "cardio", "complete"))
            writer.writerow(("upper-2", "2026-08-08", "upper_body", "complete"))
            writer.writerow(("future", "2026-08-20", "upper_body", "complete"))
            writer.writerow(("planned", "2026-08-08", "lower_body", "planned"))
        with (data / "sets.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(SET_HEADERS)
            writer.writerow(
                (
                    "upper-1",
                    "2026-08-01",
                    "Chest press",
                    "example_station",
                    "1",
                    "",
                    "20",
                    "per_hand",
                    "12",
                    "",
                    "",
                    "completed",
                    "Warm-up set",
                )
            )
            writer.writerow(
                (
                    "upper-1",
                    "2026-08-01",
                    "Chest press",
                    "example_station",
                    "2",
                    "",
                    "30",
                    "per_hand",
                    "12",
                    "2",
                    "",
                    "completed",
                    "",
                )
            )
            writer.writerow(
                (
                    "upper-2",
                    "2026-08-08",
                    "Chest press",
                    "example_station",
                    "",
                    "2",
                    "40",
                    "per_hand",
                    "10",
                    "2",
                    "",
                    "reported_aggregate",
                    "",
                )
            )
            writer.writerow(
                (
                    "future",
                    "2026-08-20",
                    "Chest press",
                    "example_station",
                    "1",
                    "",
                    "50",
                    "per_hand",
                    "12",
                    "2",
                    "",
                    "completed",
                    "",
                )
            )
        with (data / "cardio.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(CARDIO_HEADERS)
            writer.writerow(("cardio-1", "2026-08-03", "walk", "treadmill", 1, 1800))

    def test_warmup_is_not_a_working_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            rows = load_sets(root / "data" / "sets.csv")

        self.assertFalse(is_working_set(rows[0]))
        self.assertTrue(is_working_set(rows[1]))

    def test_summary_counts_attendance_and_repeated_lifts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            summary = build_summary(
                root,
                as_of=date(2026, 8, 8),
                lookback_days=14,
            )

        self.assertEqual(summary["attendance"]["complete_sessions"], 3)
        self.assertEqual(summary["attendance"]["resistance_sessions"], 2)
        self.assertEqual(summary["attendance"]["cardio_including_sessions"], 1)
        self.assertEqual(summary["work_capacity"]["max_working_sets_in_session"], 2)
        chest = summary["exercise_progress"][0]
        self.assertEqual(chest["first"]["top_load_lb"], 30.0)
        self.assertEqual(chest["latest"]["top_load_lb"], 40.0)
        self.assertEqual(chest["latest"]["working_sets"], 2)
        self.assertEqual(chest["top_load_change_lb"], 10.0)

    def test_report_groups_name_only_migration_but_splits_distinct_stack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            with (root / "data" / "sessions.csv").open(
                "a", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(("stack", "2026-08-09", "upper_body", "complete"))
                writer.writerow(("independent", "2026-08-10", "upper_body", "complete"))
            with (root / "data" / "sets.csv").open(
                "a", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    (
                        "stack",
                        "2026-08-09",
                        "chest_press",
                        "chest_press_machine",
                        1,
                        "",
                        80,
                        "machine_stack_total",
                        8,
                        2,
                        "",
                        "completed",
                        "",
                    )
                )
                writer.writerow(
                    (
                        "independent",
                        "2026-08-10",
                        "chest_press",
                        "independent_arm_chest_press_machine",
                        1,
                        "",
                        40,
                        "per_hand",
                        8,
                        2,
                        "",
                        "completed",
                        "",
                    )
                )
            result = build_summary(root, as_of=date(2026, 8, 10), lookback_days=14, equipment_aliases={"chest_press": {"example_station": "independent_arm_chest_press_machine"}})
        chest = [
            row
            for row in result["exercise_progress"]
            if row["exercise_id"] == "chest_press"
        ]
        self.assertEqual(len(chest), 2)
        independent = next(row for row in chest if row["load_basis"] == "per_hand")
        self.assertEqual(independent["sessions_observed"], 3)
        self.assertEqual(independent["latest"]["top_load_lb"], 40)

    def test_loadless_bodyweight_sets_do_not_render_as_none_pounds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            with (root / "data" / "sets.csv").open(
                "a", newline="", encoding="utf-8"
            ) as handle:
                csv.writer(handle).writerow(
                    (
                        "upper-2",
                        "2026-08-08",
                        "forearm_plank",
                        "bodyweight",
                        1,
                        "",
                        "",
                        "bodyweight",
                        1,
                        "",
                        "",
                        "completed",
                        "duration_seconds=30",
                    )
                )
            result = build_summary(root, as_of=date(2026, 8, 8), lookback_days=14)
        self.assertNotIn(
            "forearm_plank", [row["exercise_id"] for row in result["exercise_progress"]]
        )


if __name__ == "__main__":
    unittest.main()
