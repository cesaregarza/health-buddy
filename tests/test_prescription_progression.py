import copy
import csv
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from scripts.next_workout import Session
from scripts.prescription_progression import (
    ProgressionError,
    SetRecord,
    apply_progression,
    load_sets,
)


def record(
    session_id: str,
    session_date: date,
    *,
    load: float,
    reps: int,
    set_number: int,
    rir: int | None = 2,
    exercise: str = "press",
    equipment: str = "press_machine",
    basis: str = "machine_stack",
    form: str = "clean",
    notes: str = "",
) -> SetRecord:
    return SetRecord(
        session_id,
        session_date,
        exercise,
        equipment,
        set_number,
        load,
        basis,
        reps,
        rir,
        form,
        "completed",
        notes,
    )


def template(**progression: object) -> dict:
    config = {
        "exercise_id": "press",
        "equipment_id": "press_machine",
        "load_basis": "machine_stack",
        "baseline_load": 40,
        "sets": 2,
        "rep_min": 8,
        "rep_max": 12,
        "baseline_reps": [8, 8],
        "next_load": None,
    }
    config.update(progression)
    return {
        "label": "Test strength",
        "target_duration": "20 minutes",
        "warmup": [],
        "exercises": [
            {
                "exercise": "Press",
                "equipment": "machine",
                "load": "40 lb",
                "work": "2 x 8",
                "rir": "2-3",
                "next_target": "Reviewed target",
                "progression": config,
            }
        ],
    }


class PrescriptionProgressionTests(unittest.TestCase):
    day = date(2026, 9, 7)

    def sessions(self, *, status: str = "complete") -> list[Session]:
        return [Session(self.day, "upper_body", status, "s1")]

    def apply(self, records: list[SetRecord] | None = None, **config: object) -> dict:
        _, results = apply_progression(
            template(**config), self.sessions(), records or [], self.day
        )
        return results["press"]

    def test_reps_add_only_from_clean_known_rir_primary_sets(self) -> None:
        result = self.apply(
            [
                record("s1", self.day, load=40, reps=9, set_number=1),
                record("s1", self.day, load=40, reps=10, set_number=2),
                record("s1", self.day, load=40, reps=8, set_number=3, notes="warm-up"),
            ]
        )
        self.assertEqual(result["status"], "reps")
        self.assertEqual(result["rep_targets"], [10, 11])
        self.assertEqual(result["source_date"], "2026-09-07")

    def test_one_rir_holds_that_set_instead_of_resetting_exercise(self) -> None:
        result = self.apply(
            [
                record("s1", self.day, load=40, reps=10, set_number=1, rir=2),
                record("s1", self.day, load=40, reps=9, set_number=2, rir=1),
            ]
        )
        self.assertEqual(result["status"], "hold_2_rir")
        self.assertEqual(result["rep_targets"], [11, 9])

    def test_missing_rir_or_set_is_incomplete(self) -> None:
        result = self.apply(
            [record("s1", self.day, load=40, reps=10, set_number=1, rir=None)]
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["rep_targets"], [8, 8])

    def test_latest_failed_exposure_wins_over_stale_qualifying_exposure(self) -> None:
        earlier = date(2026, 9, 1)
        records = [
            record("old", earlier, load=40, reps=12, set_number=1),
            record("old", earlier, load=40, reps=12, set_number=2),
            record("s1", self.day, load=40, reps=12, set_number=1, rir=1),
            record("s1", self.day, load=40, reps=12, set_number=2, rir=2),
        ]
        sessions = [
            Session(earlier, "upper_body", "complete", "old"),
            *self.sessions(),
        ]
        _, results = apply_progression(template(), sessions, records, self.day)
        self.assertEqual(results["press"]["status"], "hold_2_rir")
        self.assertEqual(results["press"]["source_date"], "2026-09-07")

    def test_exact_identity_mismatch_has_no_comparable_data(self) -> None:
        result = self.apply(
            [
                record(
                    "s1", self.day, load=40, reps=12, set_number=1, basis="per_hand"
                ),
                record(
                    "s1", self.day, load=40, reps=12, set_number=2, basis="per_hand"
                ),
            ]
        )
        self.assertEqual(result["status"], "no_comparable_data")

    def test_small_increment_uses_lower_rep_range(self) -> None:
        records = [
            record("s1", self.day, load=40, reps=12, set_number=1),
            record("s1", self.day, load=40, reps=12, set_number=2),
        ]
        result = self.apply(records, next_load=44)
        self.assertEqual(result["status"], "eligible_next_load")
        self.assertEqual(result["recommended_load"], 44)
        self.assertEqual(result["rep_targets"], [8, 8])

    def test_coarse_increment_has_trial_and_backoff(self) -> None:
        records = [
            record("s1", self.day, load=40, reps=12, set_number=1),
            record("s1", self.day, load=40, reps=12, set_number=2),
        ]
        result = self.apply(records, next_load=50, fallback_load=40)
        self.assertEqual(result["status"], "coarse_increment_trial")
        self.assertEqual(result["recommended_load"], 50)
        self.assertEqual(result["fallback_load"], 40)

    def test_successful_coarse_trial_can_attempt_both_heavy_sets_next_time(
        self,
    ) -> None:
        records = [
            record("s1", self.day, load=50, reps=8, set_number=1),
            record("s1", self.day, load=40, reps=12, set_number=2),
        ]
        result = self.apply(records, next_load=50)
        self.assertEqual(result["status"], "mixed_trial_succeeded")
        self.assertEqual(result["current_load"], 40)
        self.assertEqual(result["recommended_load"], 50)
        self.assertEqual(result["rep_targets"], [8, 8])
        self.assertEqual([row["load"] for row in result["evidence_sets"]], [50, 40])
        self.assertIn("Do not add a replacement set", result["target"])

    def test_coarse_trial_requires_controlled_form_and_reserve(self) -> None:
        records = [
            record("s1", self.day, load=50, reps=8, set_number=1, form="not_reported"),
            record("s1", self.day, load=40, reps=12, set_number=2),
        ]
        self.assertEqual(self.apply(records, next_load=50)["status"], "incomplete")
        records[0] = replace(records[0], form_quality="clean", rir=1)
        self.assertEqual(self.apply(records, next_load=50)["status"], "incomplete")
        records[0] = replace(records[0], rir=2)
        records[1] = replace(records[1], rir=1)
        self.assertEqual(self.apply(records, next_load=50)["status"], "incomplete")

    def test_extra_coarse_trial_sets_do_not_advance(self) -> None:
        records = [
            record("s1", self.day, load=50, reps=8, set_number=1),
            record("s1", self.day, load=40, reps=12, set_number=2),
        ]
        self.assertEqual(self.apply(records)["status"], "mixed_trial_succeeded")
        self.assertEqual(
            self.apply(
                [*records, record("s1", self.day, load=40, reps=10, set_number=3)],
                next_load=50,
            )["status"],
            "reps",
        )

    def test_small_increment_after_lighter_set_can_attempt_two_at_new_load(
        self,
    ) -> None:
        records = [
            record("s1", self.day, load=42.5, reps=10, set_number=1),
            record("s1", self.day, load=45, reps=10, set_number=2),
        ]
        result = self.apply(
            records, baseline_load=40, rep_min=10, baseline_reps=[10, 10]
        )
        self.assertEqual(result["status"], "mixed_trial_succeeded")
        self.assertEqual(result["recommended_load"], 45)
        self.assertEqual(result["fallback_load"], 42.5)

    def test_higher_load_one_rir_set_holds_that_load_without_earning_next_jump(
        self,
    ) -> None:
        records = [
            record("s1", self.day, load=45, reps=10, set_number=1, rir=2),
            record("s1", self.day, load=45, reps=10, set_number=2, rir=1),
        ]
        result = self.apply(
            records, baseline_load=40, rep_min=10, baseline_reps=[10, 10]
        )
        self.assertEqual(result["status"], "hold_2_rir")
        self.assertEqual(result["current_load"], 45)
        self.assertEqual(result["rep_targets"], [11, 10])
        self.assertIsNone(result["recommended_load"])

    def test_higher_load_bad_form_does_not_override_qualified_lower_load(self) -> None:
        records = [
            record("s1", self.day, load=45, reps=10, set_number=1),
            record(
                "s1", self.day, load=45, reps=10, set_number=2, rir=1, form="form_break"
            ),
        ]
        result = self.apply(
            records, baseline_load=40, rep_min=10, baseline_reps=[10, 10]
        )
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["current_load"], 40)

    def test_unknown_increment_is_explicitly_unverified(self) -> None:
        records = [
            record("s1", self.day, load=40, reps=12, set_number=1),
            record("s1", self.day, load=40, reps=12, set_number=2),
        ]
        result = self.apply(records)
        self.assertEqual(result["status"], "eligible_next_load")
        self.assertIsNone(result["recommended_load"])
        self.assertIn("verify", result["target"])

    def test_higher_two_set_exposure_promotes_same_machine_baseline(self) -> None:
        records = [
            record("s1", self.day, load=45, reps=8, set_number=1),
            record("s1", self.day, load=45, reps=9, set_number=2),
        ]
        result = self.apply(records)
        self.assertTrue(result["baseline_promoted"])
        self.assertEqual(result["current_load"], 45)

    def test_overlay_does_not_mutate_input_template(self) -> None:
        original = template()
        before = copy.deepcopy(original)
        apply_progression(
            original,
            self.sessions(),
            [
                record("s1", self.day, load=40, reps=9, set_number=1),
                record("s1", self.day, load=40, reps=9, set_number=2),
            ],
            self.day,
        )
        self.assertEqual(original, before)

    def test_in_progress_and_future_sessions_are_ignored(self) -> None:
        result = self.apply(
            [
                record("s1", self.day, load=40, reps=12, set_number=1),
                record("s1", self.day, load=40, reps=12, set_number=2),
            ]
        )
        self.assertEqual(result["status"], "eligible_next_load")
        future = date(2026, 9, 8)
        sessions = [Session(future, "upper_body", "complete", "future")]
        _, results = apply_progression(
            template(),
            sessions,
            [
                record("future", future, load=40, reps=12, set_number=1),
                record("future", future, load=40, reps=12, set_number=2),
            ],
            self.day,
        )
        self.assertEqual(results["press"]["status"], "no_comparable_data")

        in_progress = [Session(self.day, "upper_body", "in_progress", "s1")]
        in_progress_records = [
            record("s1", self.day, load=40, reps=12, set_number=1),
            record("s1", self.day, load=40, reps=12, set_number=2),
        ]
        _, results = apply_progression(
            template(), in_progress, in_progress_records, self.day
        )
        self.assertEqual(results["press"]["status"], "no_comparable_data")

    def test_bad_form_primary_set_is_not_filtered_away(self) -> None:
        records = [
            record("s1", self.day, load=40, reps=12, set_number=1, form="form_break"),
            record("s1", self.day, load=40, reps=12, set_number=2),
            record("s1", self.day, load=40, reps=12, set_number=3),
        ]
        result = self.apply(records)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(len(result["evidence_sets"]), 2)
        self.assertEqual(result["evidence_sets"][0]["form_quality"], "form_break")

    def test_lower_load_backoff_cannot_qualify_reviewed_primary(self) -> None:
        records = [
            record("s1", self.day, load=30, reps=12, set_number=3),
            record("s1", self.day, load=30, reps=12, set_number=4),
        ]
        result = self.apply(records)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["current_load"], 40)

    def test_same_day_sessions_are_not_combined(self) -> None:
        sessions = [
            Session(self.day, "upper_body", "complete", "a"),
            Session(self.day, "upper_body", "complete", "b"),
        ]
        records = [
            record("a", self.day, load=40, reps=12, set_number=1),
            record("b", self.day, load=40, reps=12, set_number=2),
        ]
        _, results = apply_progression(template(), sessions, records, self.day)
        self.assertEqual(results["press"]["status"], "incomplete")
        self.assertIsNone(results["press"]["source_session_id"])
        self.assertTrue(results["press"]["review_required"])

    def test_work_ordinals_after_warmup_qualify(self) -> None:
        rows = [
            record("s1", self.day, load=20, reps=8, set_number=1, notes="practice_set"),
            record("s1", self.day, load=40, reps=12, set_number=2),
            record("s1", self.day, load=40, reps=12, set_number=3),
        ]
        result = self.apply(rows)
        self.assertEqual(result["status"], "eligible_next_load")
        self.assertEqual([s["set_number"] for s in result["evidence_sets"]], [2, 3])

    def test_working_note_that_mentions_missing_practice_set_is_not_excluded(
        self,
    ) -> None:
        result = self.apply(
            [
                record(
                    "s1",
                    self.day,
                    load=40,
                    reps=12,
                    set_number=1,
                    notes="Primary working set; planned practice set was omitted",
                ),
                record("s1", self.day, load=40, reps=12, set_number=2),
            ]
        )
        self.assertEqual(result["status"], "eligible_next_load")
        self.assertEqual(len(result["evidence_sets"]), 2)

    def test_unreported_form_is_explained_without_awarding_progression(self) -> None:
        rows = [
            record("s1", self.day, load=40, reps=12, set_number=i, form="not_reported")
            for i in (1, 2)
        ]
        output, results = apply_progression(template(), self.sessions(), rows, self.day)
        self.assertEqual(results["press"]["status"], "incomplete")
        self.assertEqual(results["press"]["review_reason"], "form_not_reported")
        self.assertIn("Form not logged", output["exercises"][0]["next_target"])
        self.assertNotIn("increment earned", output["exercises"][0]["next_target"])

    def test_failed_status_cannot_qualify_or_reuse_stale_increment_text(self) -> None:
        failed = replace(
            record("s1", self.day, load=40, reps=12, set_number=1), status="failed"
        )
        output, results = apply_progression(
            template(),
            self.sessions(),
            [failed, record("s1", self.day, load=40, reps=12, set_number=2)],
            self.day,
        )
        self.assertEqual(results["press"]["status"], "incomplete")
        self.assertTrue(results["press"]["review_required"])
        self.assertNotEqual(output["exercises"][0]["next_target"], "Reviewed target")

    def test_primary_header_and_extra_backoff_remain_distinct(self) -> None:
        rows = [record("s1", self.day, load=40, reps=12, set_number=i) for i in (1, 2)]
        output, results = apply_progression(
            template(
                next_load=50,
                fallback_load=30,
                backoff_sets=1,
                backoff_rep_min=10,
                backoff_rep_max=12,
            ),
            self.sessions(),
            rows,
            self.day,
        )
        exercise = output["exercises"][0]
        self.assertEqual(results["press"]["fallback_load"], 40)
        self.assertIn("50 lb stack", exercise["load"])
        self.assertIn("40 lb stack", exercise["load"])
        self.assertIn("30 lb stack", exercise["load"])
        self.assertIn("1 additional backoff", exercise["work"])
        self.assertNotEqual(exercise["load"], exercise["work"])
        self.assertNotEqual(exercise["work"], exercise["next_target"])

    def test_promoted_top_range_still_earns_an_increment_check(self) -> None:
        result = self.apply(
            [record("s1", self.day, load=50, reps=12, set_number=i) for i in (1, 2)],
            next_load=45,
        )
        self.assertEqual(result["current_load"], 50)
        self.assertEqual(result["status"], "eligible_next_load")
        self.assertIsNone(result["recommended_load"])

    def test_promoted_baseline_persists_through_later_one_rir_exposure(self) -> None:
        earlier = date(2026, 9, 1)
        sessions = [
            Session(earlier, "upper_body", "complete", "old"),
            *self.sessions(),
        ]
        records = [
            record("old", earlier, load=50, reps=10, set_number=1),
            record("old", earlier, load=50, reps=10, set_number=2),
            record("s1", self.day, load=50, reps=10, set_number=1, rir=1),
            record("s1", self.day, load=50, reps=10, set_number=2, rir=2),
        ]
        _, results = apply_progression(
            template(next_load=45), sessions, records, self.day
        )
        result = results["press"]
        self.assertEqual(result["status"], "hold_2_rir")
        self.assertEqual(result["current_load"], 50)
        self.assertIsNone(result["recommended_load"])

    def test_malformed_progression_is_rejected(self) -> None:
        bad = template()
        del bad["exercises"][0]["progression"]["sets"]
        with self.assertRaises(ProgressionError):
            apply_progression(bad, self.sessions(), [], self.day)

    def test_reported_aggregate_is_loaded_but_never_qualified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sets.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=(
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
                    ),
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "session_id": "s1",
                        "session_date": "2026-09-07",
                        "exercise": "press",
                        "equipment": "press_machine",
                        "set_number": "",
                        "set_count": "5",
                        "load_lb": "40",
                        "load_basis": "machine_stack",
                        "reps": "5",
                        "rir": "",
                        "form_quality": "not_reported",
                        "status": "reported_aggregate",
                        "notes": (
                            "Reported as 5x5; individual set completion not confirmed"
                        ),
                    }
                )
            rows = load_sets(path)
            self.assertIsNone(rows[0].set_number)
            result = self.apply(rows)
            self.assertEqual(result["status"], "no_comparable_data")


if __name__ == "__main__":
    unittest.main()
