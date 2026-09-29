import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import build_dashboard as build
import context_pack as cp


class TemplateAssemblyTests(unittest.TestCase):
    def test_read_template_uses_patched_path_and_optional_feature_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template.html"
            path.write_text("const DATA = /*__DATA__*/;")
            with patch.object(build, "TEMPLATE", path):
                self.assertEqual(build.read_template(), "const DATA = /*__DATA__*/;")
            path.write_text("<script>/*__FEATURES__*/</script>")
            (Path(folder) / "features.js").write_text("function feature() {}")
            self.assertEqual(build.read_template(path), "<script>function feature() {}</script>")


class TileComparisonTests(unittest.TestCase):
    def test_calendar_windows_dedupe_anchor_and_exclude_future_values(self):
        as_of = date(2026, 9, 10)
        sleep = [
            {"d": "2026-09-03", "hours": 6.0, "src": "watch"},
            {"d": "2026-09-04", "hours": 8.0, "src": "watch"},
            {"d": "2026-09-04", "hours": 10.0, "src": "checkin"},
            {"d": "2026-09-10", "hours": 9.0, "src": "watch"},
            {"d": "2026-09-11", "hours": 100.0, "src": "future"},
            {"d": "2026-09-08", "hours": float("nan"), "src": "bad"},
            {"d": "2026-09-07", "hours": -4, "src": "negative"},
            {"d": "nonsense", "hours": 12, "src": "invalid date"},
        ]
        rhr = [
            {"d": "2026-09-01", "bpm": 60}, {"d": "2026-09-03", "bpm": 70},
            {"d": "2026-09-09", "bpm": 62}, {"d": "2026-09-12", "bpm": 1},
        ]
        steps = [
            {"d": "2026-09-01", "n": 1000}, {"d": "2026-09-01", "n": 2000},
            {"d": "2026-09-03", "n": 3000}, {"d": "2026-09-08", "n": 5000},
            {"d": "2026-09-10", "n": 9999}, {"d": "2026-09-11", "n": 99999},
        ]
        tiles = build.tiles([], [], [], sleep, rhr, steps, [], as_of=as_of)
        by_label = {tile["label"]: tile for tile in tiles}
        self.assertEqual(by_label["Sleep, last night"]["value"], "9.0")
        self.assertEqual(by_label["Sleep, last night"]["comparison_dates"], ["2026-09-03", "2026-09-04"])
        self.assertIn("2 of 7 prior nights recorded", by_label["Sleep, last night"]["sub"])
        self.assertEqual(by_label["Resting heart rate"]["comparison_dates"], ["2026-09-03"])
        self.assertEqual(by_label["Resting heart rate"]["delta"], -8.0)
        self.assertEqual(by_label["Steps, last full day"]["value"], "5,000")
        self.assertEqual(by_label["Steps, last full day"]["comparison_dates"], ["2026-09-01", "2026-09-03"])

    def test_no_prior_calendar_values_means_no_delta_and_bp_spark_is_valid_only(self):
        bp = [
            {"d": "2026-09-10", "sys": 121, "dia": 80, "session": "morning", "status": "valid"},
            {"d": "2026-09-11", "sys": 220, "dia": 120, "session": "morning", "status": "invalid"},
        ]
        tiles = build.tiles([], [], bp, [], [{"d": "2026-09-10", "bpm": 70}], [], [], as_of=date(2026, 9, 10))
        by_label = {tile["label"]: tile for tile in tiles}
        self.assertIsNone(by_label["Resting heart rate"]["delta"])
        self.assertEqual(by_label["Resting heart rate"]["coverage"], "0 of 7 prior days recorded")
        self.assertEqual(by_label["Morning BP, last valid"]["spark"], [121])


class TrainingAggregationTests(unittest.TestCase):
    DATE = "2026-09-08"

    def aggregate(self, sessions, workouts=(), cardio=()):
        sources = {"data/sessions.csv": sessions, "data/cardio.csv": cardio}
        with patch.object(build, "rows", side_effect=lambda path: sources.get(path, [])):
            return build.training_daily(list(workouts))

    def test_unknown_duration_stays_unknown_and_cardio_segments_precede_watch(self):
        sessions = [{"session_id": "c1", "date": self.DATE, "workout_type": "cardio", "status": "complete", "duration_min": ""}]
        unknown = self.aggregate(sessions)[0]
        self.assertIsNone(unknown["sessions"][0]["min"])
        self.assertEqual(unknown["sessions"][0]["src"], "unknown")
        self.assertFalse(unknown["sessions"][0]["counted"])
        self.assertEqual(unknown["unknown_duration_count"], 1)
        self.assertEqual(unknown["minutes"], {})

        watch = {"id": "w1", "d": self.DATE, "start": "09:00", "end": "10:00", "min": 60, "hk_type": 37}
        segmented = self.aggregate(sessions, [watch], [{"session_id": "c1", "duration_seconds": "900"}])[0]
        self.assertEqual(segmented["sessions"][0]["src"], "segments")
        self.assertEqual(segmented["minutes"], {"cardio": 15})
        self.assertEqual(len(segmented["sessions"]), 1)
        self.assertEqual(segmented["sessions"][0]["min"], 15)
        self.assertEqual(segmented["sessions"][0]["match_status"], "inferred-date-type")
        zero_segment = self.aggregate(sessions, [watch], [{"session_id": "c1", "duration_seconds": "0"}])[0]
        self.assertEqual(zero_segment["sessions"][0]["src"], "segments")
        self.assertEqual(zero_segment["minutes"], {"cardio": 0})

    def test_one_canonical_two_watch_candidates_are_excluded_as_ambiguous(self):
        session = {"session_id": "s1", "date": self.DATE, "workout_type": "lower_body", "status": "complete", "duration_min": ""}
        watches = [
            {"id": "w1", "d": self.DATE, "start": "08:00", "end": "08:30", "min": 30, "hk_type": 50},
            {"id": "w2", "d": self.DATE, "start": "09:00", "end": "09:40", "min": 40, "hk_type": 50},
        ]
        day = self.aggregate([session], watches)[0]
        self.assertEqual(day["minutes"], {})
        self.assertEqual(day["ambiguous_watch_count"], 2)
        self.assertTrue(day["incomplete"])
        self.assertEqual([s["src"] for s in day["sessions"]], ["unknown", "watch-unmatched", "watch-unmatched"])
        self.assertTrue(all(not s["counted"] for s in day["sessions"]))

    def test_two_canonical_one_watch_does_not_assign_watch_to_either(self):
        sessions = [
            {"session_id": sid, "date": self.DATE, "workout_type": "upper_body", "status": "complete", "duration_min": ""}
            for sid in ("s1", "s2")
        ]
        watch = {"id": "w1", "d": self.DATE, "start": "08:00", "end": "08:30", "min": 30, "hk_type": 50}
        day = self.aggregate(sessions, [watch])[0]
        self.assertEqual(day["minutes"], {})
        self.assertEqual(day["unknown_duration_count"], 2)
        self.assertEqual(day["ambiguous_watch_count"], 1)
        self.assertEqual(day["sessions"][-1]["src"], "watch-unmatched")


    def test_unique_date_type_pair_is_inferred_and_watch_only_bucket_counts(self):
        session = {"session_id": "s1", "date": self.DATE, "workout_type": "upper_body", "status": "complete", "duration_min": ""}
        watch = {"id": "w1", "d": self.DATE, "start": "08:00", "end": "08:30", "min": 30, "hk_type": 50}
        inferred = self.aggregate([session], [watch])[0]
        self.assertEqual(inferred["sessions"][0]["src"], "watch-inferred-date-type")
        self.assertEqual(inferred["sessions"][0]["match_status"], "inferred-date-type")
        self.assertTrue(inferred["sessions"][0]["counted"])
        logged = dict(session, duration_min="45")
        duration_priority = self.aggregate([logged], [watch])[0]["sessions"][0]
        self.assertEqual(duration_priority["src"], "logged")
        self.assertEqual(duration_priority["match_status"], "inferred-date-type")
        watch_only = self.aggregate([], [watch])[0]
        self.assertEqual(watch_only["minutes"], {"strength": 30})
        self.assertFalse(watch_only["incomplete"])

    def test_training_detail_carries_watch_identity_times_and_unknown_total(self):
        watch = {"id": "w1", "d": self.DATE, "start": "08:00", "end": "08:30",
                 "start_at": "2026-09-08T13:00:00Z", "end_at": "2026-09-08T13:30:00Z",
                 "min": None, "hk_type": 50}
        daily = self.aggregate([], [watch])
        sources = {"data/sessions.csv": [], "data/sets.csv": [], "data/cardio.csv": []}
        with patch.object(build, "rows", side_effect=lambda path: sources.get(path, [])):
            result = build.training_detail([watch], daily, {"snapshots": [], "errors": []})
        day = result["days"][0]
        session = day["sessions"][0]
        self.assertIsNone(day["total_minutes"])
        self.assertTrue(day["incomplete"])
        self.assertEqual(session["watch_id"], "w1")
        self.assertEqual(session["start_at"], watch["start_at"])
        self.assertEqual(session["end_at"], watch["end_at"])
        self.assertFalse(session["counted"])

    def test_no_id_watch_dedup_requires_precise_timestamps_type_and_duration(self):
        base = {"d": self.DATE, "start": "08:00", "end": "08:30", "min": 30, "hk_type": 50}
        exact = dict(base, start_at="2026-09-08T13:00:00Z", end_at="2026-09-08T13:30:00Z")
        day = self.aggregate([], [exact, dict(exact)])[0]
        self.assertEqual(day["minutes"], {"strength": 30})
        coarse = self.aggregate([], [base, dict(base)])[0]
        self.assertEqual(coarse["minutes"], {"strength": 60})

    def test_watch_only_strength_counts_and_exact_duplicate_id_time_is_deduped(self):
        watch = {"id": "w1", "d": self.DATE, "start": "08:00", "end": "08:30",
                 "start_at": "2026-09-08T13:00:00Z", "end_at": "2026-09-08T13:30:00Z",
                 "min": 30, "hk_type": 50}
        day = self.aggregate([], [watch, dict(watch)])[0]
        self.assertEqual(day["minutes"], {"strength": 30})
        self.assertEqual(len(day["sessions"]), 1)
        self.assertEqual(day["sessions"][0]["src"], "watch-only")
        self.assertEqual(day["sessions"][0]["watch_id"], "w1")
        self.assertEqual(day["sessions"][0]["start"], "08:00")
        self.assertIn("strength", build.TRAINING_ORDER)
        for canonical in ("strength", "racquet", "shuffle"):
            self.assertEqual(build.TRAINING_TYPE[canonical], canonical)


class TrainingContextTests(unittest.TestCase):
    def test_context_pack_labels_partial_known_subtotals_and_excluded_candidates(self):
        data = {
            "training": [{"d": "2026-09-08", "minutes": {"upper": 40}, "incomplete": True,
                          "unknown_duration_count": 1, "ambiguous_watch_count": 2}],
            "training_types": ["upper"],
            "training_detail": {"days": [{"d": "2026-09-08", "incomplete": True,
                "unknown_duration_count": 1, "ambiguous_watch_count": 2, "sessions": [
                    {"type": "upper", "status": "complete", "minutes": 40, "duration_source": "logged", "counted": True},
                    {"type": "strength", "status": "complete", "minutes": 30, "duration_source": "watch-unmatched", "counted": False, "match_status": "ambiguous"},
                ]}], "prescriptions": []},
        }
        lines = cp.sec_training(data, cp.Ctx(as_of=date(2026, 9, 10), days=7))
        text = "\n".join(lines)
        self.assertIn("known counted subtotals", text)
        self.assertIn("partial known subtotals", text)
        self.assertIn("2 ambiguous watch candidate(s) excluded", text)
        self.assertIn("excluded from totals (ambiguous match)", text)


if __name__ == "__main__":
    unittest.main()
