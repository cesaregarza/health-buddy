#!/usr/bin/env python3
"""Select the scheduled workout and next strength template from the health log."""

from __future__ import annotations

import argparse
import csv
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

try:
    from scripts.prescription_progression import (
        ProgressionError,
        apply_progression,
        load_sets,
    )
except ModuleNotFoundError:  # direct execution: python scripts/next_workout.py
    from prescription_progression import (  # type: ignore[no-redef, import-not-found]
        ProgressionError,
        apply_progression,
        load_sets,
    )

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROGRAM = REPO_ROOT / "plans" / "current_program.json"
DEFAULT_SESSIONS = REPO_ROOT / "data" / "sessions.csv"
DEFAULT_SETS = REPO_ROOT / "data" / "sets.csv"
STRENGTH_TYPES = ("upper_body", "lower_body")


class ProgramError(ValueError):
    """Raised when program or session inputs are invalid."""


@dataclass(frozen=True)
class Session:
    session_date: date
    workout_type: str
    status: str
    session_id: str | None = None


def _required(mapping: dict[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ProgramError(f"{context}: missing {key}")
    return mapping[key]


def _iso_date(value: Any, context: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ProgramError(f"{context}: invalid date") from exc


def _validate_slot(slot: Any, templates: dict[str, Any], context: str) -> None:
    if not isinstance(slot, dict):
        raise ProgramError(f"{context}: slot must be an object")
    kind = _required(slot, "kind", context)
    _required(slot, "label", context)
    if kind != "strength":
        template_name = _required(slot, "template", context)
        if template_name not in templates:
            raise ProgramError(f"{context}: unknown template {template_name}")


def load_program(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            program = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ProgramError(f"{path}: invalid JSON") from exc

    if not isinstance(program, dict):
        raise ProgramError(f"{path}: program must be an object")
    for key in (
        "schema_version",
        "program_id",
        "title",
        "canonical_source",
        "start_date",
        "end_date",
        "default_first_strength",
        "strength_rotation",
        "schedule",
        "templates",
    ):
        _required(program, key, str(path))

    if program["schema_version"] != 2:
        raise ProgramError(f"{path}: unsupported schema_version")

    start = _iso_date(program["start_date"], f"{path}: start_date")
    end = _iso_date(program["end_date"], f"{path}: end_date")
    if end < start:
        raise ProgramError(f"{path}: end_date precedes start_date")

    rotation = program["strength_rotation"]
    if rotation != list(STRENGTH_TYPES) and rotation != list(reversed(STRENGTH_TYPES)):
        raise ProgramError(
            f"{path}: strength_rotation must contain upper_body and lower_body once"
        )
    if program["default_first_strength"] not in rotation:
        raise ProgramError(f"{path}: invalid default_first_strength")

    templates = program["templates"]
    required_templates = (
        *STRENGTH_TYPES,
        "cardio_treadmill",
        "shuffle_main",
        "cardio_easy_sunday",
        "lead_in_recovery",
        "recovery",
    )
    for template_name in required_templates:
        _required(templates, template_name, f"{path}: templates")
    for template_name, template in templates.items():
        if not isinstance(template, dict):
            raise ProgramError(f"{path}: template {template_name} must be an object")
        for key in ("label", "target_duration", "warmup", "exercises"):
            _required(template, key, f"{path}: template {template_name}")
        if not template["exercises"]:
            raise ProgramError(f"{path}: template {template_name} has no exercises")
        for index, exercise in enumerate(template["exercises"], start=1):
            context = f"{path}: template {template_name} exercise {index}"
            if not isinstance(exercise, dict):
                raise ProgramError(f"{context} must be an object")
            if "available_from" in exercise:
                _iso_date(exercise["available_from"], f"{context} available_from")
            if "first_session_of_week_only" in exercise and not isinstance(
                exercise["first_session_of_week_only"], bool
            ):
                raise ProgramError(
                    f"{context} first_session_of_week_only must be a boolean"
                )

    schedule = program["schedule"]
    if not isinstance(schedule, dict) or set(schedule) != {
        str(day) for day in range(7)
    }:
        raise ProgramError(f"{path}: schedule must define weekdays 0 through 6")
    for weekday, slot in schedule.items():
        _validate_slot(slot, templates, f"{path}: schedule {weekday}")

    overrides = program.get("date_overrides", {})
    if not isinstance(overrides, dict):
        raise ProgramError(f"{path}: date_overrides must be an object")
    for override_date, slot in overrides.items():
        _iso_date(override_date, f"{path}: date_overrides {override_date}")
        _validate_slot(slot, templates, f"{path}: date_overrides {override_date}")

    lead_in = _required(program, "lead_in", str(path))
    lead_start = _iso_date(
        _required(lead_in, "start_date", f"{path}: lead_in"),
        f"{path}: lead_in start_date",
    )
    lead_end = _iso_date(
        _required(lead_in, "end_date", f"{path}: lead_in"),
        f"{path}: lead_in end_date",
    )
    if lead_end < lead_start or lead_end >= start:
        raise ProgramError(f"{path}: lead_in must end before program start")
    lead_template = _required(lead_in, "template", f"{path}: lead_in")
    if lead_template not in templates:
        raise ProgramError(f"{path}: lead_in has unknown template {lead_template}")

    monitoring = _required(program, "health_monitoring", str(path))
    blood_pressure = _required(
        monitoring, "blood_pressure", f"{path}: health_monitoring"
    )
    _required(blood_pressure, "protocol", f"{path}: blood_pressure")
    for phase_name in ("baseline", "maintenance"):
        phase = _required(blood_pressure, phase_name, f"{path}: blood_pressure")
        phase_start = _iso_date(
            _required(phase, "start_date", f"{path}: {phase_name}"),
            f"{path}: {phase_name} start_date",
        )
        phase_end = _iso_date(
            _required(phase, "end_date", f"{path}: {phase_name}"),
            f"{path}: {phase_name} end_date",
        )
        if phase_end < phase_start:
            raise ProgramError(f"{path}: {phase_name} end precedes start")
        sessions = _required(phase, "sessions", f"{path}: {phase_name}")
        if not isinstance(sessions, list) or not sessions:
            raise ProgramError(f"{path}: {phase_name} sessions must be non-empty")
        for index, session in enumerate(sessions, start=1):
            context = f"{path}: {phase_name} session {index}"
            if not isinstance(session, dict):
                raise ProgramError(f"{context}: session must be an object")
            for key in ("time", "label", "work"):
                _required(session, key, context)
    return program


def load_sessions(path: Path) -> list[Session]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"date", "workout_type", "status"}
        if not required.issubset(reader.fieldnames or []):
            raise ProgramError(f"{path}: missing required session columns")
        sessions: list[Session] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                session_date = date.fromisoformat(row["date"])
            except (TypeError, ValueError) as exc:
                raise ProgramError(f"{path}:{line_number}: invalid date") from exc
            sessions.append(
                Session(
                    session_date=session_date,
                    workout_type=row["workout_type"].strip(),
                    status=row["status"].strip(),
                    session_id=(row.get("session_id") or "").strip() or None,
                )
            )
    return sessions


def next_strength_type(
    sessions: list[Session], program: dict[str, Any], target_date: date
) -> str:
    completed = [
        session
        for session in sessions
        if session.status == "complete"
        and session.workout_type in program["strength_rotation"]
        and session.session_date <= target_date
    ]
    if not completed:
        return str(program["default_first_strength"])
    latest = max(completed, key=lambda session: session.session_date)
    rotation: list[str] = program["strength_rotation"]
    return rotation[(rotation.index(latest.workout_type) + 1) % len(rotation)]


def _slot_for_date(program: dict[str, Any], target_date: date) -> dict[str, Any]:
    override = program.get("date_overrides", {}).get(target_date.isoformat())
    if override is not None:
        return cast(dict[str, Any], override)
    return cast(dict[str, Any], program["schedule"][str(target_date.weekday())])


def health_tasks_for_date(
    program: dict[str, Any], target_date: date
) -> list[dict[str, Any]]:
    blood_pressure = program["health_monitoring"]["blood_pressure"]
    tasks: list[dict[str, Any]] = []
    for phase_name in ("baseline", "maintenance"):
        phase = blood_pressure[phase_name]
        phase_start = date.fromisoformat(phase["start_date"])
        phase_end = date.fromisoformat(phase["end_date"])
        if not phase_start <= target_date <= phase_end:
            continue
        weekday = phase.get("weekday")
        if weekday is not None and target_date.weekday() != weekday:
            continue
        for session in phase["sessions"]:
            tasks.append(
                {
                    "category": "blood_pressure",
                    "phase": phase_name,
                    "time": session["time"],
                    "label": session["label"],
                    "work": session["work"],
                    "protocol": blood_pressure["protocol"],
                    "safety": blood_pressure["safety"],
                }
            )
    return tasks


def gym_timing_for_date(
    program: dict[str, Any], target_date: date, template_name: str
) -> dict[str, str] | None:
    gym_access = program.get("gym_access", {})
    weekday_name = target_date.strftime("%A").lower()
    starts = (
        gym_access.get("weekday_latest_useful_starts", {})
        .get(weekday_name, {})
        .get(template_name)
    )
    if starts is None:
        starts = gym_access.get("latest_useful_starts", {}).get(template_name)
    if starts is None:
        return None
    return {
        "hours": gym_access["hours"][weekday_name],
        "latest_full_start": starts["full"],
        "latest_minimum_start": starts["minimum"],
        "rule": gym_access["rule"],
    }


def _next_strength_date(program: dict[str, Any], target_date: date) -> date | None:
    end = date.fromisoformat(program["end_date"])
    candidate = target_date
    while candidate <= end:
        slot = _slot_for_date(program, candidate)
        if slot["kind"] == "strength":
            return candidate
        candidate += timedelta(days=1)
    return None


def build_recommendation(
    program: dict[str, Any],
    sessions: list[Session],
    target_date: date,
    set_history: list[Any] | None = None,
) -> dict[str, Any]:
    start = date.fromisoformat(program["start_date"])
    end = date.fromisoformat(program["end_date"])
    next_strength = next_strength_type(sessions, program, target_date)
    health_tasks = health_tasks_for_date(program, target_date)

    if target_date < start:
        lead_in = program["lead_in"]
        lead_start = date.fromisoformat(lead_in["start_date"])
        lead_end = date.fromisoformat(lead_in["end_date"])
        if lead_start <= target_date <= lead_end:
            return {
                "date": target_date.isoformat(),
                "program_id": program["program_id"],
                "program_title": program["title"],
                "status": "lead_in",
                "message": lead_in["message"],
                "next_strength_type": next_strength,
                "next_strength_date": start.isoformat(),
                "template_name": lead_in["template"],
                "template": program["templates"][lead_in["template"]],
                "progression_result": {},
                "health_tasks": health_tasks,
                "gym_timing": None,
            }
        return {
            "date": target_date.isoformat(),
            "program_id": program["program_id"],
            "program_title": program["title"],
            "status": "not_started",
            "message": f"Program starts {start.isoformat()}.",
            "next_strength_type": next_strength,
            "next_strength_date": start.isoformat(),
            "template": program["templates"]["recovery"],
            "progression_result": {},
            "health_tasks": health_tasks,
            "gym_timing": None,
        }
    if target_date > end:
        return {
            "date": target_date.isoformat(),
            "program_id": program["program_id"],
            "program_title": program["title"],
            "status": "review_due",
            "message": f"Program ended {end.isoformat()}; complete the block review.",
            "next_strength_type": next_strength,
            "next_strength_date": None,
            "template": None,
            "progression_result": {},
            "health_tasks": health_tasks,
            "gym_timing": None,
        }

    completed_today = [
        session
        for session in sessions
        if session.status == "complete" and session.session_date == target_date
    ]
    if completed_today:
        completed_types = ", ".join(
            sorted({session.workout_type for session in completed_today})
        )
        next_date = _next_strength_date(program, target_date + timedelta(days=1))
        return {
            "date": target_date.isoformat(),
            "program_id": program["program_id"],
            "program_title": program["title"],
            "status": "completed_today",
            "message": f"Completed today: {completed_types}.",
            "next_strength_type": next_strength,
            "next_strength_date": next_date.isoformat() if next_date else None,
            "template": program["templates"]["recovery"],
            "progression_result": {},
            "health_tasks": health_tasks,
            "gym_timing": None,
        }

    slot = _slot_for_date(program, target_date)
    if slot["kind"] == "strength":
        template_name = next_strength
    else:
        template_name = slot["template"]
    next_date = (
        target_date
        if slot["kind"] == "strength"
        else _next_strength_date(program, target_date + timedelta(days=1))
    )
    template = deepcopy(program["templates"][template_name])
    week_start = target_date - timedelta(days=target_date.weekday())
    completed_same_template_this_week = any(
        session.status == "complete"
        and session.workout_type == template_name
        and week_start <= session.session_date < target_date
        for session in sessions
    )
    template["exercises"] = [
        exercise
        for exercise in template["exercises"]
        if (
            "available_from" not in exercise
            or target_date >= date.fromisoformat(exercise["available_from"])
        )
        and not (
            exercise.get("first_session_of_week_only")
            and completed_same_template_this_week
        )
    ]
    progression_result: dict[str, Any] = {}
    if slot["kind"] == "strength" and set_history is not None:
        template, progression_result = apply_progression(
            template,
            sessions,
            set_history,
            target_date,
            program.get("progression_policy"),
        )
    return {
        "date": target_date.isoformat(),
        "program_id": program["program_id"],
        "program_title": program["title"],
        "status": "scheduled",
        "scheduled_kind": slot["kind"],
        "scheduled_label": slot["label"],
        "template_name": template_name,
        "next_strength_type": next_strength,
        "next_strength_date": next_date.isoformat() if next_date else None,
        "template": template,
        "progression_result": progression_result,
        "health_tasks": health_tasks,
        "gym_timing": gym_timing_for_date(program, target_date, template_name),
    }


def build_preview(
    program: dict[str, Any],
    sessions: list[Session],
    start: date,
    days: int,
    set_history: list[Any] | None = None,
) -> dict[str, Any]:
    """Preview rotation with explicitly hypothetical attendance, never new sets."""
    if not 1 <= days <= 14:
        raise ProgramError("preview days must be between 1 and 14")
    end = start + timedelta(days=days - 1)
    if start < date.fromisoformat(program["start_date"]) or end > date.fromisoformat(
        program["end_date"]
    ):
        raise ProgramError("preview must stay within the active program dates")
    if any(
        session.session_date >= start
        and session.workout_type in STRENGTH_TYPES
        and session.status in ("complete", "partial")
        for session in sessions
    ):
        raise ProgramError("preview must start after recorded strength sessions")
    projected_sessions = list(sessions)
    assumptions: list[dict[str, str]] = []
    results = []
    for offset in range(days):
        target = start + timedelta(days=offset)
        recommendation = build_recommendation(
            program, projected_sessions, target, set_history
        )
        results.append(
            {
                "assumed_completed_sessions": list(assumptions),
                "recommendation": recommendation,
            }
        )
        if recommendation.get("scheduled_kind") == "strength":
            workout_type = recommendation["template_name"]
            assumptions.append({"date": target.isoformat(), "type": workout_type})
            projected_sessions.append(
                Session(target, workout_type, "complete", f"preview-{target}")
            )
    return {
        "mode": "conditional_preview",
        "assumption": (
            "Assumes each preceding strength slot is completed for rotation only. "
            "No attendance or sets are saved; loads use recorded evidence only. "
            "Recompute after each actual session or change in availability."
        ),
        "days": results,
    }


def render_text(recommendation: dict[str, Any]) -> str:
    lines = [
        f"{recommendation['date']} — {recommendation['program_title']}",
        recommendation.get("message", ""),
    ]
    lines = [line for line in lines if line]
    template = recommendation.get("template")
    if template is not None:
        lines.extend(
            [
                f"Workout: {template['label']}",
                f"Target duration: {template['target_duration']}",
            ]
        )
        timing = recommendation.get("gym_timing")
        if timing:
            lines.append(
                "Timing: "
                f"{timing['hours']}; start the full session by "
                f"{timing['latest_full_start']} or the minimum version by "
                f"{timing['latest_minimum_start']}."
            )
            lines.append(f"- {timing['rule']}")
        if template["warmup"]:
            lines.append("Warm-up:")
            lines.extend(f"- {item}" for item in template["warmup"])
        lines.append("Work:")
        for index, exercise in enumerate(template["exercises"], start=1):
            lines.append(
                f"{index}. {exercise['exercise']} — {exercise['load']}; "
                f"{exercise['work']}; target {exercise['rir']}"
            )
            lines.append(f"   Next target: {exercise['next_target']}")
            progression = exercise.get("progression_result")
            if progression:
                lines.append(f"   Evidence: {progression['message']}")
        if template.get("cooldown"):
            lines.append("Cooldown:")
            lines.extend(f"- {item}" for item in template["cooldown"])
        if template.get("minimum_version"):
            lines.append(f"Minimum version: {template['minimum_version']}")

    health_tasks = recommendation.get("health_tasks", [])
    if health_tasks:
        lines.append("Health tasks:")
        for task in health_tasks:
            lines.append(f"- {task['label']} ({task['time']}): {task['work']}")
        lines.append(f"  Protocol: {health_tasks[0]['protocol']}")
        lines.append(f"  Safety: {health_tasks[0]['safety']}")
    next_date = recommendation.get("next_strength_date")
    if recommendation.get("scheduled_kind") != "strength" and next_date:
        strength_label = recommendation["next_strength_type"].replace("_", " ")
        lines.append(f"Next strength: {strength_label} on {next_date}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print the current structured workout from logged history."
    )
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--program", type=Path, default=DEFAULT_PROGRAM)
    parser.add_argument("--sessions-file", type=Path, default=DEFAULT_SESSIONS)
    parser.add_argument(
        "--sets-file",
        type=Path,
        default=None,
        help=(
            "set history CSV (defaults to data/sets.csv with the default sessions file)"
        ),
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--preview-days",
        type=int,
        help="read-only conditional preview for 1-14 days, assuming attendance",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        program = load_program(args.program)
        sessions = load_sessions(args.sessions_file)
        if args.sets_file is not None:
            set_history = load_sets(args.sets_file)
        elif args.sessions_file.resolve() == DEFAULT_SESSIONS.resolve():
            set_history = load_sets(DEFAULT_SETS)
        else:
            set_history = []
        if args.preview_days is not None:
            recommendation = build_preview(
                program, sessions, args.date, args.preview_days, set_history
            )
        else:
            recommendation = build_recommendation(
                program, sessions, args.date, set_history
            )
    except (OSError, ProgramError, ProgressionError) as exc:
        raise SystemExit(f"error: {exc}") from exc
    if args.format == "json":
        print(json.dumps(recommendation, indent=2, sort_keys=True))
    elif args.preview_days is not None:
        print(recommendation["assumption"])
        for item in recommendation["days"]:
            print()
            print(render_text(item["recommendation"]))
    else:
        print(render_text(recommendation))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
