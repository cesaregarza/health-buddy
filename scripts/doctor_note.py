#!/usr/bin/env python3
"""Build a provenance-aware health digest for a clinician visit."""

from __future__ import annotations

import argparse
import os
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts.set_classification import is_nonworking_set
except ModuleNotFoundError:  # direct scripts/doctor_note.py execution
    from set_classification import is_nonworking_set

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]
STRENGTH_TYPES = {"upper_body", "lower_body"}
DEDICATED_AEROBIC_TYPES = {"cardio", "padel", "stair_machine", "tennis"}
SKILL_PRACTICE_TYPES = {"shuffle_practice"}
PROMINENT_SYMPTOM_MARKERS = (
    "wooz",
    "dizz",
    "faint",
    "blackout",
    "chest discomfort",
    "chest pain",
    "shortness of breath",
    "palpitation",
    "irregular heartbeat",
    "neurolog",
    "unable to keep fluids",
    "presyncope",
)
AUDIENCE_EXISTING_CLINICIAN = "existing-clinician"
AUDIENCE_NEW_CLINICIAN = "new-clinician"
AUDIENCES = (AUDIENCE_EXISTING_CLINICIAN, AUDIENCE_NEW_CLINICIAN)
HEADLINE_LABS = (
    ("A1c", {"hemoglobin a1c", "hba1c"}),
    ("Fasting glucose", {"glucose"}),
    ("LDL", {"ldl chol calc nih", "calc ldl chol", "ldl cholesterol"}),
    ("Triglycerides", {"triglycerides"}),
    ("Total cholesterol", {"cholesterol total", "cholesterol"}),
    ("Creatinine", {"creatinine"}),
    ("eGFR", {"egfr", "egfr 2021 ckd epi"}),
    ("TSH", {"tsh"}),
    ("Free T4", {"t4 free direct", "free t4"}),
    ("ALT", {"alt sgpt", "alt"}),
)


class DataError(ValueError):
    """Raised when source data cannot support a trustworthy digest."""


def require_headers(path: Path, headers: Iterable[str], actual: Sequence[str]) -> None:
    missing = sorted(set(headers) - set(actual))
    if missing:
        raise DataError(f"{path}: missing required columns: {', '.join(missing)}")


def read_rows(path: Path, required: Iterable[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise DataError(f"required data file does not exist: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        require_headers(path, required, reader.fieldnames or [])
        return [dict(row) for row in reader]


def parse_date(value: str, *, path: Path, line_number: int) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f"{path}:{line_number}: invalid date {value!r}") from exc


def parse_datetime(value: str, *, path: Path, line_number: int) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DataError(f"{path}:{line_number}: invalid datetime {value!r}") from exc


def optional_float(value: str) -> float | None:
    return float(value) if value.strip() else None


def resolve_anchor(data_dir: Path, since: str, through: date) -> dict[str, str]:
    if since != "last-visit":
        anchor = parse_date(since, path=Path("--since"), line_number=1)
        if anchor > through:
            raise DataError("--since cannot be after --through")
        return {
            "date": anchor.isoformat(),
            "kind": "explicit_date",
            "appointment_id": "",
            "label": "Explicit interval start",
        }

    path = data_dir / "appointments.csv"
    rows = read_rows(
        path,
        ("appointment_id", "appointment_date", "status", "provider", "specialty"),
    )
    completed: list[tuple[date, dict[str, str]]] = []
    for line_number, row in enumerate(rows, start=2):
        appointment_date = parse_date(
            row["appointment_date"], path=path, line_number=line_number
        )
        if row["status"].strip() == "completed" and appointment_date <= through:
            completed.append((appointment_date, row))
    if not completed:
        raise DataError(
            "appointments.csv has no completed appointment on or before "
            f"{through}; use --since YYYY-MM-DD or log the prior visit"
        )
    appointment_date, appointment = max(completed, key=lambda item: item[0])
    provider = appointment["provider"].strip()
    specialty = appointment["specialty"].strip()
    label_parts = [part for part in (provider, specialty) if part]
    return {
        "date": appointment_date.isoformat(),
        "kind": "last_completed_appointment",
        "appointment_id": appointment["appointment_id"].strip(),
        "label": ", ".join(label_parts) or "Last completed appointment",
    }


def summarize_conditions(data_dir: Path) -> list[dict[str, str]]:
    path = data_dir / "conditions.csv"
    rows = read_rows(
        path,
        ("condition_id", "condition_name", "category", "status", "source", "notes"),
    )
    return [
        {
            "condition_id": row["condition_id"].strip(),
            "condition_name": row["condition_name"].strip(),
            "category": row["category"].strip(),
            "status": row["status"].strip(),
            "source": row["source"].strip(),
            "notes": row["notes"].strip(),
        }
        for row in rows
        if row["condition_name"].strip()
    ]


def summarize_medications(data_dir: Path, start: date, through: date) -> dict[str, Any]:
    registry_path = data_dir / "medications.csv"
    registry_rows = read_rows(
        registry_path,
        (
            "medication_name",
            "active_ingredient",
            "formulation",
            "strength_value",
            "strength_unit",
            "frequency",
            "status",
            "source",
            "notes",
        ),
    )
    active = []
    for row in registry_rows:
        if row["status"].strip() != "active":
            continue
        active.append(
            {
                "medication_name": row["medication_name"].strip(),
                "active_ingredient": row["active_ingredient"].strip(),
                "formulation": row["formulation"].strip(),
                "strength_value": row["strength_value"].strip(),
                "strength_unit": row["strength_unit"].strip(),
                "frequency": row["frequency"].strip(),
                "prescribed_date": row.get("prescribed_date", "").strip(),
                "start_date": row.get("start_date", "").strip(),
                "start_date_basis": row.get("start_date_basis", "").strip(),
                "source": row["source"].strip(),
                "notes": row["notes"].strip(),
                "evidence_basis": "active medication registry; not dose-by-dose",
            }
        )

    events_path = data_dir / "medication_events.csv"
    event_rows = read_rows(
        events_path,
        (
            "event_date",
            "medication",
            "event_type",
            "injection_number",
            "dose",
            "dose_unit",
            "source",
            "notes",
        ),
    )
    events = []
    for line_number, row in enumerate(event_rows, start=2):
        event_date = parse_date(
            row["event_date"], path=events_path, line_number=line_number
        )
        if not start <= event_date <= through:
            continue
        notes = row["notes"].strip()
        events.append(
            {
                "event_date": event_date.isoformat(),
                "medication": row["medication"].strip(),
                "event_type": row["event_type"].strip(),
                "injection_number": row["injection_number"].strip(),
                "dose": row["dose"].strip(),
                "dose_unit": row["dose_unit"].strip(),
                "source": row["source"].strip(),
                "notes": notes,
                "logged_retrospectively": any(
                    marker in notes.casefold()
                    for marker in ("backfilled", "retrospectively")
                ),
                "reported_zero_missed_doses": ("zero missed doses" in notes.casefold()),
            }
        )
    return {
        "active_registry": active,
        "observed_events": events,
        "data_quality": (
            "The active registry reconciles portal and user-reported evidence and "
            "is not proof that every scheduled daily dose was taken. Start dates "
            "and dose changes retain their stated provenance; only listed "
            "medication events are event-level observations."
        ),
    }


def summarize_blood_pressure(
    data_dir: Path, start: date, through: date
) -> dict[str, Any]:
    path = data_dir / "blood_pressure.csv"
    rows = read_rows(
        path,
        (
            "measured_at_local",
            "systolic_mm_hg",
            "diastolic_mm_hg",
            "pulse_bpm",
            "measurement_session",
            "protocol_status",
            "protocol_notes",
            "notes",
        ),
    )
    readings: list[dict[str, Any]] = []
    for line_number, row in enumerate(rows, start=2):
        measured_at = parse_datetime(
            row["measured_at_local"], path=path, line_number=line_number
        )
        if not start <= measured_at.date() <= through:
            continue
        status = row["protocol_status"].strip() or "unknown"
        if status not in {"valid", "invalid", "unknown"}:
            raise DataError(f"{path}:{line_number}: invalid protocol_status")
        try:
            systolic = int(row["systolic_mm_hg"])
            diastolic = int(row["diastolic_mm_hg"])
            pulse = int(row["pulse_bpm"]) if row["pulse_bpm"].strip() else None
        except ValueError as exc:
            raise DataError(f"{path}:{line_number}: invalid BP value") from exc
        readings.append(
            {
                "measured_at_local": measured_at.isoformat(),
                "systolic_mm_hg": systolic,
                "diastolic_mm_hg": diastolic,
                "pulse_bpm": pulse,
                "measurement_session": row["measurement_session"].strip(),
                "protocol_status": status,
                "protocol_notes": row["protocol_notes"].strip(),
                "notes": row["notes"].strip(),
            }
        )

    counts = Counter(reading["protocol_status"] for reading in readings)
    valid = [reading for reading in readings if reading["protocol_status"] == "valid"]
    valid_average = None
    if valid:
        pulses = [reading["pulse_bpm"] for reading in valid if reading["pulse_bpm"]]
        valid_average = {
            "systolic_mm_hg": mean(reading["systolic_mm_hg"] for reading in valid),
            "diastolic_mm_hg": mean(reading["diastolic_mm_hg"] for reading in valid),
            "pulse_bpm": mean(pulses) if pulses else None,
        }
    sessions = {
        (
            reading["measured_at_local"][:10],
            reading["measurement_session"],
        )
        for reading in readings
    }
    return {
        "reading_count": len(readings),
        "session_count": len(sessions),
        "status_counts": {
            "valid": counts["valid"],
            "invalid": counts["invalid"],
            "unknown": counts["unknown"],
        },
        "protocol_valid_average": valid_average,
        "readings": readings,
    }


def load_daily_weights(path: Path, through: date) -> list[tuple[date, float]]:
    rows = read_rows(path, ("measured_at_local", "weight_lb"))
    grouped: dict[date, list[float]] = defaultdict(list)
    for line_number, row in enumerate(rows, start=2):
        measured_at = parse_datetime(
            row["measured_at_local"], path=path, line_number=line_number
        )
        if measured_at.date() > through:
            continue
        try:
            weight = float(row["weight_lb"])
        except ValueError as exc:
            raise DataError(f"{path}:{line_number}: invalid weight") from exc
        grouped[measured_at.date()].append(weight)
    return sorted((day, float(median(values))) for day, values in grouped.items())


def average_window(
    weights: list[tuple[date, float]], start: date, end: date
) -> dict[str, Any] | None:
    selected = [(day, weight) for day, weight in weights if start <= day <= end]
    if not selected:
        return None
    center_ordinal = mean(day.toordinal() for day, _ in selected)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "reading_count": len(selected),
        "average_weight_lb": mean(weight for _, weight in selected),
        "center_ordinal": center_ordinal,
    }


def summarize_weight(data_dir: Path, start: date, through: date) -> dict[str, Any]:
    all_weights = load_daily_weights(data_dir / "measurements.csv", through)
    weights = [(day, value) for day, value in all_weights if start <= day <= through]
    if not weights:
        return {"reading_count": 0, "data_quality": "No weights in interval"}

    first_day = weights[0][0]
    last_day = weights[-1][0]
    first = average_window(
        weights, first_day, min(first_day + timedelta(days=6), last_day)
    )
    latest = average_window(
        weights, max(first_day, last_day - timedelta(days=6)), last_day
    )
    assert first is not None and latest is not None
    elapsed_days = latest["center_ordinal"] - first["center_ordinal"]
    pace = None
    if elapsed_days > 0:
        pace = (
            (latest["average_weight_lb"] - first["average_weight_lb"])
            * 7.0
            / elapsed_days
        )

    rolling_series = []
    for day, _ in weights:
        window = average_window(weights, max(start, day - timedelta(days=6)), day)
        assert window is not None
        rolling_series.append(
            {
                "date": day.isoformat(),
                "average_weight_lb": window["average_weight_lb"],
                "reading_count": window["reading_count"],
            }
        )
    return {
        "reading_count": len(weights),
        "coverage_start": first_day.isoformat(),
        "coverage_end": last_day.isoformat(),
        "first_7_day_window": first,
        "latest_7_day_window": latest,
        "change_between_window_averages_lb": (
            latest["average_weight_lb"] - first["average_weight_lb"]
        ),
        "average_pace_lb_per_week": pace,
        "rolling_7_day_series": rolling_series,
        "data_quality": (
            "Daily readings are reduced to one median value per date; window "
            "averages may contain fewer than seven readings and report their count."
        ),
    }


def summarize_symptoms(
    data_dir: Path, start: date, through: date
) -> list[dict[str, Any]]:
    path = data_dir / "daily_checkins.csv"
    rows = read_rows(path, ("date", "other_symptoms", "source", "notes"))
    symptoms = []
    for line_number, row in enumerate(rows, start=2):
        day = parse_date(row["date"], path=path, line_number=line_number)
        symptom = row["other_symptoms"].strip()
        if not symptom or not start <= day <= through:
            continue
        notes = row["notes"].strip()
        combined = f"{symptom} {notes}".casefold()
        symptoms.append(
            {
                "date": day.isoformat(),
                "reported_symptom": symptom,
                "context_notes": notes,
                "source": row["source"].strip(),
                "prominent": any(
                    marker in combined for marker in PROMINENT_SYMPTOM_MARKERS
                ),
            }
        )
    return sorted(symptoms, key=lambda item: (not item["prominent"], item["date"]))


def working_set(row: dict[str, str]) -> bool:
    status = row["status"].strip()
    if status == "reported_aggregate":
        return True
    return status == "completed" and not is_nonworking_set(status, row["notes"])


def summarize_training(data_dir: Path, start: date, through: date) -> dict[str, Any]:
    sessions_path = data_dir / "sessions.csv"
    sessions = read_rows(
        sessions_path,
        ("session_id", "date", "workout_type", "status", "duration_min", "notes"),
    )
    completed = []
    for line_number, row in enumerate(sessions, start=2):
        session_date = parse_date(
            row["date"], path=sessions_path, line_number=line_number
        )
        if row["status"].strip() == "complete" and start <= session_date <= through:
            completed.append({**row, "parsed_date": session_date})

    strength = [row for row in completed if row["workout_type"] in STRENGTH_TYPES]
    aerobic = [
        row for row in completed if row["workout_type"] in DEDICATED_AEROBIC_TYPES
    ]
    skill_practice = [
        row for row in completed if row["workout_type"] in SKILL_PRACTICE_TYPES
    ]
    aerobic_minutes = sum(
        float(row["duration_min"]) for row in aerobic if row["duration_min"].strip()
    )
    missing_aerobic_duration = sum(not row["duration_min"].strip() for row in aerobic)
    skill_practice_minutes = sum(
        float(row["duration_min"])
        for row in skill_practice
        if row["duration_min"].strip()
    )
    missing_skill_practice_duration = sum(
        not row["duration_min"].strip() for row in skill_practice
    )
    interval_weeks = max(1, (through - start).days + 1) / 7.0
    strength_coverage_start = (
        min(row["parsed_date"] for row in strength) if strength else None
    )
    strength_active_weeks = (
        max(1, (through - strength_coverage_start).days + 1) / 7.0
        if strength_coverage_start
        else None
    )

    sets_path = data_dir / "sets.csv"
    sets = read_rows(
        sets_path,
        (
            "session_id",
            "session_date",
            "exercise",
            "equipment",
            "set_count",
            "load_lb",
            "load_basis",
            "reps",
            "status",
            "notes",
        ),
    )
    completed_ids = {row["session_id"] for row in strength}
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for line_number, row in enumerate(sets, start=2):
        session_date = parse_date(
            row["session_date"], path=sets_path, line_number=line_number
        )
        if row["session_id"] not in completed_ids or not working_set(row):
            continue
        try:
            reps = int(row["reps"])
            load = optional_float(row["load_lb"])
        except ValueError as exc:
            raise DataError(f"{sets_path}:{line_number}: invalid set value") from exc
        groups[
            (
                row["exercise"].strip(),
                row["equipment"].strip(),
                row["load_basis"].strip(),
            )
        ].append(
            {
                "date": session_date,
                "session_id": row["session_id"].strip(),
                "load_lb": load,
                "reps": reps,
            }
        )

    comparisons = []
    for (exercise, equipment, load_basis), rows in sorted(groups.items()):
        sessions_by_key: dict[tuple[date, str], list[dict[str, Any]]] = defaultdict(
            list
        )
        for row in rows:
            sessions_by_key[(row["date"], row["session_id"])].append(row)
        if len(sessions_by_key) < 2:
            continue
        ordered = sorted(sessions_by_key.items())
        first_key, first_rows = ordered[0]
        latest_key, latest_rows = ordered[-1]

        def top_set(items: list[dict[str, Any]]) -> dict[str, Any]:
            with_load = [item for item in items if item["load_lb"] is not None]
            if not with_load:
                return {"load_lb": None, "reps": max(item["reps"] for item in items)}
            top_load = max(item["load_lb"] for item in with_load)
            return {
                "load_lb": top_load,
                "reps": max(
                    item["reps"] for item in with_load if item["load_lb"] == top_load
                ),
            }

        comparisons.append(
            {
                "exercise": exercise,
                "equipment": equipment,
                "load_basis": load_basis,
                "sessions_observed": len(sessions_by_key),
                "first": {"date": first_key[0].isoformat(), **top_set(first_rows)},
                "latest": {"date": latest_key[0].isoformat(), **top_set(latest_rows)},
            }
        )

    latest_by_type = {}
    for workout_type in sorted(STRENGTH_TYPES):
        candidates = [row for row in strength if row["workout_type"] == workout_type]
        if candidates:
            latest = max(candidates, key=lambda row: row["parsed_date"])
            latest_by_type[workout_type] = {
                "date": latest["parsed_date"].isoformat(),
                "notes": latest["notes"].strip(),
            }
    return {
        "completed_sessions": len(completed),
        "strength_sessions": len(strength),
        "strength_sessions_per_week": len(strength) / interval_weeks,
        "strength_coverage_start": (
            strength_coverage_start.isoformat() if strength_coverage_start else None
        ),
        "strength_sessions_per_active_week": (
            len(strength) / strength_active_weeks if strength_active_weeks else None
        ),
        "dedicated_aerobic_sessions": len(aerobic),
        "dedicated_aerobic_minutes": aerobic_minutes,
        "aerobic_sessions_missing_duration": missing_aerobic_duration,
        "skill_practice_sessions": len(skill_practice),
        "skill_practice_minutes": skill_practice_minutes,
        "skill_practice_sessions_missing_duration": missing_skill_practice_duration,
        "latest_strength_sessions": latest_by_type,
        "repeated_exercise_comparisons": comparisons,
    }


def summarize_labs(data_dir: Path, start: date, through: date) -> list[dict[str, str]]:
    path = data_dir / "labs.csv"
    rows = read_rows(
        path,
        (
            "collected_on",
            "test_name",
            "value",
            "unit",
            "reference_low",
            "reference_high",
            "flag",
            "fasting",
            "source",
            "notes",
        ),
    )
    labs = []
    for line_number, row in enumerate(rows, start=2):
        collected_on = parse_date(
            row["collected_on"], path=path, line_number=line_number
        )
        if start <= collected_on <= through:
            labs.append({**row, "collected_on": collected_on.isoformat()})
    return labs


def _normalized_lab_name(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _is_out_of_range_lab(row: dict[str, str]) -> bool:
    flag = row["flag"].strip().lower()
    return bool(flag) and flag not in {"normal", "in range", "within range"}


def _numeric_lab_value(row: dict[str, str]) -> float | None:
    try:
        return float(row["value"])
    except ValueError:
        return None


def summarize_lab_note(
    data_dir: Path,
    start: date,
    through: date,
    interval_labs: list[dict[str, str]],
) -> dict[str, Any]:
    """Condense interval labs for a chart-aware established clinician."""
    path = data_dir / "labs.csv"
    all_rows = read_rows(
        path,
        (
            "collected_on",
            "test_name",
            "value",
            "unit",
            "reference_low",
            "reference_high",
            "flag",
            "fasting",
            "source",
            "notes",
        ),
    )
    history: list[dict[str, str]] = []
    for line_number, row in enumerate(all_rows, start=2):
        collected_on = parse_date(
            row["collected_on"], path=path, line_number=line_number
        )
        if collected_on <= through:
            history.append({**row, "collected_on": collected_on.isoformat()})

    out_of_range = [row for row in interval_labs if _is_out_of_range_lab(row)]
    headline: list[dict[str, Any]] = []
    displayed_ids = {id(row) for row in out_of_range}
    for label, aliases in HEADLINE_LABS:
        current_candidates = [
            row
            for row in interval_labs
            if _normalized_lab_name(row["test_name"]) in aliases
            and _numeric_lab_value(row) is not None
        ]
        if not current_candidates:
            continue
        current = max(current_candidates, key=lambda row: row["collected_on"])
        current_date = current["collected_on"]
        previous_candidates = [
            row
            for row in history
            if row["collected_on"] < current_date
            and _normalized_lab_name(row["test_name"]) in aliases
            and _numeric_lab_value(row) is not None
        ]
        previous = (
            max(previous_candidates, key=lambda row: row["collected_on"])
            if previous_candidates
            else None
        )
        current_value = _numeric_lab_value(current)
        previous_value = _numeric_lab_value(previous) if previous else None
        headline.append(
            {
                "label": label,
                "current": current,
                "previous": previous,
                "delta": (
                    current_value - previous_value
                    if current_value is not None and previous_value is not None
                    else None
                ),
            }
        )
        displayed_ids.add(id(current))

    return {
        "out_of_range": out_of_range,
        "headline_deltas": headline,
        "additional_unflagged_count": sum(
            1
            for row in interval_labs
            if id(row) not in displayed_ids and not _is_out_of_range_lab(row)
        ),
        "interval_result_count": len(interval_labs),
    }


def summarize_instructions(
    data_dir: Path, start: date, through: date
) -> list[dict[str, str]]:
    path = data_dir / "clinical_instructions.csv"
    rows = read_rows(
        path,
        (
            "instruction_id",
            "appointment_id",
            "instruction_date",
            "instruction",
            "status",
            "source",
            "notes",
        ),
    )
    instructions = []
    for line_number, row in enumerate(rows, start=2):
        instruction_date = parse_date(
            row["instruction_date"], path=path, line_number=line_number
        )
        if instruction_date <= through and (
            instruction_date >= start or row["status"].strip() == "active"
        ):
            instructions.append(
                {**row, "instruction_date": instruction_date.isoformat()}
            )
    return instructions


def summarize_clinician_questions(
    data_dir: Path, start: date, through: date
) -> list[dict[str, str]]:
    path = data_dir / "clinician_questions.csv"
    if not path.exists():
        return []
    rows = read_rows(
        path,
        ("question_id", "recorded_on", "question", "status", "source", "notes"),
    )
    questions = []
    allowed_statuses = {"active", "carried", "answered", "withdrawn", "context"}
    for line_number, row in enumerate(rows, start=2):
        recorded_on = parse_date(row["recorded_on"], path=path, line_number=line_number)
        status = row["status"].strip()
        if status not in allowed_statuses:
            raise DataError(f"{path}:{line_number}: invalid question status {status!r}")
        if recorded_on <= through and (
            recorded_on >= start or status in {"active", "carried"}
        ):
            questions.append({**row, "recorded_on": recorded_on.isoformat()})
    return questions


def generated_questions(digest: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the existing generated suggestions with their trigger provenance."""
    questions = []
    symptoms = digest["symptoms_and_episodes"]
    if any(item["prominent"] for item in symptoms):
        questions.append((
            "Could we review the positional wooziness episodes in the context of "
            "weight loss, food and fluid intake, blood pressure, and metoprolol?",
            "generated from a prominent symptom in the interval",
        ))
    bp = digest["home_blood_pressure"]
    if bp["status_counts"]["valid"] == 0:
        questions.append((
            "What home blood-pressure cadence and measurement conditions would you "
            "like me to use before the next visit?",
            "generated because the interval has no protocol-valid blood-pressure readings",
        ))
    weight = digest["weight"]
    pace = weight.get("average_pace_lb_per_week")
    latest = weight.get("latest_7_day_window", {}).get("average_weight_lb")
    if pace is not None and latest and pace < 0 and abs(pace) / latest >= 0.01:
        questions.append((
            "Is the observed average weight-loss pace appropriate given my current "
            "medications, intake, symptoms, and resistance training?",
            "generated from the interval's observed average weight-loss pace",
        ))
    questions.append((
        "Do you want any medication, laboratory, symptom, or home-monitoring changes "
        "tracked before the next visit?",
        "generated as the standard follow-up question",
    ))
    return questions


def build_prepared_questions(digest: dict[str, Any]) -> list[dict[str, Any]]:
    """Structure exactly the unique questions rendered in the markdown note."""
    prepared = []
    seen = set()
    for item in digest.get("patient_questions", []):
        question = item.get("question", "").strip()
        if item.get("status") not in {"active", "carried", "pending"} or not question or question in seen:
            continue
        seen.add(question)
        origin = item.get("origin") or "canonical"
        prepared.append({
            "question_id": item.get("question_id", ""), "question": question,
            "status": item["status"], "topic": item.get("topic", ""),
            "notes": item.get("notes", ""), "prov": bool(item.get("prov", False)),
            "origin": origin,
        })
    for index, (question, provenance) in enumerate(generated_questions(digest), start=1):
        if question in seen:
            continue
        seen.add(question)
        prepared.append({
            "question_id": f"generated-{index}", "question": question,
            "status": "suggested", "topic": "", "notes": provenance, "prov": False,
            "origin": "generated",
        })
    return prepared


def build_questions(digest: dict[str, Any]) -> list[str]:
    """Compatibility helper returning the note's deduplicated question text."""
    return [item["question"] for item in build_prepared_questions(digest)]


def build_digest(
    repo_root: Path,
    *,
    since: str,
    through: date,
    audience: str = AUDIENCE_EXISTING_CLINICIAN,
    additional_questions: Sequence[dict[str, str]] = (),
) -> dict[str, Any]:
    if audience not in AUDIENCES:
        raise DataError(
            f"invalid audience {audience!r}; expected one of: {', '.join(AUDIENCES)}"
        )
    data_dir = repo_root / "data"
    anchor = resolve_anchor(data_dir, since, through)
    start = date.fromisoformat(anchor["date"])
    interval_labs = summarize_labs(data_dir, start, through)
    digest: dict[str, Any] = {
        "generated_on": datetime.now(ZoneInfo(os.environ.get("HEALTH_TIMEZONE", "UTC"))).date().isoformat(),
        "audience": audience,
        "interval": {
            "start_inclusive": start.isoformat(),
            "end_inclusive": through.isoformat(),
            "days": (through - start).days + 1,
            "anchor": anchor,
        },
        "standing_history": summarize_conditions(data_dir),
        "prior_clinical_instructions": summarize_instructions(data_dir, start, through),
        "patient_questions": summarize_clinician_questions(data_dir, start, through),
        "medications": summarize_medications(data_dir, start, through),
        "home_blood_pressure": summarize_blood_pressure(data_dir, start, through),
        "symptoms_and_episodes": summarize_symptoms(data_dir, start, through),
        "weight": summarize_weight(data_dir, start, through),
        "training": summarize_training(data_dir, start, through),
        "labs": interval_labs,
        "lab_note_summary": summarize_lab_note(
            data_dir, start, through, interval_labs
        ),
    }
    caveats = [digest["medications"]["data_quality"]]
    bp = digest["home_blood_pressure"]
    if bp["status_counts"]["valid"] == 0:
        caveats.append(
            "No interval blood-pressure readings have all protocol conditions "
            "documented as valid; no protocol-valid average is reported."
        )
    if digest["weight"].get("coverage_start") != start.isoformat():
        caveats.append(
            "Weight coverage begins on "
            f"{digest['weight'].get('coverage_start', 'no recorded date')}, after "
            "the interval anchor."
        )
    if not digest["labs"]:
        caveats.append("No laboratory results are logged in this interval.")
    digest["data_quality_caveats"] = caveats
    for item in additional_questions:
        question_id = (item.get("question_id") or "").strip()
        question = (item.get("question") or "").strip()
        if not question_id or not question:
            continue
        if any(existing.get("question_id") == question_id for existing in digest["patient_questions"]):
            continue
        recorded_on = (item.get("recorded_on") or "").strip()
        if recorded_on and parse_date(recorded_on, path=Path("--additional-questions"), line_number=1) > through:
            continue
        status = (item.get("status") or "pending").strip()
        if status not in {"active", "carried", "pending", "answered", "withdrawn", "context"}:
            raise DataError(f"invalid additional question status {status!r}")
        digest["patient_questions"].append({
            "question_id": question_id, "recorded_on": recorded_on,
            "question": question, "status": status, "topic": item.get("topic", ""),
            "source": item.get("source", "pending"), "notes": item.get("notes", ""),
            "prov": True, "origin": "pending",
        })
    digest["prepared_questions"] = build_prepared_questions(digest)
    digest["questions_for_clinician"] = [item["question"] for item in digest["prepared_questions"]]
    return digest


def format_decimal(value: float | None, digits: int = 1) -> str:
    return "not available" if value is None else f"{value:.{digits}f}"


def format_lab_value(row: dict[str, str]) -> str:
    return " ".join(part for part in (row["value"], row["unit"]) if part)


def format_lab_delta(value: float) -> str:
    return f"{value:+g}".replace("-", "−")


def medication_label(item: dict[str, str]) -> str:
    parts = [item["medication_name"]]
    strength = " ".join(
        part for part in (item["strength_value"], item["strength_unit"]) if part
    )
    if strength:
        parts.append(strength)
    if item["formulation"]:
        parts.append(item["formulation"])
    if item["frequency"]:
        parts.append(item["frequency"])
    return " — ".join(parts)


def symptom_context_excerpt(notes: str) -> str:
    """Keep the first context sentence and safety-relevant follow-up sentences."""

    notes = re.sub(r"^context:\s*", "", notes, flags=re.IGNORECASE)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", notes) if part]
    if len(sentences) <= 2:
        return notes
    selected = [sentences[0]]
    follow_up_markers = (
        "follow-up",
        "no blackout",
        "resolved",
        "no chest",
        "without recurrent",
        "felt substantially better",
    )
    for sentence in sentences[1:]:
        if any(marker in sentence.casefold() for marker in follow_up_markers):
            selected.append(sentence)
        if len(selected) == 3:
            break
    return " ".join(selected)


def clearly_increased(item: dict[str, Any]) -> bool:
    first = item["first"]
    latest = item["latest"]
    first_load = first["load_lb"]
    latest_load = latest["load_lb"]
    if first_load is None or latest_load is None:
        return latest["reps"] > first["reps"]
    return latest_load > first_load or (
        latest_load == first_load and latest["reps"] > first["reps"]
    )


def medication_event_lines(events: list[dict[str, Any]]) -> list[str]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[(event["medication"], event["event_type"])].append(event)

    lines = []
    for (medication, event_type), items in sorted(grouped.items()):
        details = []
        injection_numbers = sorted(
            {
                int(item["injection_number"])
                for item in items
                if item["injection_number"].isdigit()
            }
        )
        if injection_numbers:
            if len(injection_numbers) == 1:
                details.append(f"injection #{injection_numbers[0]}")
            elif len(injection_numbers) == (
                injection_numbers[-1] - injection_numbers[0] + 1
            ):
                details.append(
                    f"injections #{injection_numbers[0]}-#{injection_numbers[-1]}"
                )
            else:
                details.append(
                    "injections "
                    + ", ".join(f"#{number}" for number in injection_numbers)
                )
        doses = sorted(
            {
                " ".join(part for part in (item["dose"], item["dose_unit"]) if part)
                for item in items
                if item["dose"] or item["dose_unit"]
            }
        )
        if len(doses) == 1:
            details.append(doses[0])
        retrospective = sum(item["logged_retrospectively"] for item in items)
        if retrospective:
            details.append(f"{retrospective} logged retrospectively")
        detail_text = f" ({'; '.join(details)})" if details else ""
        event_word = "event" if len(items) == 1 else "events"
        lines.append(
            f"- {medication}: {len(items)} {event_type} {event_word} in this interval"
            f"{detail_text}."
        )
    no_missed_dose_reports = [
        event for event in events if event["reported_zero_missed_doses"]
    ]
    if no_missed_dose_reports:
        latest = max(
            no_missed_dose_reports,
            key=lambda item: (
                item["event_date"],
                int(item["injection_number"])
                if item["injection_number"].isdigit()
                else -1,
            ),
        )
        injection = (
            f" through injection #{latest['injection_number']}"
            if latest["injection_number"]
            else ""
        )
        lines.append(
            f"- Adherence report: user reported zero missed doses{injection}; "
            "this is self-reported, not independently verified."
        )
    return lines


def medication_started_in_interval(
    item: dict[str, str], interval: dict[str, Any]
) -> bool:
    start_date = item["start_date"]
    return bool(
        start_date
        and interval["start_inclusive"] <= start_date <= interval["end_inclusive"]
    )


def medication_relevant_to_wooziness(item: dict[str, str]) -> bool:
    text = f"{item['medication_name']} {item['active_ingredient']}".casefold()
    return "metoprolol" in text


def render_markdown(digest: dict[str, Any]) -> str:
    interval = digest["interval"]
    anchor = interval["anchor"]
    audience = digest.get("audience", AUDIENCE_EXISTING_CLINICIAN)
    existing_clinician = audience == AUDIENCE_EXISTING_CLINICIAN
    title = (
        "# Patient-prepared since-last-visit update"
        if existing_clinician
        else "# Patient-prepared transfer summary"
    )
    lines = [
        title,
        "",
        (
            f"**Interval:** {interval['start_inclusive']} through "
            f"{interval['end_inclusive']} ({interval['days']} days)  "
        ),
        f"**Anchor:** {anchor['label']} ({anchor['kind']})  ",
        (
            "**Audience:** Established clinician with access to the existing chart.  "
            if existing_clinician
            else "**Audience:** New clinician or transfer of care.  "
        ),
        (
            "**Purpose:** Recall aid from self-tracked data; not a diagnosis "
            "or treatment plan."
        ),
        "",
        "## Interval summary and data quality",
        "",
    ]
    caveats = digest["data_quality_caveats"]
    if existing_clinician:
        caveats = [
            caveat
            for caveat in caveats
            if not caveat.startswith("The active registry")
            and not caveat.startswith("No laboratory results")
        ]
    for caveat in caveats:
        lines.append(f"- {caveat}")
    if not existing_clinician and digest["standing_history"]:
        history = "; ".join(
            item["condition_name"] for item in digest["standing_history"]
        )
        lines.append(f"- Relevant standing history: {history}.")
    if digest["prior_clinical_instructions"]:
        heading = (
            "- Follow-up on the prior plan:"
            if existing_clinician
            else "- Prior clinician instructions being reviewed:"
        )
        lines.append(heading)
        for item in digest["prior_clinical_instructions"]:
            lines.append(
                f"  - {item['instruction_date']}: {item['instruction']} "
                f"({item['status']})"
            )

    medication_heading = (
        "## Interval medication context"
        if existing_clinician
        else "## Medications as logged"
    )
    lines.extend(["", medication_heading, ""])
    active_medications = digest["medications"]["active_registry"]
    if existing_clinician:
        lines.append(
            "- The full current medication list remains in the clinician chart and "
            "is not repeated here."
        )
        for item in active_medications:
            if medication_started_in_interval(item, interval):
                start_basis = item["start_date_basis"] or "source as logged"
                lines.append(
                    f"- Started during this interval: {medication_label(item)} "
                    f"on {item['start_date']} ({start_basis})."
                )
        has_prominent_symptoms = any(
            item["prominent"] for item in digest["symptoms_and_episodes"]
        )
        if has_prominent_symptoms:
            for item in active_medications:
                if medication_relevant_to_wooziness(item):
                    label = medication_label(item)
                    lines.append(
                        f"- Symptom-review context: {label} is flagged "
                        "for clinician review alongside home BP/pulse and positional "
                        "wooziness; no causal claim is made."
                    )
    else:
        for item in active_medications:
            lines.append(
                f"- {medication_label(item)} *(active registry; not dose-by-dose)*"
            )
            timing_parts = []
            if item["prescribed_date"]:
                timing_parts.append(
                    f"Portal prescription/order date {item['prescribed_date']} "
                    "(not assumed to be initiation)."
                )
            if item["start_date_basis"]:
                timing_parts.append(item["start_date_basis"])
            if timing_parts:
                lines.append(f"  - Prescription/start timing: {' '.join(timing_parts)}")
    events = digest["medications"]["observed_events"]
    if events:
        lines.append("- Dose-level events in interval:")
        lines.extend(f"  {line}" for line in medication_event_lines(events))
        lines.append(
            "  - Event counts cover only this interval and are not lifetime "
            "dose counts."
        )
    else:
        lines.append("- No dose-level medication events logged in the interval.")

    bp = digest["home_blood_pressure"]
    counts = bp["status_counts"]
    lines.extend(
        [
            "",
            "## Home blood pressure",
            "",
            (
                f"- {bp['reading_count']} readings across "
                f"{bp['session_count']} sessions: "
                f"{counts['valid']} valid, {counts['invalid']} invalid, "
                f"{counts['unknown']} protocol status unknown."
            ),
        ]
    )
    average = bp["protocol_valid_average"]
    if average:
        lines.append(
            "- Protocol-valid average: "
            f"{average['systolic_mm_hg']:.1f}/{average['diastolic_mm_hg']:.1f} "
            "mm Hg."
        )
    else:
        lines.append("- Protocol-valid average: not calculated.")

    lines.extend(["", "## Symptoms and episodes", ""])
    if not digest["symptoms_and_episodes"]:
        lines.append("- No symptoms logged in the interval.")
    for item in digest["symptoms_and_episodes"]:
        marker = "**Prominent:** " if item["prominent"] else ""
        lines.append(f"- {marker}{item['date']}: “{item['reported_symptom']}”")
        if item["context_notes"]:
            lines.append(
                f"  - Context: {symptom_context_excerpt(item['context_notes'])}"
            )

    weight = digest["weight"]
    training = digest["training"]
    lines.extend(["", "## Weight and training", ""])
    if weight["reading_count"]:
        first = weight["first_7_day_window"]
        latest = weight["latest_7_day_window"]
        lines.append(
            f"- Weight coverage {weight['coverage_start']} through "
            f"{weight['coverage_end']}: first window average "
            f"{first['average_weight_lb']:.1f} lb ({first['reading_count']} readings) "
            f"to latest window average {latest['average_weight_lb']:.1f} lb "
            f"({latest['reading_count']} readings); observed weight-coverage pace "
            f"{format_decimal(weight['average_pace_lb_per_week'])} lb/week."
        )
    else:
        lines.append("- Weight: no interval readings.")
    if training["strength_coverage_start"]:
        strength_summary = (
            f"{training['strength_sessions']} strength sessions from the first "
            f"interval strength session on {training['strength_coverage_start']} "
            f"({training['strength_sessions_per_active_week']:.1f}/week over the "
            "active training span)"
        )
    else:
        strength_summary = "no recorded strength sessions"
    lines.append(
        f"- Training: {strength_summary}; "
        f"{training['dedicated_aerobic_sessions']} dedicated aerobic sessions "
        f"({training['dedicated_aerobic_minutes']:.0f} recorded minutes)."
    )
    if training["skill_practice_sessions"]:
        session_word = (
            "session" if training["skill_practice_sessions"] == 1 else "sessions"
        )
        lines.append(
            "- Movement-skill practice: "
            f"{training['skill_practice_sessions']} shuffle {session_word} "
            f"({training['skill_practice_minutes']:.0f} recorded practice minutes); "
            "tutorial and reset time is not automatically moderate aerobic work."
        )
    comparisons = [
        item
        for item in training["repeated_exercise_comparisons"]
        if clearly_increased(item)
    ][:6]
    if comparisons:
        lines.append("- Selected increasing same-basis lift comparisons:")
        for item in comparisons:
            first = item["first"]
            latest = item["latest"]
            lines.append(
                f"  - {item['exercise']} ({item['load_basis']}): "
                f"{first['load_lb']} lb x {first['reps']} on {first['date']} -> "
                f"{latest['load_lb']} lb x {latest['reps']} on {latest['date']}"
            )

    lines.extend(["", "## New labs", ""])
    if digest["labs"]:
        summary = digest["lab_note_summary"]
        if summary["out_of_range"]:
            lines.append("- Out-of-range results:")
        else:
            lines.append("- Out-of-range results: none flagged in the interval.")
        for lab in summary["out_of_range"]:
            lines.append(
                f"  - {lab['collected_on']}: {lab['test_name']} "
                f"{format_lab_value(lab)} [{lab['flag']}]"
            )
        if summary["headline_deltas"]:
            lines.append("- Headline results and changes from the most recent prior draw:")
            for item in summary["headline_deltas"]:
                current = item["current"]
                previous = item["previous"]
                if previous is None:
                    lines.append(
                        f"  - {item['label']}: {format_lab_value(current)} on "
                        f"{current['collected_on']} (no prior result logged)."
                    )
                    continue
                lines.append(
                    f"  - {item['label']}: {format_lab_value(previous)} on "
                    f"{previous['collected_on']} -> {format_lab_value(current)} on "
                    f"{current['collected_on']} (change "
                    f"{format_lab_delta(item['delta'])})."
                )
        additional = summary["additional_unflagged_count"]
        if additional:
            lines.append(
                f"- {additional} additional interval results had no out-of-range "
                "flag and are omitted here; the complete panel remains in the "
                "canonical lab log."
            )
    else:
        lines.append("- No new laboratory results logged.")

    lines.extend(["", "## Questions for the clinician", ""])
    for item in digest["prepared_questions"]:
        if item["origin"] == "generated":
            label = "[Suggested discussion] "
        elif item["origin"] == "pending" or item["prov"]:
            label = "[not yet in the log] "
        elif item["status"] == "carried":
            label = "[Carried question] "
        else:
            label = ""
        lines.append(f"- {label}{item['question']}")
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a provenance-aware health digest for a clinician visit."
    )
    parser.add_argument(
        "--since",
        required=True,
        help="Inclusive YYYY-MM-DD interval start or 'last-visit'.",
    )
    parser.add_argument(
        "--through",
        type=date.fromisoformat,
        default=date.today(),
        help="Inclusive interval end (default: today).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help="Workout-log repository root.",
    )
    parser.add_argument(
        "--audience",
        choices=AUDIENCES,
        default=AUDIENCE_EXISTING_CLINICIAN,
        help=(
            "Note audience. Existing-clinician avoids repeating chart history; "
            "new-clinician includes transfer context."
        ),
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument(
        "--additional-questions",
        type=Path,
        help="JSON array of pending question records.",
    )
    parser.add_argument("--output", type=Path, help="Write output to this path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        additional_questions = []
        if args.additional_questions:
            additional_questions = json.loads(args.additional_questions.read_text(encoding="utf-8"))
            if not isinstance(additional_questions, list):
                raise DataError("--additional-questions must contain a JSON array")
        digest = build_digest(
            args.repo_root.resolve(),
            since=args.since,
            through=args.through,
            audience=args.audience,
            additional_questions=additional_questions,
        )
    except (DataError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    output = (
        json.dumps({**digest, "note_markdown": render_markdown(digest)}, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else render_markdown(digest)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
