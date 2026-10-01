"""Finite version-1 tool schemas; no arbitrary endpoint, path or executable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from health_buddy.loggers import FIELDS, camel
from health_buddy.plans import CHILDREN, NODE_FIELDS
from health_buddy.service_api import ServiceError

Schema = dict[str, Any]


def text(maximum: int = 2000, *, minimum: int = 0) -> Schema:
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


def object_schema(
    properties: dict[str, Schema], required: tuple[str, ...] = ()
) -> Schema:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def array(items: Schema, maximum: int, *, minimum: int = 0) -> Schema:
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


ID = {**text(128, minimum=1), "pattern": "^[A-Za-z0-9_.:-]+$"}
UUID = {
    **text(36, minimum=36),
    "pattern": "^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$",
}
IDENTITY = object_schema(
    {key: UUID for key in ("installationId", "datasetId", "restoreEpoch")},
    ("installationId", "datasetId", "restoreEpoch"),
)
REVISION = {"type": "integer", "minimum": 0, "maximum": 999999999999998}
CONTEXT_SCOPE = {
    "enum": [
        "profile",
        "weight",
        "bp",
        "intake",
        "training",
        "recovery",
        "labs",
        "clinical",
        "note",
    ]
}
DATE = {**text(10, minimum=10), "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
TIMESTAMP = {
    **text(40, minimum=20),
    "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}T.*(Z|[+-][0-9]{2}:[0-9]{2})$",
}
NUMBER = {"type": "number", "minimum": 0, "maximum": 1000000000}
NUMERIC = {
    "anyOf": [NUMBER, {**text(32, minimum=1), "pattern": "^[0-9]+(\\.[0-9]+)?$"}]
}

_LOG_REQUIRED = {
    "measurement": ("measuredAtLocal", "timezone", "weightLb"),
    "intake": ("eventAtLocal", "timezone", "itemName", "status", "category", "source"),
    "blood-pressure": (
        "measuredAtLocal",
        "timezone",
        "systolic",
        "diastolic",
        "readingNumber",
        "source",
    ),
    "circumference": (
        "measuredAtLocal",
        "timezone",
        "site",
        "reading",
        "measurementSite",
    ),
    "workout-start": ("sessionId", "date", "workoutType"),
    "workout-set": (
        "sessionId",
        "date",
        "exercise",
        "equipment",
        "setNumber",
        "loadBasis",
        "reps",
    ),
    "workout-cardio": ("sessionId", "date", "activity", "equipment", "segmentNumber"),
    "workout-finish": ("sessionId",),
}
_NUMERIC_NAMES = frozenset(
    (
        "systolic diastolic pulse reading_number set_number set_count reps rir "
        "segment_number level steps_per_min floors_climbed calories "
        "avg_heart_rate_bpm max_heart_rate_bpm serving_quantity distance_value reading"
    ).split()
)


def _log_schema(kind: str) -> Schema:
    properties = {}
    for name in FIELDS[kind].split():
        value = (
            NUMERIC
            if name in _NUMERIC_NAMES
            or name.endswith(
                (
                    "_lb",
                    "_pct",
                    "_kcal",
                    "_g",
                    "_mg",
                    "_min",
                    "_seconds",
                    "_minutes",
                    "_mph",
                    "_percent",
                    "_feet",
                )
            )
            else text()
        )
        if name == "reading":
            value = array(NUMERIC, 10, minimum=1)
        elif name.endswith("_at_local"):
            value = TIMESTAMP
        elif name == "date":
            value = DATE
        properties[camel(name)] = value
    return object_schema(properties, _LOG_REQUIRED[kind])


def _plan_node(node: str) -> Schema:
    if node.startswith("list:"):
        return array(_plan_node(node[5:]), 80)
    if node.startswith("map:"):
        return {
            "type": "object",
            "propertyNames": text(80, minimum=1),
            "maxProperties": 100,
            "additionalProperties": _plan_node(node[4:]),
        }
    properties = {}
    for name in sorted(NODE_FIELDS[node]):
        child = CHILDREN.get((node, name))
        if child:
            value = _plan_node(child)
        elif name == "schema_version":
            value = {"type": "integer", "const": 2}
        elif name == "first_session_of_week_only":
            value = {"type": "boolean"}
        elif name in {"warmup", "cooldown", "strength_rotation"}:
            value = array(text(), 80)
        elif name == "baseline_reps":
            value = array({"type": "integer", "minimum": 1, "maximum": 100}, 100)
        elif name == "hours":
            value = object_schema(
                {
                    day: text(1000)
                    for day in (
                        "monday tuesday wednesday thursday friday saturday sunday"
                    ).split()
                }
            )
        elif name in {
            "sets",
            "rep_min",
            "rep_max",
            "backoff_sets",
            "backoff_rep_min",
            "backoff_rep_max",
            "weekday",
            "qualifying_exposures_for_small_increment",
        }:
            value = {"type": "integer", "minimum": 0, "maximum": 100}
        elif name in {
            "baseline_load",
            "next_load",
            "fallback_load",
            "qualifying_rir",
            "small_increment_max_fraction",
        }:
            value = NUMBER
        else:
            value = text()
        properties[camel(name)] = value
    required = (
        (
            "schemaVersion",
            "programId",
            "title",
            "canonicalSource",
            "templates",
            "leadIn",
            "healthMonitoring",
        )
        if node == "top"
        else ()
    )
    return object_schema(properties, required)


PLAN = _plan_node("top")
SET = object_schema(
    {
        "exercise": text(100, minimum=1),
        "equipment": text(100, minimum=1),
        "set_number": {"type": "integer", "minimum": 1, "maximum": 1000},
        "load_lb": {
            "anyOf": [
                {"type": "null"},
                {"type": "number", "minimum": 0, "maximum": 2000},
            ]
        },
        "load_basis": {
            "enum": [
                "per_hand",
                "total_stack",
                "machine_stack",
                "total",
                "bodyweight",
                "not_reported",
            ]
        },
        "reps": {"type": "integer", "minimum": 1, "maximum": 1000},
        "rir": {
            "anyOf": [
                {"type": "null"},
                {"type": "integer", "minimum": 0, "maximum": 10},
            ]
        },
        "form_quality": {"enum": ["not_reported", "clean", "controlled", "breakdown"]},
        "notes": text(),
    },
    ("exercise", "equipment", "set_number", "load_basis", "reps"),
)
WORKOUT = object_schema(
    {
        "schema_version": {"type": "integer", "const": 1},
        "session_id": {**text(46, minimum=46), "pattern": "^dashboard-[a-f0-9-]{36}$"},
        "date": DATE,
        "workout_type": {
            "enum": [
                "upper_body",
                "lower_body",
                "cardio",
                "racquet",
                "shuffle",
                "strength",
            ]
        },
        "status": {"enum": ["complete", "partial"]},
        "duration_min": {
            "anyOf": [
                {"type": "null"},
                {"type": "number", "minimum": 0, "maximum": 1440},
            ]
        },
        "notes": text(),
        "sets": array(SET, 80),
        "base_revision": {**text(40, minimum=40), "pattern": "^[a-f0-9]{40}$"},
    },
    ("schema_version", "session_id", "date", "workout_type", "status", "sets"),
)
WRITE = {"intentId": ID, "identity": IDENTITY, "expectedRevision": REVISION}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    operation: str
    description: str
    input_schema: Schema
    write: bool = False


SPECS = (
    ToolSpec(
        "discover_workspace",
        "workspace.discover",
        (
            "Discover admitted interfaces and logical source/docs references. "
            "Selected health content reaches your chosen AI host."
        ),
        object_schema({}),
    ),
    ToolSpec(
        "get_context",
        "context.read",
        (
            "Read only explicitly selected context scopes over a bounded window; "
            "preserves missingness and truncation."
        ),
        object_schema(
            {
                "scopes": array(CONTEXT_SCOPE, 9, minimum=1),
                "days": {"type": "integer", "minimum": 1, "maximum": 90},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            ("scopes",),
        ),
    ),
    ToolSpec(
        "list_records",
        "records.list",
        (
            "Read one bounded page. Repeat the exact returned window/filter and "
            "cursor to continue at one revision."
        ),
        object_schema(
            {
                "from": TIMESTAMP,
                "to": TIMESTAMP,
                "kinds": array(ID, 16, minimum=1),
                "sourceIds": array(ID, 32, minimum=1),
                "fields": array(ID, 20, minimum=1),
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cursor": text(4096, minimum=1),
            },
            ("from", "to", "kinds", "sourceIds"),
        ),
    ),
    ToolSpec(
        "get_plan",
        "plan.read",
        "Read the current canonical plan and revision.",
        object_schema({}),
    ),
    ToolSpec(
        "sync_status",
        "projection.status",
        (
            "Read admitted source availability/freshness; "
            "missing, disabled and fresh-empty are distinct."
        ),
        object_schema({}),
    ),
    ToolSpec(
        "record_workout",
        "workouts.write",
        (
            "Save one completed workout with an explicit receiver/CAS revision and "
            "stable action ID. Retry uncertain outcomes with the same ID."
        ),
        object_schema({**WRITE, "workout": WORKOUT}, (*WRITE, "workout")),
        True,
    ),
    ToolSpec(
        "log_health",
        "logs.write",
        (
            "Log a typed observation under an explicit source and original CAS. "
            "Timestamps/dates are explicit; repeating the same ID preserves "
            "the original request."
        ),
        {
            "type": "object",
            "oneOf": [
                object_schema(
                    {
                        **WRITE,
                        "kind": {"const": kind},
                        "sourceId": ID,
                        "fields": _log_schema(kind),
                        "replaceExisting": {"type": "boolean"},
                    },
                    (*WRITE, "kind", "sourceId", "fields"),
                )
                for kind in FIELDS
            ],
        },
        True,
    ),
    ToolSpec(
        "propose_plan",
        "plan.write",
        (
            "Persist a reviewable plan proposal and its original CAS without "
            "applying it. Server semantic validation remains pending until apply."
        ),
        object_schema({**WRITE, "plan": PLAN}, (*WRITE, "plan")),
    ),
    ToolSpec(
        "apply_plan",
        "plan.write",
        (
            "Apply only the retained proposal body/CAS after reviewing its returned "
            "digest. Never refresh a stale proposal automatically."
        ),
        object_schema(
            {
                "proposalId": ID,
                "reviewDigest": {**text(64, minimum=64), "pattern": "^[a-f0-9]{64}$"},
                "confirm": {"const": True},
            },
            ("proposalId", "reviewDigest", "confirm"),
        ),
        True,
    ),
    ToolSpec(
        "write_status",
        "capabilities",
        (
            "Inspect safe status for an existing action ID; "
            "no private payload or raw errors."
        ),
        object_schema({"intentId": ID}, ("intentId",)),
    ),
    ToolSpec(
        "retry_write",
        "capabilities",
        (
            "Replay the exact retained request after an uncertain outcome. "
            "No new key or revision is generated."
        ),
        object_schema({"intentId": ID}, ("intentId",)),
        True,
    ),
)
CATALOG = {spec.name: spec for spec in SPECS}


def validate(name: str, value: object) -> dict[str, Any]:
    spec = CATALOG.get(name)
    if (
        spec is None
        or not isinstance(value, dict)
        or not Draft202012Validator(spec.input_schema).is_valid(value)
    ):
        raise ServiceError(422, "invalid_request")
    return value
