"""Strict v1 camelCase plan intent; retain the existing schema-v2 store format."""

from __future__ import annotations

from typing import Any, cast

from . import legacy
from .domain import invalid, object_value, text
from .loggers import camel
from .service_api import JSON

TOP = set(
    "schema_version program_id title canonical_source start_date end_date default_first_strength strength_rotation schedule templates lead_in health_monitoring gym_access date_overrides progression_policy".split()
)
TEMPLATE = set(
    "label target_duration warmup cooldown exercises minimum_version minimum_session_guidance notes".split()
)
EXERCISE = set(
    "exercise load work rir next_target available_from first_session_of_week_only progression notes".split()
)
PROGRESSION = set(
    "exercise_id equipment_id load_basis baseline_load sets rep_min rep_max baseline_reps next_load fallback_load backoff_sets backoff_rep_min backoff_rep_max".split()
)
POLICY = set(
    "qualifying_rir small_increment_max_fraction qualifying_exposures_for_small_increment".split()
)
SLOT = {"kind", "label", "template"}
LEAD = {"start_date", "end_date", "template", "message"}
PHASE = {"start_date", "end_date", "weekday", "sessions"}
GYM = {"hours", "rule", "latest_useful_starts", "weekday_latest_useful_starts"}
NODE_FIELDS = {
    "top": TOP,
    "template": TEMPLATE,
    "exercise": EXERCISE,
    "progression": PROGRESSION,
    "policy": POLICY,
    "slot": SLOT,
    "lead": LEAD,
    "phase": PHASE,
    "gym": GYM,
    "monitoring": {"blood_pressure"},
    "bp": {"protocol", "safety", "baseline", "maintenance"},
    "session": {"time", "label", "work"},
    "starts": {"full", "minimum"},
}
CHILDREN = {
    ("top", "templates"): "map:template",
    ("template", "exercises"): "list:exercise",
    ("exercise", "progression"): "progression",
    ("top", "progression_policy"): "policy",
    ("top", "schedule"): "map:slot",
    ("top", "date_overrides"): "map:slot",
    ("top", "lead_in"): "lead",
    ("top", "health_monitoring"): "monitoring",
    ("monitoring", "blood_pressure"): "bp",
    ("bp", "baseline"): "phase",
    ("bp", "maintenance"): "phase",
    ("phase", "sessions"): "list:session",
    ("top", "gym_access"): "gym",
    ("gym", "latest_useful_starts"): "map:starts",
    ("gym", "weekday_latest_useful_starts"): "map:map:starts",
}


def _convert(value: JSON, *, wire: bool, node: str = "top") -> JSON:
    if node.startswith("list:"):
        if not isinstance(value, list):
            raise invalid()
        return [_convert(item, wire=wire, node=node[5:]) for item in value]
    if not isinstance(value, dict):
        raise invalid()
    if node.startswith("map:"):
        # Dynamic template IDs, dates and weekdays are values in the schema;
        # preserve them verbatim even if they equal a documented field name.
        return {
            key: _convert(item, wire=wire, node=node[4:]) for key, item in value.items()
        }
    mapping = {word if wire else camel(word): word for word in NODE_FIELDS[node]}
    result: dict[str, JSON] = {}
    for key, item in value.items():
        if key not in mapping:
            raise invalid()
        field = mapping[key]
        target = camel(field) if wire else field
        child = CHILDREN.get((node, field))
        result[target] = _convert(item, wire=wire, node=child) if child else item
    return result


def to_wire(program: JSON) -> JSON:
    return _convert(program, wire=True)


def _allowed(value: JSON, keys: set[str]) -> dict[str, JSON]:
    return object_value(value, set(), keys)


def validate_plan(value: JSON) -> dict[str, Any]:
    # Canonical clients use camelCase, including nested field names. Dynamic
    # template IDs/weekday/date-map keys stay stable and are not renamed.
    if not isinstance(value, dict) or "schemaVersion" not in value:
        raise invalid()
    program = _allowed(_convert(value, wire=False), TOP)
    text(program.get("program_id"), limit=128)
    text(program.get("title"), limit=200)
    text(program.get("canonical_source"), limit=500)
    templates = program.get("templates")
    if not isinstance(templates, dict) or not 1 <= len(templates) <= 40:
        raise invalid()
    progression = legacy.module("prescription_progression")
    policy = _allowed(program.get("progression_policy", {}), POLICY)
    for template_name, value in templates.items():
        text(template_name, limit=80)
        template = _allowed(value, TEMPLATE)
        for key in ("label", "target_duration"):
            text(template.get(key), limit=500)
        for key in ("minimum_version", "minimum_session_guidance", "notes"):
            if key in template:
                text(template[key], limit=2000, empty=True)
        for key in ("warmup", "cooldown"):
            items = template.get(key, [])
            if not isinstance(items, list) or len(items) > 40:
                raise invalid()
            for item in items:
                text(item, limit=2000)
        exercises = template.get("exercises")
        if not isinstance(exercises, list) or not 1 <= len(exercises) <= 80:
            raise invalid()
        for raw in exercises:
            exercise = _allowed(raw, EXERCISE)
            text(exercise.get("exercise"), limit=200)
            for key in ("load", "work", "rir", "next_target", "notes"):
                if key in exercise:
                    text(exercise[key], limit=2000, empty=True)
            if "progression" in exercise:
                config = _allowed(exercise["progression"], PROGRESSION)
                for key in (
                    "sets",
                    "rep_min",
                    "rep_max",
                    "backoff_sets",
                    "backoff_rep_min",
                    "backoff_rep_max",
                ):
                    if key in config and (
                        type(config[key]) is not int
                        or not 1 <= cast(int, config[key]) <= 100
                    ):
                        raise invalid()
                progression._config(exercise, policy)
    for key in ("schedule", "date_overrides"):
        slots = program.get(key, {})
        if not isinstance(slots, dict) or len(slots) > 100:
            raise invalid()
        for slot in slots.values():
            entry = _allowed(slot, SLOT)
            for item in entry.values():
                text(item, limit=2000)
    lead = _allowed(program.get("lead_in"), LEAD)
    for item in lead.values():
        text(item, limit=2000)
    monitoring = object_value(program.get("health_monitoring"), {"blood_pressure"})
    bp = object_value(
        monitoring["blood_pressure"], {"protocol", "safety", "baseline", "maintenance"}
    )
    for key in ("protocol", "safety"):
        text(bp[key], limit=2000)
    for key in ("baseline", "maintenance"):
        phase = _allowed(bp[key], PHASE)
        if "weekday" in phase and (
            type(phase["weekday"]) is not int or not 0 <= phase["weekday"] <= 6
        ):
            raise invalid()
        sessions = phase.get("sessions")
        if not isinstance(sessions, list) or not 1 <= len(sessions) <= 10:
            raise invalid()
        for session in sessions:
            entry = object_value(session, {"time", "label", "work"})
            for item in entry.values():
                text(item, limit=2000)
    if "gym_access" in program:
        gym = _allowed(program["gym_access"], GYM)
        if "hours" in gym:
            hours = object_value(
                gym["hours"],
                set(),
                set("monday tuesday wednesday thursday friday saturday sunday".split()),
            )
            for hours_text in hours.values():
                text(hours_text, limit=1000)
        if "rule" in gym:
            text(gym["rule"], limit=2000)
        mappings = [gym.get("latest_useful_starts", {})]
        weekdays = gym.get("weekday_latest_useful_starts", {})
        if not isinstance(weekdays, dict):
            raise invalid()
        mappings.extend(weekdays.values())
        for mapping in mappings:
            if not isinstance(mapping, dict):
                raise invalid()
            for starts in mapping.values():
                entry = object_value(starts, {"full", "minimum"})
                for start in entry.values():
                    text(start, limit=20)
    return cast(dict[str, Any], legacy.module("next_workout").validate_program(program))
