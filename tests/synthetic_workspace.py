"""New fabricated fixtures; never read the extraction source's records or plans."""
import csv
from datetime import date, timedelta

START = date(2030, 1, 7)


def csv_file(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def program():
    templates = {
        name: {"label": "Synthetic " + name, "target_duration": "10 minutes", "warmup": [], "cooldown": [],
               "exercises": [{"exercise": "Example movement", "load": "example", "work": "example", "rir": "example", "next_target": "example"}]}
        for name in ("upper_body", "lower_body", "cardio_treadmill", "shuffle_main", "cardio_easy_sunday", "lead_in_recovery", "recovery")
    }
    schedule = {}
    for day in range(7):
        name = "cardio_treadmill" if day == 2 else "shuffle_main" if day == 5 else "recovery"
        schedule[str(day)] = {"kind": "strength", "label": "Example strength"} if day in (0, 1, 3) else {"kind": "cardio" if day == 2 else "recovery", "label": name, "template": name}
    return {
        "schema_version": 2, "program_id": "synthetic-program", "title": "Synthetic only", "canonical_source": "synthetic fixture",
        "start_date": START.isoformat(), "end_date": (START+timedelta(days=27)).isoformat(),
        "default_first_strength": "upper_body", "strength_rotation": ["upper_body", "lower_body"], "templates": templates, "schedule": schedule,
        "lead_in": {"start_date": (START-timedelta(days=2)).isoformat(), "end_date": (START-timedelta(days=1)).isoformat(), "template": "lead_in_recovery", "message": "Synthetic lead-in"},
        "health_monitoring": {"blood_pressure": {"protocol": "synthetic-protocol", "safety": "Test configuration, not advice",
            "baseline": {"start_date": (START-timedelta(days=2)).isoformat(), "end_date": (START+timedelta(days=6)).isoformat(),
                         "sessions": [{"time": t, "label": "Synthetic " + t, "work": "example"} for t in ("morning", "evening")]},
            "maintenance": {"start_date": (START+timedelta(days=7)).isoformat(), "end_date": (START+timedelta(days=27)).isoformat(), "weekday": 4,
                            "sessions": [{"time": "morning", "label": "Synthetic maintenance", "work": "example"}]}}},
        "gym_access": {"hours": {d: "Synthetic hours" for d in ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")},
                       "rule": "Synthetic timing", "latest_useful_starts": {"upper_body": {"full":"12:00","minimum":"12:15"}, "lower_body": {"full":"13:00","minimum":"13:15"}}},
    }


def weight_workspace(root, weights=None):
    values = weights if weights is not None else [190-index for index in range(14)]
    csv_file(root/"data/measurements.csv", ["measured_at_local","weight_lb"],
             [{"measured_at_local": (START+timedelta(days=i)).isoformat()+"T08:00:00+00:00", "weight_lb":v} for i,v in enumerate(values)])
    csv_file(root/"data/medication_events.csv", ["event_date","medication","event_type","injection_number"], [])
    csv_file(root/"data/goals.csv", ["goal_id","metric","direction","target_value","unit","status","priority","created_on","target_date","source","notes"],
             [{"goal_id":"synthetic-goal","metric":"weight_lb","direction":"decrease","target_value":185,"unit":"lb","status":"active","priority":"primary","created_on":START.isoformat(),"target_date":"","source":"fabricated","notes":"Test only"}])
    return root
