"""Synthetic presentation regressions; no canonical files or private snapshots."""
import copy
from datetime import date
import unittest

import build_dashboard as build


def analyte(key="example", values=(120, 50), low=0, high=100, headline=True):
    return {
        "key": key, "name": key, "headline": headline, "ref_low": low,
        "ref_high": high, "unit": "units", "panel": "CMP", "rules": [],
        "points": [{"d": d, "v": v, "flag": None, "prov": False}
                   for d, v in zip(("2026-07-01", "2026-08-19"), values)],
    }


def complete_labs():
    return {"analytes": [analyte(key) for key, _, _ in build.HEADLINE_LABS],
            "dates": ["2026-07-01", "2026-08-19"], "events": []}


class LabPresentationTests(unittest.TestCase):
    def test_arithmetic_direction_is_independent_of_colour(self):
        hdl = build.lab_presentation(analyte("hdl cholesterol", (50, 40), 40, None))
        egfr = build.lab_presentation(analyte("egfr", (70, 75), 60, None))
        self.assertEqual((hdl["direction"], hdl["tone"], hdl["state"]), ("down", "serious", "edge"))
        self.assertEqual((egfr["direction"], egfr["tone"]), ("up", "good"))

    def test_neutral_changes_and_unknown_ranges_do_not_imply_improvement(self):
        self.assertEqual(build.lab_presentation(analyte(values=(50, 55)))["tone"], "neutral")
        unchanged = build.lab_presentation(analyte(values=(50, 50)))
        self.assertEqual((unchanged["direction"], unchanged["tone"]), ("flat", "neutral"))
        self.assertEqual(build.lab_presentation(analyte(low=None, high=None))["state"], "unknown")
        self.assertEqual(build.lab_presentation({"points": []})["state"], "unknown")

    def test_edge_and_outside_are_distinct_and_bounds_are_inclusive(self):
        self.assertEqual(build.lab_presentation(analyte(values=(110, 100)))["state"], "edge")
        self.assertEqual(build.lab_presentation(analyte(values=(110, 101)))["state"], "outside")
        self.assertEqual(build.lab_presentation(analyte(values=(110, 94), high=99))["state"], "in_range")
        self.assertEqual(build.lab_presentation(analyte(values=(1.3, 1.26), low=.76, high=1.27))["state"], "edge")

    def test_only_known_chart_floors_fill_missing_bounds(self):
        self.assertEqual(build.lab_reference(analyte("hdl cholesterol", low=None, high=None)), (40, None))
        self.assertEqual(build.lab_reference(analyte("egfr", low=None, high=None)), (60, None))
        self.assertEqual(build.lab_reference(analyte("example", low=None, high=None)), (None, None))
        self.assertEqual(build.lab_reference(analyte("egfr", low=65, high=None)), (65, None))

    def test_delta_uses_prior_draw_not_second_result_same_day(self):
        a = analyte(values=(110, 50))
        a["points"].append({"d": "2026-08-19", "v": 55})
        self.assertEqual(build.lab_presentation(a)["delta"], -55)

    def test_all_in_range_requires_all_ten_on_same_date(self):
        labs = complete_labs()
        self.assertTrue(build.lab_summary(labs)["all_in_range"])
        self.assertEqual(build.lab_summary(labs)["first_all_in_range"], "2026-08-19")
        labs["analytes"][0]["points"].pop()
        self.assertFalse(build.lab_summary(labs)["all_in_range"])
        self.assertFalse(build.lab_summary({"analytes": []})["all_in_range"])

    def test_first_in_range_is_not_repeated_for_later_normal_panel(self):
        labs = complete_labs()
        for a in labs["analytes"]:
            a["points"][0]["v"] = 50
        self.assertEqual(build.lab_summary(labs)["first_all_in_range"], "2026-07-01")
        self.assertTrue(build.lab_summary(labs)["all_in_range"])

    def test_a_flag_or_missing_range_blocks_whole_panel_claim(self):
        labs = complete_labs()
        labs["analytes"][0]["points"][-1]["flag"] = "high"
        self.assertFalse(build.lab_summary(labs)["all_in_range"])
        labs["analytes"][0]["points"][-1]["flag"] = None
        labs["analytes"][0]["ref_low"] = labs["analytes"][0]["ref_high"] = None
        self.assertFalse(build.lab_summary(labs)["all_in_range"])


class TodayTests(unittest.TestCase):
    def setUp(self):
        self.as_of = date(2026, 8, 20)
        self.bp = [{"d": "2026-08-19", "dia": d, "sys": 120, "status": "valid", "session": "morning"} for d in (90, 92)]
        self.inj = [{"d": "2026-08-14", "n": 3, "dose": "example dose"}]
        self.visit = {"next_visit": {"appointment_date": "2026-08-19", "status": "scheduled", "provider": "Example clinician"}}

    def items(self, labs=None):
        return build.today_items(self.bp, self.inj, self.visit, labs or complete_labs(), self.as_of)

    def test_bounded_priority_and_same_morning_evidence(self):
        items = self.items()
        self.assertEqual([i["tone"] for i in items], ["serious", "warning", "good"])
        self.assertIn("2 readings in one morning", items[0]["evidence"])
        self.assertNotIn("dose", [i["kind"] for i in items])

    def test_invalid_evening_future_and_stale_bp_do_not_trigger(self):
        for change in ({"status": "invalid"}, {"session": "evening"}, {"d": "2026-08-21"}, {"d": "2026-08-01"}):
            with self.subTest(change=change):
                bp = [{**p, **change} for p in self.bp]
                items = build.today_items(bp, [], {}, {}, self.as_of)
                self.assertEqual(items, [])

    def test_visit_states_and_no_false_outcome_claim(self):
        self.assertEqual(self.items()[1]["claim"], "Visit needs outcome")
        self.visit["next_visit"]["appointment_date"] = "2026-08-21"
        self.assertEqual(next(x for x in self.items() if x["kind"] == "visit")["claim"], "Upcoming visit")
        self.visit["next_visit"]["status"] = "completed"
        self.assertFalse(any(x["kind"] == "visit" for x in self.items()))

    def test_inputs_remain_unchanged_and_empty_data_is_safe(self):
        before = copy.deepcopy((self.bp, self.inj, self.visit))
        self.items()
        self.assertEqual((self.bp, self.inj, self.visit), before)
        self.assertEqual(build.today_items([], [], {}, {}, self.as_of), [])

    def test_hrv_is_measurement_and_medication_is_not_a_tile(self):
        tiles = build.tiles([], [], [], [], [], [], self.inj, self.as_of, hrv=[{"d": "2026-08-19", "ms": 44}])
        self.assertEqual([t["label"] for t in tiles], ["HRV"])


if __name__ == "__main__":
    unittest.main()
