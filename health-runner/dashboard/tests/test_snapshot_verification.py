from datetime import datetime, timezone, timedelta
import json
import subprocess
from pathlib import Path
import unittest

from scripts.verify_snapshot import summarize


class SnapshotVerificationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
        self.sha = "a" * 40
        self.data = {
            "meta": {"built_at": self.now.isoformat(),
                     "origin_full_sha": self.sha, "builder_sha": self.sha},
            "weight": [{"d": "2026-01-05", "lb": 150}, {"d": "2026-01-04", "lb": 151}],
            "progress": {"weight": {"as_of": "2026-01-05", "weight": {"latest_measured_weight_lb": 150}}},
            "training_detail": {"prescriptions": [{"date": "2026-01-05", "live": True}]},
        }

    def html(self):
        return "<script>const DATA = " + json.dumps(self.data) + ";</script>"

    def test_latest_and_missing_optional_series(self):
        result = summarize(self.html(), self.now, expected_sha=self.sha)
        self.assertEqual(result["latest_weight"]["lb"], 150)
        self.assertIsNone(result["latest_sleep"])
        self.assertEqual(result["progress_as_of"], "2026-01-05")
        self.assertEqual(result["live_training_dates"], ["2026-01-05"])

    def test_maintenance_tile_is_checked_when_energy_data_is_present(self):
        self.data["energy"] = []
        with self.assertRaisesRegex(ValueError, "missing its maintenance"):
            summarize(self.html(), self.now)
        self.data["tiles"] = [{"label": "Maintenance estimate", "value": "2,750"}]
        result = summarize(self.html(), self.now)
        self.assertEqual(result["maintenance_estimate_kcal_per_day"], "2,750")

    def test_current_prescription_uses_declared_snapshot_timezone(self):
        now = datetime(2026, 1, 6, 1, tzinfo=timezone.utc)
        self.data["meta"].update(built_at=now.isoformat(), tz="America/Los_Angeles")
        result = summarize(self.html(), now)
        self.assertEqual(result["live_training_dates"], ["2026-01-05"])

    def test_reject_source_drift_weight_mismatch_and_missing_live_plan(self):
        cases = [
            ("meta", "builder_sha", "b" * 40),
            ("progress", "weight", {"as_of": "2026-01-04", "weight": {"latest_measured_weight_lb": 150}}),
            ("progress", "weight", {"as_of": "2026-01-05", "weight": {"latest_measured_weight_lb": 149}}),
            ("training_detail", "prescriptions", []),
        ]
        for section, key, replacement in cases:
            original = self.data[section][key]
            self.data[section][key] = replacement
            with self.assertRaises(ValueError):
                summarize(self.html(), self.now)
            self.data[section][key] = original
        self.data["weight"].append({"d": "2026-01-05", "lb": 150})
        with self.assertRaisesRegex(ValueError, "duplicate dates"):
            summarize(self.html(), self.now)
        self.data["weight"].pop()
        with self.assertRaisesRegex(ValueError, "expected commit"):
            summarize(self.html(), self.now, expected_sha="b" * 40)

    def test_reject_stale_future_and_unmarked_content(self):
        for now in (self.now + timedelta(minutes=11), self.now - timedelta(minutes=2)):
            with self.assertRaises(ValueError):
                summarize(self.html(), now)
        with self.assertRaises(ValueError):
            summarize("Error page", self.now)
