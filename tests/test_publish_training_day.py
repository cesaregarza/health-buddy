"""scripts/publish_training_day.py: legacy CLI that no product path calls."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.publish_training_day import publish_snapshot, render_snapshot


class PublishTrainingDayTests(unittest.TestCase):
    def snapshot(self) -> dict:
        return {
            "schema_version": 1,
            "date": "2026-09-10",
            "generator": "scripts/publish_training_day.py",
            "recommendation": {
                "status": "scheduled",
                "template": {"label": "Upper body"},
            },
        }

    def test_snapshot_is_deterministic_and_newline_terminated(self) -> None:
        rendered = render_snapshot(self.snapshot())
        self.assertTrue(rendered.endswith("\n"))
        self.assertEqual(json.loads(rendered)["date"], date(2026, 9, 10).isoformat())

    def test_publish_is_idempotent_and_check_detects_stale_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            target, status = publish_snapshot(self.snapshot(), output)
            self.assertEqual(status, "created")
            self.assertEqual(publish_snapshot(self.snapshot(), output)[1], "unchanged")
            self.assertEqual(
                publish_snapshot(self.snapshot(), output, check=True)[1], "current"
            )

            target.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "missing or stale"):
                publish_snapshot(self.snapshot(), output, check=True)


if __name__ == "__main__":
    unittest.main()
