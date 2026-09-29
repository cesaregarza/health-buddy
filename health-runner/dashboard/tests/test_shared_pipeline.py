import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import context_pack
import pipeline
import snapshot_store

NOW = datetime(2026, 9, 28, 16, tzinfo=timezone.utc)


def data():
    return {
        "meta": {
            "origin_full_sha": "a" * 40,
            "builder_sha": "a" * 40,
            "built_at": NOW.isoformat(),
            "healthkit_last_batch": "2026-09-28T15:00:00Z",
            "healthkit_available": True,
        },
        "weight": [],
        "sleep": [],
        "rhr": [],
        "bp": [],
        "training": [],
        "intake": [],
        "steps": [],
    }


class SharedSnapshotTests(unittest.TestCase):
    def test_atomic_generation_keeps_html_and_json_together(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "index.html"
            first = data()
            snapshot_store.publish(first, "first", output)
            old = (output.parent / ".current").resolve()
            self.assertEqual(
                snapshot_store.load(output.with_name("snapshot.json")), first
            )
            second = data()
            second["weight"] = [{"d": "2026-09-27", "lb": 200}]
            snapshot_store.publish(second, "second", output)
            self.assertEqual(output.read_text(), "second")
            self.assertEqual(
                snapshot_store.load(output.with_name("snapshot.json")), second
            )
            self.assertNotEqual(old, (output.parent / ".current").resolve())
            self.assertEqual(
                output.resolve().parent,
                output.with_name("snapshot.json").resolve().parent,
            )
            with patch.object(
                snapshot_store,
                "_link",
                side_effect=OSError("synthetic pointer failure"),
            ):
                with self.assertRaises(OSError):
                    snapshot_store.publish(first, "failed", output)
            self.assertEqual(output.read_text(), "second")
            self.assertEqual(
                snapshot_store.load(output.with_name("snapshot.json")), second
            )

    def test_invalid_json_revision_and_checksum_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "index.html"
            snapshot_store.publish(data(), "html", output)
            path = output.with_name("snapshot.json")
            original = json.loads(path.read_text())
            for change in ("schema", "checksum", "revision"):
                body = copy.deepcopy(original)
                if change == "schema":
                    body["schema_version"] = True
                elif change == "checksum":
                    body["data"]["weight"] = [1]
                else:
                    body["data"]["meta"]["builder_sha"] = "b" * 40
                path.write_text(json.dumps(body))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    snapshot_store.load(path)

    def test_context_prefers_structured_data_and_legacy_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "index.html"
            output.write_text('const DATA = {"legacy":true};')
            with patch.object(context_pack, "HTML", output):
                self.assertEqual(context_pack.load_data(), {"legacy": True})
                snapshot_store.publish(data(), 'const DATA = {"legacy":true};', output)
                self.assertEqual(context_pack.load_data(), data())
                self.assertEqual(context_pack.load_data(output), {"legacy": True})
                output.with_name("snapshot.json").write_text("{}")
                with self.assertRaises(ValueError):
                    context_pack.load_data()


class PipelineTests(unittest.TestCase):
    def test_refresh_only_after_changes_settle(self):
        meta = data()["meta"]
        self.assertIsNone(
            pipeline.refresh_reason(meta, meta["healthkit_last_batch"], "a" * 40, NOW)
        )
        self.assertIsNone(
            pipeline.refresh_reason(meta, "2026-09-28T15:59:50Z", "a" * 40, NOW)
        )
        self.assertEqual(
            pipeline.refresh_reason(meta, "2026-09-28T15:59:30Z", "a" * 40, NOW),
            "ingestion_changed",
        )
        self.assertEqual(
            pipeline.refresh_reason(meta, meta["healthkit_last_batch"], "b" * 40, NOW),
            "source_changed",
        )
        self.assertEqual(
            pipeline.refresh_reason(None, None, "a" * 40, NOW), "snapshot_missing"
        )

    def test_monitor_uses_utc_and_no_commit_age_alarm(self):
        self.assertEqual(
            pipeline.assess(data()["meta"], "2026-09-27T16:01:00Z", [], NOW), {}
        )
        alarms = pipeline.assess(
            data()["meta"],
            "2026-09-27T15:59:00Z",
            ["health-weekly-report.service"],
            NOW,
        )
        self.assertEqual(set(alarms), {"ingest_stale", "health-weekly-report.service"})

    def test_check_only_never_refreshes_writes_or_notifies(self):
        with (
            patch.object(pipeline, "inspect", return_value={"alerts": {}}),
            patch.object(pipeline, "refresh") as refresh,
            patch.object(pipeline, "write_weekly") as report,
            patch.object(pipeline, "monitor") as monitor,
        ):
            for action in ("refresh", "monitor", "weekly"):
                self.assertEqual(pipeline.main([action, "--notify", "--check-only"]), 0)
            refresh.assert_not_called()
            report.assert_not_called()
            monitor.assert_not_called()

    def test_monitor_notifies_only_new_conditions_and_does_not_mark_failed_delivery(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            report = {"alerts": {"one": "Fixed headline"}}
            with (
                patch.object(pipeline, "inspect", return_value=report),
                patch.object(pipeline, "notify") as notify,
            ):
                pipeline.monitor(Path("unused"), state, send=False)
                self.assertFalse(state.exists())
                notify.assert_not_called()
                pipeline.monitor(Path("unused"), state, send=True)
                pipeline.monitor(Path("unused"), state, send=True)
                notify.assert_called_once_with("Fixed headline")
                report["alerts"]["two"] = "New fixed headline"
                notify.side_effect = OSError("synthetic network failure")
                with self.assertRaises(OSError):
                    pipeline.monitor(Path("unused"), state, send=True)
                self.assertEqual(json.loads(state.read_text()), ["one"])

    def test_weekly_bounds_valid_bp_unknowns_and_no_prescription(self):
        example = data()
        example.update(
            weight=[
                {"d": "2026-09-21", "lb": 200},
                {"d": "2026-09-27", "lb": 198},
                {"d": "2026-09-28", "lb": 500},
            ],
            bp=[
                {"d": "2026-09-22", "status": "valid", "sys": 120, "dia": 80},
                {"d": "2026-09-23", "status": "invalid", "sys": 200, "dia": 120},
            ],
            training=[
                {
                    "d": "2026-09-24",
                    "minutes": {},
                    "incomplete": True,
                    "sessions": [{"src": "unknown"}],
                }
            ],
        )
        report = pipeline.weekly_text(example, NOW.date(), NOW)
        self.assertIn("199.0 lb", report)
        self.assertNotIn("500.0", report)
        self.assertIn("120/80 mmHg", report)
        self.assertIn("1 valid, 1 invalid", report)
        self.assertIn("Duration unknown", report)
        self.assertIn("0 dates with entries", report)
        self.assertIn("descriptive-only", report)
        self.assertIn("Watch-wear coverage unverified", report)
        example["meta"]["healthkit_available"] = False
        with self.assertRaises(ValueError):
            pipeline.weekly_text(example, NOW.date(), NOW)
        self.assertEqual(
            pipeline.report_week(datetime(2026, 9, 27).date())[1].isoformat(),
            "2026-09-20",
        )
        self.assertEqual(pipeline.report_week(NOW.date())[1].isoformat(), "2026-09-27")


class DeliveryRecoveryTests(unittest.TestCase):
    def test_multi_alert_failure_retains_earlier_success(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state.json"
            report = {"alerts": {"one": "First", "two": "Second"}}
            with (
                patch.object(pipeline, "inspect", return_value=report),
                patch.object(
                    pipeline, "notify", side_effect=[None, OSError("offline")]
                ),
            ):
                with self.assertRaises(OSError):
                    pipeline.monitor(Path("unused"), state, True)
            self.assertEqual(json.loads(state.read_text()), ["one"])
            with (
                patch.object(pipeline, "inspect", return_value=report),
                patch.object(pipeline, "notify") as notify,
            ):
                pipeline.monitor(Path("unused"), state, True)
                notify.assert_called_once_with("Second")

    def test_weekly_atomic_failure_preserves_absence_and_retry_deduplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "reports"
            deliveries = root / "delivery"
            with (
                patch.object(pipeline.snapshot_store, "load", return_value=data()),
                patch.object(
                    pipeline, "weekly_text", return_value="Descriptive report"
                ),
                patch.object(pipeline, "notify") as notify,
            ):
                with patch.object(
                    pipeline.os, "link", side_effect=OSError("disk failure")
                ):
                    with self.assertRaises(OSError):
                        pipeline.write_weekly(
                            root / "snapshot", output, True, deliveries
                        )
                self.assertEqual(list(output.glob("*.md")), [])
                notify.assert_not_called()
                notify.side_effect = OSError("offline")
                with self.assertRaises(OSError):
                    pipeline.write_weekly(root / "snapshot", output, True, deliveries)
                self.assertEqual(len(list(output.glob("*.md"))), 1)
                notify.side_effect = None
                notify.reset_mock()
                result = pipeline.write_weekly(
                    root / "snapshot", output, True, deliveries
                )
                self.assertTrue(result["existing_preserved"])
                pipeline.write_weekly(root / "snapshot", output, True, deliveries)
                notify.assert_called_once_with("Weekly health report ready.")
                self.assertEqual(
                    Path(result["report"]).read_text(), "Descriptive report"
                )

    def test_existing_report_is_not_overwritten_or_newly_announced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "reports"
            deliveries = root / "delivery"
            with (
                patch.object(pipeline.snapshot_store, "load", return_value=data()),
                patch.object(pipeline, "weekly_text", return_value="Original report"),
                patch.object(pipeline, "notify") as notify,
            ):
                original = pipeline.write_weekly(
                    root / "snapshot", output, False, deliveries
                )
                with patch.object(pipeline, "weekly_text", return_value="Replacement"):
                    pipeline.write_weekly(root / "snapshot", output, True, deliveries)
                self.assertEqual(
                    Path(original["report"]).read_text(), "Original report"
                )
                notify.assert_not_called()
