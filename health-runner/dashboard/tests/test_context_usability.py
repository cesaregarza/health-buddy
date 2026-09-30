"""Focused export usability regressions using a synthetic snapshot."""
import unittest

import context_pack as cp
from dashboard_fixture import snapshot


class ContextUsabilityTests(unittest.TestCase):
    def setUp(self):
        self.data = snapshot()

    def test_nutrient_missing_counts_unknown_partial_and_zero(self):
        d = dict(self.data)
        d["intake"] = [
            {"d": "2026-08-18", "n": 2, "kcal": 500, "protein": 20, "carbs": 60, "fat": 10, "sodium": 100,
             "missing_kcal": 1, "missing_protein": 2, "missing_carbs": 0, "missing_fat": 1, "missing_sodium": 0},
            {"d": "2026-08-19", "n": 2, "kcal": 0, "protein": 0, "carbs": 0, "fat": 0, "sodium": 0,
             "missing_kcal": 1, "missing_protein": 0, "missing_carbs": 0, "missing_fat": 0, "missing_sodium": 0},
        ]
        text = cp.build_pack(d, ["intake"], 30)
        self.assertIn("| 2026-08-18 | 500* | —* | 60 | 10* | 100 | 2 |", text)
        self.assertIn("| 2026-08-18 | 500* | —* | 60 | 10* | 100 | 2 | kcal 1/2; protein 2/2; fat 1/2 |", text)
        self.assertIn("| 2026-08-19 | 0* | 0 | 0 | 0 | 0 | 2 | kcal 1/2 |", text)
        self.assertIn("| 2026-08-19 | 0* | 0 | 0 | 0 | 0 | 2 |", text)  # known partial zero remains a subtotal
        self.assertIn("kcal 1/2;", text)
        self.assertIn("fat 1/2", text)
        self.assertIn("| 2026-08-19 | 0* | 0 | 0 | 0 | 0 | 2 |", text)
        self.assertIn("Averages over logged days: — kcal (n=0)", text)
        self.assertIn("partial subtotals are not treated as complete daily averages", text)

    def test_all_missing_entries_are_unknown_and_excluded_from_means(self):
        d = dict(self.data)
        d["intake"] = [{"d": "2026-08-19", "n": 2, "kcal": 0, "protein": 0, "carbs": 0, "fat": 0, "sodium": 0,
                        "missing_kcal": 2, "missing_protein": 2, "missing_carbs": 2, "missing_fat": 2, "missing_sodium": 2}]
        text = cp.build_pack(d, ["intake"], 30)
        self.assertIn("| 2026-08-19 | —* | —* | —* | —* | —* | 2 |", text)
        self.assertIn("| 2026-08-19 | —* | —* | —* | —* | —* | 2 | kcal 2/2; protein 2/2;", text)
        self.assertIn("— kcal (n=0)", text)
        self.assertIn("fat 2/2", text)

    def test_prepared_questions_drive_summaries_and_preserve_provenance(self):
        d = dict(self.data)
        d["visit"] = {"prepared_questions": [
            {"question_id": "a", "question": "Canonical active?", "status": "active", "topic": "care", "notes": "Bring timeline", "prov": False, "origin": "canonical"},
            {"question_id": "p", "question": "Pending carried?", "status": "pending", "topic": "care", "notes": "", "prov": True, "origin": "pending"},
            {"question_id": "g", "question": "Generated prompt?", "status": "suggested", "topic": "care", "notes": "Evidence notes", "prov": False, "origin": "generated"},
            {"question_id": "c", "question": "Standing context?", "status": "context", "topic": "", "notes": "", "prov": False, "origin": "canonical"},
        ], "questions": [
            {"question_id": "legacy-c", "question": "Original context retained?", "status": "context", "topic": "care", "notes": "Keep this note", "origin": "canonical", "prov": False},
            {"question_id": "c", "question": "Standing context?", "status": "context", "topic": "", "notes": "", "prov": False, "origin": "canonical"},
            {"question": "Stale fallback should not render", "status": "active"}], "note_through": "2026-08-19"}
        text = cp.build_pack(d, ["clinical"], 30)
        self.assertIn("Canonical active?", text)
        self.assertIn("Bring timeline", text)
        self.assertIn("Pending carried?", text)
        self.assertIn("pending / pending source)", text)
        self.assertIn("Generated question suggestions (not canonical questions)", text)
        self.assertIn("Generated prompt?", text)
        self.assertIn("Standing context?", text)
        self.assertIn("Original context retained?", text)
        self.assertIn("Keep this note", text)
        self.assertEqual(text.count("Standing context?"), 1)
        self.assertIn("Evidence notes", text)
        self.assertNotIn("Stale fallback should not render", text)

    def test_legacy_questions_note_through_and_scan_baseline(self):
        d = dict(self.data)
        d["visit"] = {"questions": [{"question": "Older question?", "status": "active"}],
                       "note_markdown": "# Note\nText", "note_generated_on": "2026-08-20", "note_through": "2026-08-19"}
        d["body_composition"] = {"scans": [{"d": "2026-08-15", "scan_id": "s1", "device": "DEXA", "measures": [
            {"measure": "fat_percent", "region": "total", "value": 31.2, "unit": "%"}]}]}
        clinical = cp.build_pack(d, ["clinical"], 30)
        note = cp.build_pack(d, ["note"], 30)
        weight = cp.build_pack(d, ["weight"], 30)
        self.assertIn("Older question?", clinical)
        self.assertIn("includes records through 2026-08-19", note)
        self.assertIn("Latest body-composition scan baseline (2026-08-15, DEXA", weight)
        self.assertIn("31.2 %", weight)
        d["body_composition"]["scans"].append({"d": "2026-08-21", "device": "Future", "measures": [{"measure": "future", "region": "total", "value": 99, "unit": "%"}]})
        weight = cp.build_pack(d, ["weight"], 30)
        self.assertIn("2026-08-15, DEXA", weight)
        self.assertNotIn("Future", weight)

    def test_legacy_missing_counts_and_null_or_nonfinite_averages(self):
        d = dict(self.data)
        d["intake"] = [
            {"d": "2026-08-17", "kcal": 0, "n": 0, "missing_kcal": 2},
            {"d": "2026-08-18", "kcal": 25, "missing_kcal": 1},
            {"d": "2026-08-19", "kcal": None, "n": 1, "missing_kcal": 0},
            {"d": "2026-08-20", "kcal": float("nan"), "n": 1, "missing_kcal": 0},
        ]
        text = cp.build_pack(d, ["intake"], 30)
        self.assertIn("| 2026-08-17 | —*", text)  # zero with missing entries and no usable n is unknown
        self.assertIn("| 2026-08-18 | 25*", text)  # nonzero legacy aggregate is a partial subtotal
        self.assertIn("| 2026-08-18 | 25* | — | — | — | — | — | kcal 1/? |", text)
        self.assertIn("Averages over logged days: — kcal (n=0)", text)

    def test_waist_daily_medians_are_compared_only_within_each_site(self):
        d = dict(self.data)
        d["tape"] = {"waist": [
            {"d": "2026-08-01", "site": "navel", "in": 42},
            {"d": "2026-08-10", "site": "navel", "in": 41},
            {"d": "2026-08-19", "site": "navel", "in": 38},
            {"d": "2026-08-19", "site": "navel", "in": 40},
            {"d": "2026-08-20", "site": "narrowest", "in": 35},
        ]}
        text = cp.build_pack(d, ["weight"], 30)
        self.assertIn("navel, inches): 39.0 in on 2026-08-19; change -3.0 in since 2026-08-01", text)
        self.assertIn("narrowest, inches): 35.0 in on 2026-08-20; baseline only", text)
        self.assertNotIn("change -5.0", text)
        self.assertIn("percent_fat (total): 30 %", text)


if __name__ == "__main__":
    unittest.main()
