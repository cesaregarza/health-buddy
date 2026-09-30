"""Context pack rendering over the fabricated fixture; nothing here is a live snapshot."""
import json
import tempfile
import unittest
from pathlib import Path

import context_pack as cp
from dashboard_fixture import snapshot


class ContextPackTests(unittest.TestCase):
    def setUp(self):
        self.data = snapshot()

    def test_catalog_and_presets_are_consistent(self):
        ids = [s["id"] for s in cp.catalog()["scopes"]]
        self.assertEqual(ids, cp.SCOPE_IDS)
        for name, members in cp.PRESETS.items():
            self.assertTrue(set(members) <= set(ids), name)
        self.assertEqual(cp.catalog()["windows"], [14, 30, 90, 0])

    def test_only_selected_sections_render_with_their_rules(self):
        text = cp.build_pack(self.data, ["weight", "bp"], 30, "How is my BP?")
        self.assertIn("## Weight and body measurements", text)
        self.assertIn("## Blood pressure and pulse", text)
        self.assertNotIn("## Food intake", text)
        self.assertIn("## Request\n\nHow is my BP?", text)
        self.assertIn("use the recorded sources and units", text)
        self.assertNotIn("Intake is logged", text)
        self.assertTrue(text.endswith("\n"))

    def test_scope_resolution(self):
        self.assertEqual([s.id for s in cp.resolve_scopes("all")], cp.SCOPE_IDS)
        self.assertEqual([s.id for s in cp.resolve_scopes("bp, weight")], ["weight", "bp"])
        self.assertEqual([s.id for s in cp.resolve_scopes(["labs"])], ["labs"])
        with self.assertRaises(ValueError):
            cp.resolve_scopes("nope")
        with self.assertRaises(ValueError):
            cp.build_pack(self.data, [], 30)

    def test_window_filters_time_series(self):
        wide = cp.build_pack(self.data, ["bp", "intake"], 30)
        self.assertIn("| 2026-08-19 08:00 | 125/90 |", wide)
        self.assertIn("| 2026-08-19 | 1,800 |", wide)
        narrow = cp.build_pack(self.data, ["bp", "intake"], 1)
        self.assertIn("No blood-pressure readings in the window (last 1 days", narrow)
        self.assertIn("No intake rows in the window", narrow)
        self.assertIn("Absence means not logged, not zero", narrow)
        self.assertIn("all history to 2026-08-20", cp.build_pack(self.data, ["bp"], 0))

    def test_missing_profile_and_tape_are_tolerated(self):
        data = {k: v for k, v in self.data.items() if k not in ("profile", "tape", "body_composition")}
        text = cp.build_pack(data, ["profile", "weight"], 30)
        self.assertIn("Medications: not embedded in this page build.", text)
        self.assertNotIn("Waist (tape", text)

    def test_profile_and_tape_render_when_present(self):
        d = dict(self.data)
        d["profile"] = {"medications": [{"name": "Example med", "ingredient": "examplium", "strength": "5 mg", "form": "tablet",
                                         "route": "oral", "frequency": "daily", "status": "active", "start": "2026-01-01",
                                         "end": "", "indication": "example", "prescriber": ""}],
                        "conditions": [{"name": "Example condition", "category": "history", "status": "historical", "onset": "", "notes": "a note"}]}
        d["tape"] = {"waist": [{"d": "2026-08-13", "in": 39.0, "site": "x"}, {"d": "2026-08-20", "in": 38.5, "site": "x"}],
                     "circumferences": [{"d": "2026-08-20", "site": "chest", "side": "", "in": 40.375}]}
        text = cp.build_pack(d, ["profile", "weight"], 30)
        self.assertIn("| Example med (examplium) | 5 mg | oral / daily | active | 2026-01-01 | example |", text)
        self.assertIn("- Example condition — history, historical. a note", text)
        self.assertIn("Waist (tape, x, inches): 38.5 in on 2026-08-20; change -0.5 in since 2026-08-13", text)
        self.assertIn("chest 40.38 in", text)

    def test_intake_unknown_nutrients_are_not_zero(self):
        d = dict(self.data)
        d["intake"] = [{"d": "2026-08-19", "kcal": 0, "protein": 0, "carbs": 0, "fat": 0, "sodium": 0, "n": 1,
                        "missing_kcal": 1, "missing_protein": 1, "missing_carbs": 1, "missing_sodium": 1}]
        text = cp.build_pack(d, ["intake"], 30)
        self.assertIn("| 2026-08-19 | —* | —* | —* | 0 | —* | 1 |", text)
        self.assertIn("unknown, not zero", text)
        self.assertIn("Averages over logged days: — kcal", text)

    def test_lab_reference_formats(self):
        text = cp.build_pack(self.data, ["labs"], 30)
        self.assertIn("≥40 units", text)   # HDL: floor only
        self.assertIn("0–100 units", text)  # two-sided range

    def test_training_uses_prescription_and_sessions(self):
        text = cp.build_pack(self.data, ["training"], 30)
        self.assertIn("## Training and strength", text)
        self.assertIn("2026-08-19 upper · complete · 40 min (logged): example_lift 20 lb/hand × 10.", text)

    def test_size_estimate(self):
        self.assertEqual(cp.size_of("a" * 400), {"chars": 400, "tokens_est": 100})

    def test_load_data_reads_the_embedded_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "index.html"
            payload = json.dumps({"meta": {"built_at_ct": "2026-08-20 01:00 CDT"}, "x": "</script>"}).replace("</", "<\\/")
            p.write_text("<script>const DATA = " + payload + ";\nrender(DATA);</script>")
            d = cp.load_data(p)
            self.assertEqual(d["x"], "</script>")
            self.assertEqual(cp.as_of_date(d).isoformat(), "2026-08-20")


if __name__ == "__main__":
    unittest.main()
