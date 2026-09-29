import sys
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from datetime import date, datetime, datetime as RealDateTime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_dashboard


class DashboardUsabilityDataTests(unittest.TestCase):
    def test_london_tomorrow_can_still_be_chicago_today(self):
        london = datetime(2026, 9, 29, 0, 30, tzinfo=ZoneInfo("Europe/London"))
        self.assertEqual(build_dashboard.chicago_date(london), date(2026, 9, 28))

    def test_intake_counts_missing_fat_and_distinguishes_partial_from_unknown(self):
        source = [
            {"event_at_local": "2026-09-27T12:00:00", "status": "consumed", "calories_kcal": "0", "protein_g": "0", "carbohydrate_g": "0", "fat_g": "0", "sodium_mg": "0"},
            {"event_at_local": "2026-09-27T13:00:00", "status": "consumed", "calories_kcal": "", "protein_g": "", "carbohydrate_g": "", "fat_g": "", "sodium_mg": ""},
            {"event_at_local": "2026-09-28T12:00:00", "status": "consumed", "calories_kcal": "", "protein_g": "", "carbohydrate_g": "", "fat_g": "", "sodium_mg": ""},
        ]
        with patch.object(build_dashboard, "rows", return_value=source):
            result = build_dashboard.intake_daily()
        self.assertEqual(result[0]["fat"], 0)
        self.assertEqual(result[0]["missing_fat"], 1)
        self.assertEqual(result[0]["n"], 2)
        self.assertEqual(result[1]["missing_fat"], 1)
        self.assertEqual(result[1]["fat"], 0)

    def test_body_composition_preserves_units_and_excludes_future_or_nonfinite_rows(self):
        source = [
            {"scan_date": "2026-09-24", "scan_id": "scan-1", "device": "DXA", "measure": "fat_mass", "region": "total", "value": "42.5", "unit": "lb"},
            {"scan_date": "2026-09-24", "scan_id": "scan-1", "device": "DXA", "measure": "body_fat", "region": "total", "value": "31.2", "unit": "%"},
            {"scan_date": "2026-09-29", "scan_id": "scan-future", "device": "DXA", "measure": "fat_mass", "region": "total", "value": "40", "unit": "lb"},
            {"scan_date": "2026-09-20", "scan_id": "scan-bad", "device": "DXA", "measure": "fat_mass", "region": "total", "value": "inf", "unit": "lb"},
        ]
        with patch.object(build_dashboard, "rows", return_value=source):
            result = build_dashboard.body_composition_data(date(2026, 9, 28))
        self.assertEqual(len(result["scans"]), 1)
        self.assertEqual(result["scans"][0]["measures"][0]["unit"], "%")
        self.assertEqual(result["scans"][0]["measures"][1]["unit"], "lb")
        self.assertEqual(result["scans"][0]["measures"][1]["value"], 42.5)

    def test_tape_dates_convert_to_chicago_and_invalid_future_values_are_filtered(self):
        data = {
            "data/waist.csv": [
                {"measured_at_local": "2026-09-29T00:30:00+01:00", "waist_in": "34", "measurement_site": "navel"},
                {"measured_at_local": "2026-09-29T02:00:00Z", "waist_in": "33", "measurement_site": "navel"},
                {"measured_at_local": "not-a-date", "waist_in": "35", "measurement_site": "navel"},
                {"measured_at_local": "2026-09-28", "waist_in": "0", "measurement_site": "navel"},
                {"measured_at_local": "2026-09-29", "waist_in": "36", "measurement_site": "navel"},
            ],
            "data/body_circumferences.csv": [],
        }
        with patch.object(build_dashboard, "rows", side_effect=lambda path: data[path]):
            result = build_dashboard.tape_data(date(2026, 9, 28))
        self.assertEqual(
            result["waist"],
            [
                {"d": "2026-09-28", "in": 34.0, "site": "navel"},
                {"d": "2026-09-28", "in": 33.0, "site": "navel"},
            ],
        )

    @patch.dict(os.environ, {"HEALTH_ALLOW_LEGACY_RUNTIME": "1"})
    def test_main_passes_one_chicago_date_to_all_date_sensitive_builders(self):
        fixed = RealDateTime(2026, 9, 28, 12, 0, tzinfo=ZoneInfo("America/Chicago"))

        class FrozenDateTime(RealDateTime):
            @classmethod
            def now(cls, tz=None):
                return fixed if tz is None else fixed.astimezone(tz)

        observed = {}

        def capture(name, result=None):
            def call(*args, **kwargs):
                observed[name] = (args, kwargs)
                return result
            return call

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template = root / "template.html"
            template.write_text("<script>/*__DATA__*/</script>", encoding="utf-8")
            out_dir = root / "web"
            out = out_dir / "index.html"
            meta = {"origin_full_sha": "a" * 40, "origin_sha": "a" * 7,
                    "origin_committed": "2026-09-28T12:00:00-05:00", "origin_subject": "test"}
            hk = {"sleep": [], "rhr": [], "steps": [], "last_batch": None,
                  "bodymass": [], "workouts": [], "hrv": [], "energy": []}
            with ExitStack() as stack:
                patches = {
                    "datetime": FrozenDateTime,
                    "git_meta": capture("git_meta", meta),
                    "builder_revision": capture("builder_revision", "a" * 40),
                    "healthkit": capture("healthkit", hk),
                    "weight_series": capture("weight_series", ([], [])),
                    "bp_series": capture("bp_series", []),
                    "sleep_series": capture("sleep_series", []),
                    "injections": capture("injections", []),
                    "training_daily": capture("training_daily", []),
                    "canonical_snapshot": lambda: nullcontext(root),
                    "progress_data": capture("progress_data", {"strength": {"exercises": []}}),
                    "visit_data": capture("visit_data", {}),
                    "training_prescriptions": capture("training_prescriptions", {}),
                    "tiles": capture("tiles", []),
                    "training_detail": capture("training_detail", {}),
                    "tracking_schedule": capture("tracking_schedule", []),
                    "rows": capture("rows", []),
                    "git_paths": capture("git_paths", []),
                    "intake_daily": capture("intake_daily", []),
                    "labs_data": capture("labs_data", {"analytes": [], "dates": [], "provisional_dates": []}),
                    "visit_outcomes": capture("visit_outcomes", {}),
                    "profile_data": capture("profile_data", {}),
                    "tape_data": capture("tape_data", {}),
                    "body_composition_data": capture("body_composition_data", {"scans": []}),
                    "presentation_data": capture("presentation_data"),
                    "OUT_DIR": out_dir,
                    "OUT": out,
                    "TEMPLATE": template,
                    "ASSETS": root / "assets",
                }
                for name, replacement in patches.items():
                    stack.enter_context(patch.object(build_dashboard, name, replacement))
                self.assertEqual(build_dashboard.main(), 0)

        expected = date(2026, 9, 28)
        self.assertEqual(observed["visit_data"][0][1], expected)
        self.assertEqual(observed["training_prescriptions"][0][1], expected)
        self.assertEqual(observed["tiles"][1]["as_of"], expected)
        self.assertEqual(observed["tracking_schedule"][0][2], expected)
        self.assertEqual(observed["presentation_data"][0][1], expected)
        self.assertEqual(observed["tape_data"][0][0], expected)
        self.assertEqual(observed["body_composition_data"][0][0], expected)
