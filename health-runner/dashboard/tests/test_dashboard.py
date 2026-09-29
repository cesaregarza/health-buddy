import json
import os
import csv
import subprocess
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import build_dashboard


class WeightSeriesTests(unittest.TestCase):
    def test_fills_historical_gaps_and_preserves_canonical_dates(self):
        source = [
            {"measured_at_local": "2026-09-03T09:00:00", "weight_lb": "199.6"},
            {"measured_at_local": "2026-09-05T09:00:00", "weight_lb": "200.6"},
        ]
        healthkit = [
            {"d": "2026-09-05", "lb": 999},
            {"d": "2026-09-04", "lb": 200.4},
            {"d": "2026-09-04", "lb": 200.4},
            {"d": "2026-09-02", "lb": 199.8},
            {"d": "2026-09-06", "lb": 200.0},
        ]
        with patch.object(build_dashboard, "rows", return_value=source):
            daily, averages = build_dashboard.weight_series(healthkit)
        self.assertEqual([p["d"] for p in daily], [
            "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06"
        ])
        self.assertEqual(daily[2], {"d": "2026-09-04", "lb": 200.4, "src": "healthkit"})
        self.assertEqual(daily[3]["lb"], 200.6)
        self.assertEqual(daily[3]["src"], "log")
        self.assertEqual(averages[0], {"d": "2026-09-04", "lb": 199.9})

    def test_repeated_canonical_readings_use_same_daily_median_as_model(self):
        source = [
            {"measured_at_local": "2026-09-03T08:00:00", "weight_lb": "199"},
            {"measured_at_local": "2026-09-03T08:01:00", "weight_lb": "201"},
        ]
        with patch.object(build_dashboard, "rows", return_value=source):
            daily, _ = build_dashboard.weight_series([])
        self.assertEqual(daily, [{"d": "2026-09-03", "lb": 200.0, "src": "log"}])

    def test_progress_model_receives_chart_dates_without_persisting_pending_rows(self):
        observed = {}

        def fake_run(argv, **kwargs):
            self.assertIn("--measurements-file", argv)
            path = Path(argv[argv.index("--measurements-file") + 1])
            with path.open(newline="", encoding="utf-8") as handle:
                observed["rows"] = list(csv.DictReader(handle))
            observed["path"] = path
            return subprocess.CompletedProcess(argv, 0, stdout='{"as_of":"2026-09-12"}', stderr="")

        with patch.object(build_dashboard.subprocess, "run", side_effect=fake_run), \
             patch.object(build_dashboard, "strength_progress", return_value={"exercises": []}):
            result = build_dashboard.progress_data(Path("/tmp/synthetic-repo"), [
                {"d": "2026-09-11", "lb": 199.0, "src": "log"},
                {"d": "2026-09-12", "lb": 198.2, "src": "healthkit"},
            ])
        self.assertEqual(result["weight"]["as_of"], "2026-09-12")
        self.assertEqual([r["weight_lb"] for r in observed["rows"]], ["199.0", "198.2"])
        self.assertFalse(observed["path"].exists())


class SourceIntegrityTests(unittest.TestCase):
    def test_builder_rejects_dirty_checkout_before_reading_health_data(self):
        status = subprocess.CompletedProcess([], 0, stdout=" M template.html\n", stderr="")
        with patch.object(build_dashboard.subprocess, "run", return_value=status):
            with self.assertRaisesRegex(RuntimeError, "dirty"):
                build_dashboard.builder_revision()

    @patch.dict(os.environ, {"HEALTH_ALLOW_LEGACY_RUNTIME": "1"})
    def test_main_rejects_different_source_and_data_commits(self):
        with patch.object(build_dashboard, "git_meta", return_value={"origin_full_sha": "a" * 40}), \
             patch.object(build_dashboard, "builder_revision", return_value="b" * 40), \
             patch.object(build_dashboard, "healthkit") as healthkit:
            with self.assertRaisesRegex(RuntimeError, "differs from origin"):
                build_dashboard.main()
        healthkit.assert_not_called()

    @patch.dict(os.environ, {"HEALTH_ALLOW_LEGACY_RUNTIME": ""})
    def test_unconfigured_main_fails_before_reading_sources(self):
        with patch.object(build_dashboard, "git_meta") as git_meta:
            with self.assertRaisesRegex(RuntimeError, "Legacy adapters"):
                build_dashboard.main()
        git_meta.assert_not_called()


class MedicationSourceTests(unittest.TestCase):
    @patch.dict(os.environ, {"HEALTH_TRACKED_MEDICATION": ""})
    def test_unconfigured_timeline_does_not_read_or_infer_regimen(self):
        with patch.object(build_dashboard, "rows") as rows:
            self.assertEqual(build_dashboard.injections(), [])
        rows.assert_not_called()

    @patch.dict(os.environ, {"HEALTH_TRACKED_MEDICATION": "Synthetic selection"})
    def test_explicit_timeline_uses_only_matching_recorded_doses(self):
        records = [
            {"event_date": "2030-01-02", "event_type": "dose_taken", "medication": "Synthetic selection", "injection_number": "1", "dose": "1", "dose_unit": "example"},
            {"event_date": "2030-01-03", "event_type": "dose_taken", "medication": "Other example"},
            {"event_date": "2030-01-04", "event_type": "planned", "medication": "Synthetic selection"},
        ]
        with patch.object(build_dashboard, "rows", return_value=records):
            self.assertEqual(build_dashboard.injections(), [{"d": "2030-01-02", "n": 1, "dose": "1 example"}])


class TileTests(unittest.TestCase):
    def test_maintenance_uses_recent_paired_energy_without_partial_today_or_workout_addition(self):
        as_of = date(2026, 9, 12)
        energy = [
            {"d": (date(2026, 8, 29) + timedelta(days=i)).isoformat(),
             "basal_kcal": 2200, "active_kcal": 550, "workout_kcal": 300}
            for i in range(14)
        ]
        energy.append({"d": "2026-09-12", "basal_kcal": 900, "active_kcal": 5000})
        tile = build_dashboard.maintenance_tile(energy, as_of)
        self.assertEqual(tile["value"], "2,750")
        self.assertIn("2,200 resting + 550 active", tile["context"])
        self.assertIn("14/14 days", tile["sub"])
        self.assertIn("not a food target", tile["sub"])

    def test_maintenance_suppresses_sparse_or_stale_energy(self):
        as_of = date(2026, 9, 12)
        sparse = [{"d": "2026-09-11", "basal_kcal": 2200, "active_kcal": 500}]
        self.assertEqual(build_dashboard.maintenance_tile(sparse, as_of)["value"], "—")
        stale = [{"d": (date(2026, 8, 29) + timedelta(days=i)).isoformat(),
                  "basal_kcal": 2200, "active_kcal": 500} for i in range(12)]
        self.assertEqual(build_dashboard.maintenance_tile(stale, as_of)["value"], "—")

    def test_maintenance_excludes_likely_partial_past_day(self):
        as_of = date(2026, 9, 12)
        energy = [{"d": date(2026, 9, i).isoformat(), "basal_kcal": 2200,
                   "active_kcal": 500} for i in range(1, 12)]
        energy.append({"d": "2026-08-31", "basal_kcal": 700, "active_kcal": 100})
        self.assertEqual(build_dashboard.maintenance_tile(energy, as_of)["value"], "—")

    def test_bp_tile_surfaces_recent_protocol_coverage_and_invalid_attempt(self):
        bp = [
            {"d": "2026-09-01", "sys": 126, "dia": 88, "session": "morning", "status": "valid"},
            {"d": "2026-09-01", "sys": 125, "dia": 89, "session": "morning", "status": "valid"},
            {"d": "2026-09-02", "sys": 100, "dia": 77, "session": "morning", "status": "valid"},
            {"d": "2026-09-02", "sys": 102, "dia": 84, "session": "morning", "status": "valid"},
            {"d": "2026-09-03", "sys": 110, "dia": 86, "session": "morning", "status": "invalid"},
        ]

        result = build_dashboard.tiles([], [], bp, [], [], [], [], as_of=date(2026, 9, 3))

        self.assertEqual(len(result), 1)
        tile = result[0]
        self.assertEqual(tile["value"], "101/80")
        self.assertEqual(tile["context"], "2 of 7 protocol-valid mornings")
        self.assertIn("latest attempt 2026-09-03 invalid", tile["sub"])


class HealthKitEnergyExtractionTests(unittest.TestCase):
    def test_energy_uses_latest_merged_aggregate_per_day(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "health.db"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE batches(received_at TEXT)")
                conn.execute("""CREATE TABLE records(
                    start_at TEXT, end_at TEXT, local_date TEXT, value_json TEXT,
                    deleted_at TEXT, type_identifier TEXT, record_kind TEXT,
                    unit TEXT, received_at TEXT, workout_json TEXT, source_json TEXT)""")
                for kind, value, received in (
                    ("Basal", 2200, "2026-09-12T10:00:00Z"),
                    ("Basal", 2300, "2026-09-12T11:00:00Z"),
                    ("Active", 600, "2026-09-12T11:00:00Z"),
                ):
                    conn.execute("""INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                 ("2026-09-11T05:00:00Z", "2026-09-12T05:00:00Z",
                                  "2026-09-11", str(value), None,
                                  f"HKQuantityTypeIdentifier{kind}EnergyBurned",
                                  "dailyAggregate", "kcal", received, None, None))
            result = subprocess.run(
                [sys.executable, "-c", build_dashboard.HK_SCRIPT.replace("__DB__", str(db))],
                capture_output=True, text=True, check=True,
            )
        self.assertEqual(json.loads(result.stdout)["energy"], [
            {"d": "2026-09-11", "basal_kcal": 2300.0, "active_kcal": 600.0}
        ])


class HealthKitSleepSourceTests(unittest.TestCase):
    def test_watch_and_pillow_are_distinct_and_evening_stages_join_wake_date(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "health.db"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE batches(received_at TEXT)")
                conn.execute("""CREATE TABLE records(
                    start_at TEXT, end_at TEXT, local_date TEXT, value_json TEXT,
                    deleted_at TEXT, type_identifier TEXT, record_kind TEXT,
                    unit TEXT, received_at TEXT, workout_json TEXT, source_json TEXT)""")
                sleep_type = "HKCategoryTypeIdentifierSleepAnalysis"
                for start, end, source in (
                    ("2026-09-18T04:00:00Z", "2026-09-18T06:00:00Z", "health-buddy’s Apple\u00a0Watch"),
                    ("2026-09-18T05:00:00Z", "2026-09-18T11:00:00Z", "health-buddy’s Apple\u00a0Watch"),
                    ("2026-09-18T04:00:00Z", "2026-09-18T10:00:00Z", "Pillow"),
                    ("2026-09-19T04:00:00Z", "2026-09-19T10:00:00Z", "Pillow"),
                ):
                    conn.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                        start, end, None, json.dumps("asleepCore"), None,
                        sleep_type, "category", None, None, None,
                        json.dumps({"name": source}),
                    ))
            result = subprocess.run(
                [sys.executable, "-c", build_dashboard.HK_SCRIPT.replace("__DB__", str(db))],
                capture_output=True, text=True, check=True,
            )
        hk = json.loads(result.stdout)
        self.assertEqual(hk["sleep"], [
            {"d": "2026-09-18", "hours": 7.0, "src": "Apple Watch"},
            {"d": "2026-09-19", "hours": 6.0, "src": "Pillow"},
        ])
        self.assertEqual(hk["sleep_sources"], [
            {"d": "2026-09-18", "src": "Apple Watch", "hours": 7.0,
             "bed": -1.0, "wake": 6.0, "span_hours": 7.0},
            {"d": "2026-09-18", "src": "Pillow", "hours": 6.0,
             "bed": -1.0, "wake": 5.0, "span_hours": 6.0},
            {"d": "2026-09-19", "src": "Pillow", "hours": 6.0,
             "bed": -1.0, "wake": 5.0, "span_hours": 6.0},
        ])
        self.assertEqual(hk["sleep_spans"][0]["src"], "Apple Watch")
        self.assertEqual(hk["sleep_spans"][0]["bed"], -1.0)

    def test_checkin_wins_without_relabeling_pillow_as_watch(self):
        with patch.object(build_dashboard, "rows", return_value=[
            {"date": "2026-09-18", "sleep_hours": "8"},
        ]):
            series = build_dashboard.sleep_series([
                {"d": "2026-09-18", "hours": 7, "src": "Apple Watch"},
                {"d": "2026-09-19", "hours": 6, "src": "Pillow"},
            ])
        self.assertEqual(series, [
            {"d": "2026-09-18", "hours": 8.0, "src": "checkin"},
            {"d": "2026-09-19", "hours": 6, "src": "Pillow"},
        ])


class StrengthProgressTests(unittest.TestCase):
    def test_nominal_per_hand_totals_do_not_merge_with_stack_basis(self):
        source = [
            {"session_id": "a", "session_date": "2026-08-01", "exercise": "Chest press", "load_lb": "30", "load_basis": "per_hand", "reps": "12", "rir": "3", "status": "completed", "notes": "Working set"},
            {"session_id": "a", "session_date": "2026-08-01", "exercise": "Chest press", "load_lb": "40", "load_basis": "per_hand", "reps": "8", "rir": "4", "status": "completed", "notes": "Warm-up set"},
            {"session_id": "b", "session_date": "2026-09-01", "exercise": "chest_press", "load_lb": "70", "load_basis": "machine_stack_total", "reps": "10", "rir": "2", "status": "completed", "notes": "backoff_set"},
        ]

        with patch.object(build_dashboard, "rows", return_value=source):
            result = build_dashboard.strength_progress()

        series = result["exercises"]
        self.assertEqual(len(series), 2)
        self.assertEqual({item["key"] for item in series}, {"chest_press", "chest_press:per_hand"})
        self.assertEqual(sorted(item["latest"]["load"] for item in series), [60.0, 70.0])
        self.assertTrue(all(item["unit"] == "lb total" for item in series))
        self.assertTrue(all(item["sessions"] == 1 for item in series))


class TrainingDetailTests(unittest.TestCase):
    def test_live_prescription_replaces_stale_same_day_snapshot_and_adds_next_strength(self):
        def recommendation(argv, **kwargs):
            target = argv[argv.index("--date") + 1]
            next_strength = "2026-09-14" if target == "2026-09-12" else target
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({
                "date": target, "next_strength_date": next_strength, "status": "scheduled",
            }), stderr="")

        stale = {"schema_version": 1, "date": "2026-09-12",
                 "recommendation": {"date": "2026-09-12", "status": "stale"}}
        with patch.object(build_dashboard, "git_paths", return_value=["plans/training-days/2026-09-12.json"]), \
             patch.object(build_dashboard, "git_show", return_value=json.dumps(stale)), \
             patch.object(build_dashboard.subprocess, "run", side_effect=recommendation):
            result = build_dashboard.training_prescriptions(Path("/tmp/synthetic-repo"), date(2026, 9, 12))

        self.assertEqual([x["date"] for x in result["snapshots"]], ["2026-09-12", "2026-09-14"])
        self.assertTrue(all(x["live"] for x in result["snapshots"]))
        self.assertEqual(result["snapshots"][0]["recommendation"]["status"], "scheduled")

    def test_builds_completed_session_history_with_sets_and_cardio(self):
        sources = {
            "data/sets.csv": [
                {
                    "session_id": "lower-1",
                    "exercise": "leg_press",
                    "equipment": "leg_press_machine",
                    "set_number": "1",
                    "set_count": "",
                    "load_lb": "140",
                    "load_basis": "machine_stack",
                    "reps": "12",
                    "rir": "2",
                    "form_quality": "clean",
                    "status": "completed",
                    "notes": "",
                }
            ],
            "data/cardio.csv": [
                {
                    "session_id": "lower-1",
                    "activity": "treadmill_warmup",
                    "equipment": "treadmill",
                    "segment_number": "1",
                    "duration_seconds": "480",
                    "speed_mph": "3",
                    "incline_percent": "0",
                }
            ],
            "data/sessions.csv": [
                {
                    "session_id": "lower-1",
                    "date": "2026-09-08",
                    "workout_type": "lower_body",
                    "status": "complete",
                    "notes": "Good session.",
                }
            ],
        }
        daily = [
            {
                "d": "2026-09-08",
                "minutes": {"lower": 43},
                "sessions": [
                    {"id": "lower-1", "type": "lower", "min": 43, "src": "logged"}
                ],
            }
        ]
        with (
            patch.object(build_dashboard, "rows", side_effect=lambda path: sources[path]),
            patch.object(
                build_dashboard,
                "training_prescriptions",
                return_value={"snapshots": [], "errors": []},
            ),
        ):
            result = build_dashboard.training_detail([], daily)

        self.assertEqual(result["days"][0]["total_minutes"], 43)
        session = result["days"][0]["sessions"][0]
        self.assertEqual(session["sets"][0]["load_lb"], 140.0)
        self.assertEqual(session["cardio"][0]["duration_seconds"], 480.0)

    def test_loads_valid_committed_training_snapshot(self):
        snapshot = {
            "schema_version": 1,
            "date": "2026-09-10",
            "recommendation": {"date": "2026-09-10", "status": "scheduled"},
        }
        with (
            patch.object(
                build_dashboard,
                "git_paths",
                return_value=["plans/training-days/2026-09-10.json"],
            ),
            patch.object(build_dashboard, "git_show", return_value=json.dumps(snapshot)),
        ):
            result = build_dashboard.training_prescriptions()

        self.assertEqual(result["errors"], [])
        self.assertEqual(result["snapshots"][0]["date"], "2026-09-10")


class TemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (Path(__file__).parents[1] / "template.html").read_text()

    def test_generated_private_snapshot_is_not_embedded_in_source(self):
        self.assertIn("const DATA = /*__DATA__*/;", self.template)

    def test_lab_and_clinician_copy_avoid_false_precision(self):
        self.assertIn("not flagged", self.template)
        self.assertNotIn("expected the week of Sep 7", self.template)
        self.assertIn("Supporting context", self.template)

    def test_progress_workspace_distinguishes_inferred_measured_and_projected(self):
        self.assertIn('data-tab="progress"', self.template)
        self.assertIn('id="forecast-model"', self.template)
        self.assertIn('id="forecast-model-control"', self.template)
        self.assertIn("first_7_day_average_crossing_below", self.template)
        self.assertIn("Full weight journey", self.template)
        self.assertIn("Inferred history and future scenarios", self.template)
        self.assertIn("forward_30_day_robust", self.template)
        self.assertIn("Strength exercise", self.template)

    def test_training_workspace_has_date_navigation_and_history(self):
        self.assertIn('data-tab="training"', self.template)
        self.assertIn('id="training-date"', self.template)
        self.assertIn('id="training-history"', self.template)
        self.assertIn("Next published session", self.template)


if __name__ == "__main__":
    unittest.main()
