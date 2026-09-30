"""Entirely fabricated data for dashboard rendering; never copy a live snapshot here."""
from datetime import date, timedelta

import build_dashboard as build

AS_OF = date(2026, 8, 20)


def snapshot():
    dates = [(AS_OF - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    weight = [{"d": d, "lb": 215 - i * .3, "src": "log"} for i, d in enumerate(dates)]
    bp = [{"d": dates[-2], "t": dates[-2] + f"T08:0{i}:00", "sys": 125,
           "dia": dia, "status": "valid", "session": "morning"} for i, dia in enumerate((90, 92))]
    labs = []
    for key, name, rules in build.HEADLINE_LABS:
        labs.append({"key": key, "name": name, "headline": True, "unit": "units",
                     "panel": "CMP", "ref_low": 0, "ref_high": 100,
                     "rules": rules.get("rules", []),
                     "points": [{"d": "2026-07-01", "v": 120, "flag": "high", "prov": False},
                                {"d": dates[-2], "v": 50, "flag": None, "prov": False}]})
    for a in labs:
        if a["key"] in ("hdl cholesterol", "egfr"):
            a["ref_low"], a["ref_high"] = (40 if a["key"] == "hdl cholesterol" else 60), None
            a["points"][0].update(v=50 if a["key"] == "hdl cholesterol" else 70, flag=None)
            a["points"][1]["v"] = 40 if a["key"] == "hdl cholesterol" else 75
    for key, panel in (("example blood count", "CBC"), ("example sodium", "CMP")):
        labs.append({"key": key, "name": key, "headline": False, "unit": "units", "panel": panel,
                     "ref_low": None, "ref_high": None, "rules": [],
                     "points": [{"d": dates[-2], "v": 5, "flag": None, "prov": False}]})
    exercises = []
    for i, status in enumerate(("incomplete", "reps", "eligible_next_load", "eligible_next_load", "reps", "incomplete")):
        result = {"source_date": dates[-3], "load_basis": "per_hand", "evidence_sets": [{"load": 20, "reps": 10, "rir": 2}], "status": status, "message": "verify the smallest available increment" if i == 3 else "Example evidence"}
        exercises.append({"exercise": f"Example lift {i + 1}", "next_target": "20 lb per hand × 10. Stop with two reps in reserve.",
                          "load": "20 lb per hand", "work": "2 sets", "notes": "Example setup", "progression_result": result})
    rx = {"date": dates[-1], "recommendation": {"date": dates[-1], "status": "scheduled",
          "gym_timing": {"latest_full_start": "22:00", "latest_minimum_start": "22:20", "hours": "Closes 23:00", "rule": "Example gym timing"},
          "template": {"label": "Example upper body", "exercises": exercises, "warmup": ["Example warmup"], "cooldown": ["Example cooldown"]}}}
    sessions = [{"id": "example-session", "type": "upper", "minutes": 40, "duration_source": "logged", "status": "complete",
                 "sets": [{"exercise": "example_lift", "load_lb": 20, "load_basis": "per_hand", "reps": 10}], "cardio": []}]
    goals = [{"target_value": goal, "remaining": 11 if goal == 200 else 41,
              "percent_complete": 60 if goal == 200 else 30,
              "projection_range": {"early": "2026-08-20", "late": "2026-08-22"},
              "projections": {"forward_30_day_ordinary": "2026-08-20", "forward_30_day_robust": "2026-08-22"}} for goal in (200, 170)]
    data = {
        "meta": {"built_at": "2026-08-20T06:00:00Z", "built_at_ct": "2026-08-20 01:00 CDT",
                 "healthkit_last_batch": "2026-08-20T04:00:00Z", "tz": "America/Chicago", "origin_sha": "example",
                 "origin_committed": "2026-08-19T20:00:00Z"},
        "weight": weight, "weight7": weight, "weight_goals": {"milestone": 200, "band": [175, 185]}, "bp": bp,
        "injections": [{"d": "2026-08-14", "n": 3, "dose": "example dose"}],
        "tracking_schedule": build.tracking_schedule(
            [{"measured_at_local": "2026-08-13T09:00", "waist_in": "38"}],
            ["data/progress_photos/2026-08-01-front.jpeg"], AS_OF),
        "tape": {"waist": [{"d": dates[0], "site": "navel", "in": 40}, {"d": dates[-2], "site": "navel", "in": 38}, {"d": dates[-2], "site": "navel", "in": 40}, {"d": dates[-1], "site": "narrowest", "in": 35}], "circumferences": [{"d": dates[-2], "site": "upper_arm", "side": "left", "in": 13}]},
        "body_composition": {"scans": [{"d": dates[0], "scan_id": "example-scan", "device": "Example DXA", "measures": [{"measure": "percent_fat", "region": "total", "value": 30, "unit": "%"}, {"measure": "fat_mass", "region": "total", "value": 60, "unit": "lb"}]}]},
        "intake": [{"d": dates[-5], "n": 2, **{k: 0 for k in ("kcal", "protein", "carbs", "fat", "sodium")}, **{"missing_"+k: 2 for k in ("kcal", "protein", "carbs", "fat", "sodium")}},
                   {"d": dates[-4], "n": 2, **{k: 0 for k in ("kcal", "protein", "carbs", "fat", "sodium")}, **{"missing_"+k: 1 for k in ("kcal", "protein", "carbs", "fat", "sodium")}},
                   {"d": dates[-3], "n": 1, **{k: 0 for k in ("kcal", "protein", "carbs", "fat", "sodium")}},
                   {"d": dates[-2], "kcal": 1800, "protein": 100, "carbs": 150, "fat": 70, "sodium": 1500, "n": 3, "missing_kcal": 0}],
        "training": [{"d": dates[-2], "minutes": {"upper": 40}, "sessions": [{"id": "example-session", "type": "upper", "min": 40, "src": "logged"}]}],
        "training_types": build.TRAINING_ORDER,
        "training_detail": {"prescriptions": [rx], "prescription_errors": [], "days": [{"d": dates[-2], "total_minutes": 40, "sessions": sessions}]},
        "sleep": [{"d": d, "hours": 7 + i % 3 * .2, "src": "watch"} for i, d in enumerate(dates)],
        "rhr": [{"d": d, "bpm": 60 + i % 3} for i, d in enumerate(dates)],
        "hrv": [{"d": d, "ms": 45 + i % 3} for i, d in enumerate(dates)],
        "steps": [{"d": d, "n": 5000 + i * 10} for i, d in enumerate(dates)], "workouts": [],
        "energy": [{"d": d, "basal_kcal": 1950 + i, "active_kcal": 350 + i * 10}
                   for i, d in enumerate(dates)],
        "labs": {"analytes": labs, "events": [], "dates": ["2026-07-01", dates[-2]], "provisional_dates": []},
        "visit": {"next_visit": {"appointment_date": dates[-2], "status": "scheduled", "provider": "Example clinician"},
                  "last_visit": None, "questions": [{"question_id": "example-q", "question": "Example active question?", "topic": "Example", "notes": "Example supporting detail", "status": "active", "recorded_on": dates[-2]},
                                                     {"question_id": "example-context", "question": "Example context", "topic": "Example", "status": "context", "recorded_on": dates[-2]}],
                  "prepared_questions": [{"question_id": "example-q", "question": "Example active question?", "topic": "Example", "notes": "Example supporting detail", "status": "active", "origin": "canonical", "prov": False}, {"question_id": "suggested-q", "question": "Example generated question?", "status": "suggested", "origin": "generated", "prov": False}],
                  "note_through": dates[-1], "note_markdown": "## Example note\n\nSynthetic browser fixture.\n\n## Questions for clinician\n\n- Example active question?\n- Example generated question? [generated suggestion]", "note_generated_on": dates[-1]},
        "progress": {"strength": {"exercises": []}, "weight": {
            "as_of": dates[-1], "estimated_treatment_start": {"date": dates[0], "estimated_weight_lb": 230},
            "weight": {"current_7_day": {"average_weight_lb": 211, "end_date": dates[-1]},
                       "forward_30_day": {"robust_loss_rate_lb_per_week": 1, "window_start": dates[0], "window_end": dates[-1]}},
            "goals": goals}},
    }
    data["tiles"] = build.tiles(weight, weight, bp, data["sleep"], data["rhr"],
                                data["steps"], data["injections"], AS_OF,
                                hrv=data["hrv"], energy=data["energy"])
    data["progress"]["strength"] = build.strength_progress([
        {"session_id": session, "session_date": day, "exercise": "chest_press", "load_lb": load,
         "load_basis": "total_stack", "equipment": "synthetic_station_a", "reps": reps, "status": "completed", "rir": 2}
        for session, day, load, reps in [('example-first',dates[0],100,5),('example-latest',dates[-2],80,8),('example-latest',dates[-2],80,8)]
    ])
    build.presentation_data(data, AS_OF)
    return data
