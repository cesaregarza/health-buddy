"""scripts/doctor_note.py: run only by legacy build_dashboard.py main (visit_data)."""

import csv
import tempfile
import unittest
from datetime import date, datetime as RealDateTime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts.doctor_note import DataError, build_digest, render_markdown, working_set


def write_csv(
    path: Path, headers: tuple[str, ...], rows: list[tuple[object, ...]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


class DoctorNoteTests(unittest.TestCase):
    def test_working_set_note_may_mention_an_omitted_practice_set(self) -> None:
        self.assertTrue(
            working_set(
                {"status": "completed", "notes": "Primary working set; practice set omitted"}
            )
        )
        self.assertFalse(working_set({"status": "completed", "notes": "Practice set"}))

    def write_fixture(self, root: Path, *, include_appointment: bool = True) -> None:
        data = root / "data"
        data.mkdir()
        appointments = []
        if include_appointment:
            appointments = [
                (
                    "visit-1",
                    "2026-07-15",
                    "Dr Example",
                    "primary care",
                    "follow_up",
                    "Blood pressure",
                    "completed",
                    "",
                    "user_reported",
                    "",
                ),
                (
                    "visit-future",
                    "2026-09-10",
                    "Dr Example",
                    "primary care",
                    "follow_up",
                    "",
                    "planned",
                    "",
                    "user_reported",
                    "",
                ),
            ]
        write_csv(
            data / "appointments.csv",
            (
                "appointment_id",
                "appointment_date",
                "provider",
                "specialty",
                "visit_type",
                "reason",
                "status",
                "outcome_notes",
                "source",
                "notes",
            ),
            appointments,
        )
        write_csv(
            data / "clinical_instructions.csv",
            (
                "instruction_id",
                "appointment_id",
                "instruction_date",
                "instruction",
                "status",
                "source",
                "notes",
            ),
            [
                (
                    "instruction-1",
                    "visit-1",
                    "2026-07-15",
                    "Record home blood pressure",
                    "active",
                    "user_reported",
                    "",
                ),
            ],
        )
        write_csv(
            data / "clinician_questions.csv",
            (
                "question_id",
                "recorded_on",
                "question",
                "status",
                "topic",
                "source",
                "notes",
            ),
            [
                (
                    "question-1",
                    "2026-08-02",
                    "Could we review my home blood-pressure pattern?",
                    "active",
                    "blood_pressure",
                    "user_reported",
                    "",
                ),
                (
                    "context-1",
                    "2026-08-03",
                    "Context, not a question: representative interval note.",
                    "context",
                    "history",
                    "user_reported",
                    "",
                ),
            ],
        )
        write_csv(
            data / "conditions.csv",
            (
                "condition_id",
                "condition_name",
                "category",
                "status",
                "onset_date",
                "onset_age",
                "resolved_date",
                "source",
                "notes",
            ),
            [
                (
                    "kidney-stone",
                    "Kidney stone history",
                    "medical_history",
                    "historical",
                    "",
                    "19",
                    "",
                    "user_reported",
                    "",
                )
            ],
        )
        write_csv(
            data / "medications.csv",
            (
                "medication_id",
                "medication_name",
                "active_ingredient",
                "formulation",
                "strength_value",
                "strength_unit",
                "dose_form",
                "route",
                "frequency",
                "status",
                "prescribed_date",
                "start_date",
                "start_date_basis",
                "end_date",
                "indication",
                "prescriber",
                "source",
                "label_text",
                "notes",
            ),
            [
                (
                    "Example medication",
                    "Example medication",
                    "example-ingredient",
                    "",
                    "2.5",
                    "mg",
                    "injection",
                    "subcutaneous",
                    "weekly",
                    "active",
                    "2026-07-10",
                    "2026-07-11",
                    "Started after prescription and before follow-up.",
                    "",
                    "",
                    "",
                    "user_reported",
                    "",
                    "",
                )
            ],
        )
        write_csv(
            data / "medication_events.csv",
            (
                "event_date",
                "event_time_local",
                "timezone",
                "medication",
                "event_type",
                "injection_number",
                "dose",
                "dose_unit",
                "route",
                "side_effects",
                "source",
                "notes",
            ),
            [
                (
                    "2026-08-08",
                    "",
                    "",
                    "Example medication",
                    "dose_taken",
                    "8",
                    "2.5",
                    "mg",
                    "",
                    "",
                    "user_reported",
                    "Backfilled from later confirmation; zero missed doses",
                )
            ],
        )
        write_csv(
            data / "blood_pressure.csv",
            (
                "measured_at_local",
                "timezone",
                "systolic_mm_hg",
                "diastolic_mm_hg",
                "pulse_bpm",
                "arm",
                "reading_number",
                "measurement_session",
                "device",
                "source",
                "notes",
                "protocol_status",
                "protocol_notes",
            ),
            [
                (
                    "2026-08-02T08:00:00",
                    "",
                    "120",
                    "80",
                    "70",
                    "",
                    "1",
                    "morning",
                    "",
                    "user_reported",
                    "",
                    "valid",
                    "All conditions documented",
                ),
                (
                    "2026-08-02T08:01:00",
                    "",
                    "122",
                    "78",
                    "72",
                    "",
                    "2",
                    "morning",
                    "",
                    "user_reported",
                    "",
                    "valid",
                    "All conditions documented",
                ),
                (
                    "2026-08-03T18:00:00",
                    "",
                    "130",
                    "85",
                    "",
                    "",
                    "1",
                    "other",
                    "",
                    "user_reported",
                    "After stairs",
                    "invalid",
                    "Recent exercise",
                ),
            ],
        )
        weights = []
        start = date(2026, 8, 1)
        for offset in range(14):
            day = start + timedelta(days=offset)
            weights.append(
                (
                    f"{day.isoformat()}T08:00:00",
                    "",
                    str(210 - offset),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "test",
                    "",
                )
            )
        write_csv(
            data / "measurements.csv",
            (
                "measured_at_local",
                "timezone",
                "weight_lb",
                "body_fat_pct",
                "muscle_mass_pct",
                "water_pct",
                "bmi",
                "bone_mass_pct",
                "source",
                "notes",
            ),
            weights,
        )
        write_csv(
            data / "daily_checkins.csv",
            (
                "date",
                "sleep_hours",
                "sleep_quality_1_5",
                "energy_1_5",
                "soreness_0_10",
                "appetite_1_5",
                "physical_hunger_0_10",
                "food_noise_0_10",
                "nausea_0_10",
                "other_symptoms",
                "source",
                "notes",
            ),
            [
                (
                    "2026-08-05",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "Brief positional wooziness",
                    "user_reported",
                    "Resolved within seconds; no blackout",
                )
            ],
        )
        write_csv(
            data / "sessions.csv",
            (
                "session_id",
                "date",
                "workout_type",
                "status",
                "duration_min",
                "bodyweight_lb",
                "notes",
            ),
            [
                ("upper-1", "2026-08-01", "upper_body", "complete", "", "", ""),
                ("upper-2", "2026-08-08", "upper_body", "complete", "", "", ""),
                ("cardio-1", "2026-08-10", "cardio", "complete", "30", "", ""),
                (
                    "shuffle-1",
                    "2026-08-11",
                    "shuffle_practice",
                    "complete",
                    "15",
                    "",
                    "Week 1 fundamentals",
                ),
            ],
        )
        write_csv(
            data / "sets.csv",
            (
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
            [
                (
                    "upper-1",
                    "2026-08-01",
                    "Chest press",
                    "machine",
                    "1",
                    "",
                    "30",
                    "per_hand",
                    "10",
                    "2",
                    "clean",
                    "completed",
                    "",
                ),
                (
                    "upper-2",
                    "2026-08-08",
                    "Chest press",
                    "machine",
                    "1",
                    "",
                    "40",
                    "per_hand",
                    "10",
                    "2",
                    "clean",
                    "completed",
                    "",
                ),
            ],
        )
        write_csv(
            data / "labs.csv",
            (
                "collected_on",
                "test_name",
                "value",
                "unit",
                "reference_low",
                "reference_high",
                "flag",
                "fasting",
                "source",
                "notes",
            ),
            [
                (
                    "2026-08-12",
                    "Example lab",
                    "5",
                    "unit",
                    "1",
                    "10",
                    "",
                    "yes",
                    "test",
                    "",
                )
            ],
        )

    def test_digest_uses_last_completed_visit_and_valid_bp_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            digest = build_digest(root, since="last-visit", through=date(2026, 8, 14))

        self.assertEqual(digest["interval"]["start_inclusive"], "2026-07-15")
        self.assertEqual(digest["interval"]["anchor"]["appointment_id"], "visit-1")
        self.assertEqual(digest["audience"], "existing-clinician")
        average = digest["home_blood_pressure"]["protocol_valid_average"]
        self.assertIsNotNone(average)
        assert average is not None
        self.assertEqual(average["systolic_mm_hg"], 121)
        self.assertEqual(average["diastolic_mm_hg"], 79)
        self.assertEqual(digest["home_blood_pressure"]["status_counts"]["invalid"], 1)
        self.assertEqual(
            {item["status"] for item in digest["patient_questions"]},
            {"active", "context"},
        )
        self.assertNotIn(
            "Context, not a question: representative interval note.",
            digest["questions_for_clinician"],
        )

    def test_carried_questions_survive_later_visit_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            write_csv(
                root / "data" / "clinician_questions.csv",
                ("question_id", "recorded_on", "question", "status", "source", "notes"),
                [
                    ("carry", "2026-07-01", "Still unanswered?", "carried", "user", ""),
                    ("done", "2026-08-02", "Already answered?", "answered", "user", ""),
                    ("info", "2026-08-02", "Context only", "context", "user", ""),
                ],
            )
            digest = build_digest(root, since="2026-08-01", through=date(2026, 8, 14))
        self.assertIn("Still unanswered?", digest["questions_for_clinician"])
        self.assertNotIn("Already answered?", digest["questions_for_clinician"])
        self.assertNotIn("Context only", digest["questions_for_clinician"])

    def test_prepared_questions_match_note_including_carried_pending_and_generated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            write_csv(
                root / "data" / "clinician_questions.csv",
                ("question_id", "recorded_on", "question", "status", "topic", "source", "notes"),
                [
                    ("active", "2026-08-02", "Review the home pattern?", "active", "bp", "user", ""),
                    ("duplicate", "2026-08-03", "Review the home pattern?", "carried", "bp", "user", ""),
                    ("context", "2026-08-03", "Context only", "context", "history", "user", ""),
                ],
            )
            digest = build_digest(
                root,
                since="2026-08-01",
                through=date(2026, 8, 14),
                additional_questions=[{
                    "question_id": "pending-1", "recorded_on": "2026-08-05",
                    "question": "Ask about the pending item?", "status": "pending",
                    "topic": "follow_up", "source": "notes", "notes": "provisional",
                }],
            )
            rendered = render_markdown(digest)

        prepared = digest["prepared_questions"]
        note_section = rendered.split("## Questions for the clinician\n\n", 1)[1]
        rendered_questions = [
            line[2:].split("] ", 1)[-1]
            for line in note_section.splitlines()
            if line.startswith("- ")
        ]
        self.assertEqual(rendered_questions, [item["question"] for item in prepared])
        self.assertIn("[not yet in the log] Ask about the pending item?", note_section)
        self.assertIn("[Suggested discussion]", note_section)
        self.assertEqual(len(rendered_questions), len(set(rendered_questions)))
        self.assertEqual(rendered_questions[:2], ["Review the home pattern?", "Ask about the pending item?"])
        self.assertNotIn("Context only", rendered_questions)
        pending = next(item for item in prepared if item["question_id"] == "pending-1")
        self.assertTrue(pending["prov"])
        self.assertEqual(pending["origin"], "pending")
        generated = [item for item in prepared if item["origin"] == "generated"]
        self.assertTrue(generated)
        self.assertTrue(all(item["notes"] for item in generated))
        self.assertTrue(all(item["prov"] is False for item in generated))
        self.assertEqual(set(prepared[0]), {"question_id", "question", "status", "topic", "notes", "prov", "origin"})

    def test_generated_date_is_today_in_chicago_and_carried_question_is_preserved(self) -> None:
        class FrozenDateTime:
            @staticmethod
            def fromisoformat(value):
                return RealDateTime.fromisoformat(value)

            @staticmethod
            def now(tz=None):
                local = RealDateTime(2026, 9, 28, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
                return local if tz is None or str(tz) == "America/Chicago" else local.astimezone(tz)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            write_csv(
                root / "data" / "clinician_questions.csv",
                ("question_id", "recorded_on", "question", "status", "topic", "source", "notes"),
                [("carry", "2026-07-01", "Still carried?", "carried", "follow_up", "user", "")],
            )
            with patch("scripts.doctor_note.datetime", FrozenDateTime):
                digest = build_digest(root, since="2026-08-01", through=date(2026, 8, 14))

        self.assertEqual(digest["generated_on"], "2026-09-28")
        self.assertEqual(digest["interval"]["end_inclusive"], "2026-08-14")
        carried = next(item for item in digest["prepared_questions"] if item["question_id"] == "carry")
        self.assertEqual(carried["status"], "carried")
        self.assertFalse(carried["prov"])

    def test_digest_preserves_provenance_and_flags_symptom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            digest = build_digest(root, since="2026-08-01", through=date(2026, 8, 14))

        event = digest["medications"]["observed_events"][0]
        active_medication = digest["medications"]["active_registry"][0]
        self.assertEqual(active_medication["prescribed_date"], "2026-07-10")
        self.assertEqual(active_medication["start_date"], "2026-07-11")
        self.assertIn("after prescription", active_medication["start_date_basis"])
        self.assertTrue(event["logged_retrospectively"])
        self.assertTrue(event["reported_zero_missed_doses"])
        self.assertTrue(digest["symptoms_and_episodes"][0]["prominent"])
        self.assertEqual(digest["training"]["strength_sessions"], 2)
        self.assertEqual(digest["training"]["strength_coverage_start"], "2026-08-01")
        self.assertEqual(digest["training"]["strength_sessions_per_active_week"], 1.0)
        self.assertEqual(digest["training"]["dedicated_aerobic_sessions"], 1)
        self.assertEqual(digest["training"]["dedicated_aerobic_minutes"], 30)
        self.assertEqual(digest["training"]["skill_practice_sessions"], 1)
        self.assertEqual(digest["training"]["skill_practice_minutes"], 15)
        comparison = digest["training"]["repeated_exercise_comparisons"][0]
        self.assertEqual(comparison["first"]["load_lb"], 30)
        self.assertEqual(comparison["latest"]["load_lb"], 40)
        self.assertEqual(len(digest["labs"]), 1)

    def test_markdown_is_a_patient_prepared_recall_aid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            digest = build_digest(root, since="2026-08-01", through=date(2026, 8, 14))
            rendered = render_markdown(digest)

        self.assertIn("Patient-prepared since-last-visit update", rendered)
        self.assertIn(
            "Established clinician with access to the existing chart", rendered
        )
        self.assertIn("not a diagnosis or treatment plan", rendered)
        self.assertIn("**Prominent:**", rendered)
        self.assertIn("full current medication list remains", rendered)
        self.assertIn("injection #8", rendered)
        self.assertIn("reported zero missed doses through injection #8", rendered)
        self.assertIn("not lifetime dose counts", rendered)
        self.assertIn("observed weight-coverage pace", rendered)
        self.assertIn("first interval strength session on 2026-08-01", rendered)
        self.assertIn("1 shuffle session", rendered)
        self.assertIn("not automatically moderate aerobic work", rendered)
        self.assertNotIn("Kidney stone history", rendered)
        self.assertNotIn("Portal prescription/order date", rendered)
        self.assertIn("Questions for the clinician", rendered)
        self.assertIn("Could we review my home blood-pressure pattern?", rendered)
        self.assertNotIn("Context: Context:", rendered)

    def test_new_labs_collapse_normal_rows_and_show_headline_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            write_csv(
                root / "data" / "labs.csv",
                (
                    "collected_on",
                    "test_name",
                    "value",
                    "unit",
                    "reference_low",
                    "reference_high",
                    "flag",
                    "fasting",
                    "source",
                    "notes",
                ),
                [
                    ("2026-05-27", "hemoglobin A1C", "6.5", "%", "", "", "Above High Normal", "yes", "test", ""),
                    ("2026-05-27", "glucose", "115", "mg/dL", "", "", "Above High Normal", "yes", "test", ""),
                    ("2026-09-01", "hemoglobin A1C", "5.2", "%", "", "", "Normal", "yes", "test", ""),
                    ("2026-09-01", "glucose", "78", "mg/dL", "", "", "Normal", "yes", "test", ""),
                    ("2026-09-01", "ALT (SGPT)", "60", "IU/L", "", "", "High", "yes", "test", ""),
                    ("2026-09-01", "WBC", "6.1", "x10e3/uL", "", "", "Normal", "yes", "test", ""),
                ],
            )
            digest = build_digest(
                root, since="2026-08-01", through=date(2026, 9, 3)
            )
            rendered = render_markdown(digest)

        summary = digest["lab_note_summary"]
        self.assertEqual([row["test_name"] for row in summary["out_of_range"]], ["ALT (SGPT)"])
        a1c = next(item for item in summary["headline_deltas"] if item["label"] == "A1c")
        self.assertAlmostEqual(a1c["delta"], -1.3)
        self.assertIn("A1c: 6.5 % on 2026-05-27 -> 5.2 % on 2026-09-01", rendered)
        self.assertIn("ALT (SGPT) 60 IU/L [High]", rendered)
        self.assertIn("1 additional interval results", rendered)
        self.assertNotIn("2026-09-01: WBC", rendered)

    def test_new_clinician_mode_includes_transfer_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            digest = build_digest(
                root,
                since="2026-08-01",
                through=date(2026, 8, 14),
                audience="new-clinician",
            )
            rendered = render_markdown(digest)

        self.assertIn("Patient-prepared transfer summary", rendered)
        self.assertIn("New clinician or transfer of care", rendered)
        self.assertIn("Relevant standing history: Kidney stone history", rendered)
        self.assertIn("Medications as logged", rendered)
        self.assertIn("Portal prescription/order date 2026-07-10", rendered)

    def test_digest_rejects_unknown_audience(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root)
            with self.assertRaisesRegex(DataError, "invalid audience"):
                build_digest(
                    root,
                    since="2026-08-01",
                    through=date(2026, 8, 14),
                    audience="wrong-doctor",
                )

    def test_last_visit_refuses_to_infer_an_appointment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_fixture(root, include_appointment=False)
            with self.assertRaisesRegex(DataError, "no completed appointment"):
                build_digest(root, since="last-visit", through=date(2026, 8, 14))


if __name__ == "__main__":
    unittest.main()
