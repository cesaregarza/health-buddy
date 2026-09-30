"""Schedule regressions use an authored program, never a user's plan."""
import copy
import json
from datetime import timedelta

import pytest

from scripts.next_workout import ProgramError, Session, build_preview, build_recommendation, gym_timing_for_date, load_program, next_strength_type, render_text
from tests.synthetic_workspace import START, program


def test_validation_and_invalid_schema(tmp_path):
    path = tmp_path/"program.json"
    path.write_text(json.dumps(program()))
    assert load_program(path)["program_id"] == "synthetic-program"
    path.write_text(json.dumps({"schema_version":2,"title":"incomplete"}))
    with pytest.raises(ProgramError, match="missing program_id"):
        load_program(path)


def test_rotation_uses_completed_sessions_only():
    sessions = [Session(START,"upper_body","complete"), Session(START+timedelta(days=1),"lower_body","partial")]
    assert next_strength_type(sessions,program(),START+timedelta(days=2)) == "lower_body"
    sessions.append(Session(START+timedelta(days=1),"lower_body","complete"))
    assert next_strength_type(sessions,program(),START+timedelta(days=2)) == "upper_body"


def test_timing_override_precedes_template_default():
    p = program()
    p["gym_access"]["weekday_latest_useful_starts"] = {"monday":{"upper_body":{"full":"11:00","minimum":"11:15"}}}
    assert gym_timing_for_date(p,START,"upper_body")["latest_full_start"] == "11:00"
    assert gym_timing_for_date(p,START+timedelta(days=1),"upper_body")["latest_full_start"] == "12:00"


def test_lead_in_and_not_started():
    result = build_recommendation(program(),[],START-timedelta(days=1))
    assert result["status"] == "lead_in" and result["next_strength_date"] == START.isoformat()
    assert len(result["health_tasks"]) == 2
    assert build_recommendation(program(),[],START-timedelta(days=3))["status"] == "not_started"


def test_monitoring_phases_and_rendering():
    baseline = build_recommendation(program(),[],START)
    assert [x["time"] for x in baseline["health_tasks"]] == ["morning","evening"]
    assert [x["phase"] for x in build_recommendation(program(),[],START+timedelta(days=11))["health_tasks"]] == ["maintenance"]
    assert build_recommendation(program(),[],START+timedelta(days=10))["health_tasks"] == []
    assert "synthetic-protocol" in render_text(baseline)


def test_non_strength_days_and_explicit_override():
    p=program(); sessions=[Session(START,"upper_body","complete")]
    result=build_recommendation(p,sessions,START+timedelta(days=2))
    assert result["template_name"] == "cardio_treadmill" and result["next_strength_type"] == "lower_body"
    assert result["next_strength_date"] == (START+timedelta(days=3)).isoformat()
    p["date_overrides"]={(START+timedelta(days=2)).isoformat():{"kind":"recovery","label":"Example","template":"recovery"}}
    assert build_recommendation(p,sessions,START+timedelta(days=2))["template_name"] == "recovery"


def test_completed_day_and_expired_program():
    result=build_recommendation(program(),[Session(START,"upper_body","complete")],START)
    assert result["status"] == "completed_today" and result["next_strength_type"] == "lower_body"
    assert result["next_strength_date"] == (START+timedelta(days=1)).isoformat()
    ended=build_recommendation(program(),[],START+timedelta(days=28))
    assert ended["status"] == "review_due" and ended["template"] is None


def test_exercise_availability_and_first_session_of_week():
    p=program()
    p["templates"]["upper_body"]["exercises"].append({"exercise":"Synthetic trial","available_from":(START+timedelta(days=7)).isoformat(),"first_session_of_week_only":True})
    assert len(build_recommendation(p,[],START)["template"]["exercises"]) == 1
    assert len(build_recommendation(p,[],START+timedelta(days=7))["template"]["exercises"]) == 2
    sessions=[Session(START+timedelta(days=7),"upper_body","complete"),Session(START+timedelta(days=8),"lower_body","complete")]
    assert len(build_recommendation(p,sessions,START+timedelta(days=10))["template"]["exercises"]) == 1


def test_preview_is_conditional_and_does_not_mutate_history():
    p=program(); original=copy.deepcopy(p)
    result=build_preview(p,[],START,4)
    assert p == original and result["mode"] == "conditional_preview"
    assert result["days"][0]["assumed_completed_sessions"] == []
    assert result["days"][1]["assumed_completed_sessions"] == [{"date":START.isoformat(),"type":"upper_body"}]
    assert [x["recommendation"]["template_name"] for x in result["days"]] == ["upper_body","lower_body","cardio_treadmill","upper_body"]


@pytest.mark.parametrize("days", [0,-1,15])
def test_preview_window(days):
    with pytest.raises(ProgramError,match="1 and 14"):
        build_preview(program(),[],START,days)


@pytest.mark.parametrize("status", ["complete","partial"])
def test_preview_rejects_recorded_attendance(status):
    with pytest.raises(ProgramError,match="after recorded"):
        build_preview(program(),[Session(START,"upper_body",status)],START,2)
    with pytest.raises(ProgramError,match="active program"):
        build_preview(program(),[],START+timedelta(days=28),2)
