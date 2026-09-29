"""Fabricated working-set examples for session work and logging coverage."""
import unittest

import build_dashboard as build


def row(session, load, reps, **extra):
    return {"session_id": session, "session_date": "2026-08-01" if session == "a" else "2026-08-10",
            "exercise": "chest_press", "load_lb": load, "reps": reps,
            "status": "completed", "load_basis": "total_stack", **extra}


class StrengthWorkTests(unittest.TestCase):
    def test_generic_equipment_needs_explicit_identity_before_comparison(self):
        records = [row("a", 50, 10, equipment="machine"), row("b", 60, 10, equipment="machine")]
        ambiguous = build.strength_progress(records)["exercises"][0]
        self.assertTrue(ambiguous["comparison_unverified"])
        self.assertIsNone(ambiguous["delta_load"])
        explicit = build.strength_progress(records, equipment_aliases={"chest_press": {"machine": "example_station"}})["exercises"][0]
        self.assertFalse(explicit["comparison_unverified"])
        self.assertEqual(explicit["delta_load"], 10)

    def test_lighter_load_more_reps_and_sets_produces_more_work(self):
        result = build.strength_progress([row("a", 100, 5), row("b", 80, 8), row("b", 80, 8)])
        first, latest = result["exercises"][0]["points"]
        self.assertEqual((first["volume"], latest["volume"]), (500, 1280))
        self.assertEqual((latest["sets"], latest["total_reps"]), (2, 16))
        self.assertEqual(latest["load"], 80)

    def test_aggregate_count_and_backoff_loads_are_used(self):
        result = build.strength_progress([row("a", 80, 8, status="reported_aggregate", set_count=2),
                                          row("a", 60, 10, status="reported_aggregate", set_count=1)])
        point = result["exercises"][0]["latest"]
        self.assertEqual(point["volume"], 1880)
        self.assertEqual((point["sets"], point["total_reps"]), (3, 26))
        self.assertTrue(point["reported_aggregate"])
        self.assertEqual(point["coverage"], "Reported aggregate · individual sets unconfirmed")

    def test_inferred_reps_are_not_presented_as_direct_measurement(self):
        point = build.strength_progress([
            {**row("a", 40, 12, equipment="seated_leg_curl_machine"),
             "exercise": "seated_leg_curl", "notes": "12 reps inferred from target"}
        ])["exercises"][0]["latest"]
        self.assertTrue(point["inferred"])
        self.assertEqual(point["coverage"], "Reps inferred from prescription · not directly reported")

    def test_warmup_excluded_and_effort_retained(self):
        point = build.strength_progress([row("a", 100, 10, notes="Warm-up"), row("a", 80, 8, rir=2)])["exercises"][0]["latest"]
        self.assertEqual(point["volume"], 640)
        self.assertEqual(point["work_sets"][0]["rir"], 2)

    def test_working_set_note_can_mention_an_omitted_practice_set(self):
        point = build.strength_progress([
            row("a", 80, 8, notes="Primary working set; planned practice set was omitted"),
            row("a", 60, 6, notes="Practice set"),
        ])["exercises"][0]["latest"]
        self.assertEqual((point["sets"], point["volume"]), (1, 640))

    def test_missing_row_is_not_zero_work_or_complete(self):
        point = build.strength_progress([row("a", 80, 8), row("a", 80, None)])["exercises"][0]["latest"]
        self.assertTrue(point["incomplete"])
        self.assertEqual(point["volume"], 640)
        point = build.strength_progress([row("a", None, None)])["exercises"][0]["latest"]
        self.assertIsNone(point["volume"])

    def test_aggregate_without_count_and_mixed_rows_have_no_total(self):
        for rows in ([row("a", 80, 8, status="reported_aggregate")],
                     [row("a", 80, 8, status="reported_aggregate", set_count=2), row("a", 80, 8)]):
            point = build.strength_progress(rows)["exercises"][0]["latest"]
            self.assertIsNone(point["volume"])
            self.assertTrue(point["incomplete"])

    def test_equipment_and_incompatible_bases_stay_separate(self):
        result = build.strength_progress([row("a", 80, 8, equipment="machine_a"), row("b", 80, 8, equipment="machine_b"), row("b", 80, 8, equipment="machine_a", load_basis="")])
        self.assertEqual(len(result["exercises"]), 3)
        self.assertEqual(len({x["key"] for x in result["exercises"]}), 3)

    def test_dumbbell_singular_and_plural_are_one_named_variant(self):
        result = build.strength_progress([
            {**row("a", 15, 10, equipment="dumbbell", load_basis="per_hand"),
             "exercise": "dumbbell_curl"},
            {**row("b", 15, 11, equipment="dumbbells", load_basis="per_hand"),
             "exercise": "dumbbell_curl"},
        ], equipment_aliases={"dumbbell_curl": {"dumbbell": "dumbbells"}})
        self.assertEqual(len(result["exercises"]), 1)
        self.assertEqual(result["exercises"][0]["sessions"], 2)

    def test_per_hand_chest_normalization_applies_to_work(self):
        point = build.strength_progress([row("a", 40, 8, load_basis="per_hand")])["exercises"][0]["latest"]
        self.assertEqual(point["volume"], 640)

    def test_chest_joins_independent_arm_history_but_not_other_stack_machine(self):
        result = build.strength_progress([
            row("a", 50, 5, equipment="machine", load_basis="per_hand"),
            row("b", 80, 8, equipment="chest_press_machine"),
            row("b", 40, 8, equipment="independent_arm_chest_press_machine", load_basis="per_hand"),
        ], equipment_aliases={"chest_press": {"machine": "independent_arm_chest_press_machine"}})
        self.assertEqual(len(result["exercises"]), 2)
        independent = next(s for s in result["exercises"] if "independent-arm" in s["name"])
        other = next(s for s in result["exercises"] if "total-stack" in s["name"])
        self.assertEqual([p["volume"] for p in independent["points"]], [500, 640])
        self.assertEqual([p["volume"] for p in other["points"]], [640])

    def test_row_joins_independent_arm_history_but_not_other_stack_machine(self):
        result = build.strength_progress([
            {**row("a", 30, 12, equipment="machine", load_basis="per_hand"), "exercise": "Seated row"},
            {**row("b", 60, 10, equipment="seated_row_machine"), "exercise": "seated_row"},
            {**row("b", 30, 11, equipment="independent_arm_row_machine", load_basis="per_hand"), "exercise": "seated_row"},
        ], equipment_aliases={"seated_row": {"machine": "independent_arm_row_machine"}})
        self.assertEqual(len(result["exercises"]), 2)
        independent = next(s for s in result["exercises"] if "independent-arm" in s["name"])
        self.assertEqual([p["volume"] for p in independent["points"]], [720, 660])

    def test_name_only_lower_and_abdominal_migrations_join(self):
        for exercise in ("leg_press", "leg_extension", "calf_extension", "abdominal_crunch"):
            result = build.strength_progress([
                {**row("a", 50, 10, equipment="machine", load_basis="machine_stack"), "exercise": exercise.replace("_", " ").title()},
                {**row("b", 50, 11, equipment=f"{exercise}_machine", load_basis="machine_stack"), "exercise": exercise},
            ], equipment_aliases={exercise: {"machine": f"{exercise}_machine"}})
            self.assertEqual(len(result["exercises"]), 1, exercise)
            self.assertEqual(result["exercises"][0]["sessions"], 2)

    def test_leg_curl_and_shoulder_machine_variants_remain_distinct(self):
        for exercise in ("seated_leg_curl", "shoulder_press"):
            result = build.strength_progress([
                {**row("a", 40, 10, equipment="machine"), "exercise": exercise},
                {**row("b", 40, 10, equipment=f"{exercise}_machine"), "exercise": exercise},
            ])
            self.assertEqual(len(result["exercises"]), 2, exercise)

    def test_generic_shoulder_history_is_not_claimed_as_comparable_progress(self):
        result = build.strength_progress([
            {**row("a", 60, 7, equipment="machine", load_basis="per_hand"),
             "exercise": "Shoulder press"},
            {**row("b", 20, 12, equipment="machine", load_basis="per_hand"),
             "exercise": "shoulder_press"},
        ], unverified_comparisons=frozenset({("shoulder_press", "machine")}))
        self.assertTrue(result["exercises"][0]["comparison_unverified"])
        self.assertIn("identity unverified", result["exercises"][0]["name"])
        self.assertIsNone(result["exercises"][0]["delta_load"])

    def test_triceps_extension_is_visible_but_not_merged_with_pressdown(self):
        result = build.strength_progress([
            {**row("a", 30, 12, equipment="machine"), "exercise": "triceps_pressdown"},
            {**row("b", 50, 8, equipment="triceps_extension_machine"), "exercise": "triceps_extension"},
        ])
        self.assertEqual({x["exercise_key"] for x in result["exercises"]},
                         {"triceps_pressdown", "triceps_extension"})

    def test_confirmed_legacy_pulldown_labels_form_one_history(self):
        result = build.strength_progress([
            {**row("a", 50, 12, equipment="machine", load_basis="machine_stack"),
             "exercise": "Lat pulldown"},
            {**row("b", 70, 9, equipment="lat_pulldown_machine", load_basis="total_stack"),
             "exercise": "lat_pulldown"},
        ], equipment_aliases={"lat_pulldown": {"machine": "", "lat_pulldown_machine": ""}})
        self.assertEqual(len(result["exercises"]), 1)
        series = result["exercises"][0]
        self.assertEqual(series["key"], "lat_pulldown")
        self.assertEqual(series["sessions"], 2)
        self.assertEqual([point["volume"] for point in series["points"]], [600, 630])

    def test_prescription_incomplete_does_not_imply_missing_set_logging(self):
        from dashboard_fixture import snapshot
        data = snapshot()
        data["progress"]["strength"] = build.strength_progress([row("a", 80, 8)])
        data["training_detail"]["prescriptions"] = [{"recommendation": {"progression_result": {
            "chest_press": {"status": "incomplete", "source_session_id": "a", "exercise_id": "chest_press"}}}}]
        build.presentation_data(data)
        point = data["progress"]["strength"]["exercises"][0]["latest"]
        self.assertFalse(point["incomplete"])
        self.assertEqual(point["coverage"], "Logged working sets")

    def test_actual_missing_set_remains_incomplete_after_presentation(self):
        from dashboard_fixture import snapshot
        data = snapshot()
        data["progress"]["strength"] = build.strength_progress([row("a", 80, 8), row("a", 80, None)])
        build.presentation_data(data)
        point = data["progress"]["strength"]["exercises"][0]["latest"]
        self.assertTrue(point["incomplete"])
        self.assertEqual(point["coverage"], "Incomplete logging · known sets only")
