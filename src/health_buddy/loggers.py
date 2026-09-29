"""Pure transitions preserving the shipped manual loggers' natural keys.

The narrow dynamic seam loads retained parsers/validators from the release
bundle. No helper here opens a user-selected path or writes an authoritative
file. Typed JSON fields are translated to the same known argument validators.
"""

from __future__ import annotations

import argparse
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Never, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import legacy
from .config import Config
from .domain import identifier, invalid, object_value
from .legacy_store import csv_text, parse_csv
from .service_api import JSON, ServiceError

FIELDS = {
    "measurement": "measured_at_local timezone weight_lb body_fat_pct muscle_mass_pct water_pct bmi bone_mass_pct source notes",
    "intake": "event_at_local timezone status category item_name brand serving_quantity serving_unit calories_kcal protein_g carbohydrate_g fat_g sodium_mg caffeine_mg source notes",
    "blood-pressure": "measured_at_local timezone systolic diastolic pulse arm reading_number measurement_session device source notes protocol_status protocol_notes",
    "circumference": "measured_at_local timezone site side reading measurement_site source notes",
    "workout-start": "session_id date workout_type status duration_min bodyweight_lb notes",
    "workout-set": "session_id date exercise equipment set_number set_count load_lb load_basis reps rir form_quality status notes",
    "workout-cardio": "session_id date activity equipment segment_number duration_seconds duration_minutes level steps_per_min speed_mph incline_percent distance_value distance_unit vertical_feet floors_climbed calories avg_heart_rate_bpm max_heart_rate_bpm source notes",
    "workout-finish": "session_id duration_min notes",
}


def camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.title() for part in rest)


def conflict() -> ServiceError:
    return ServiceError(409, "record_conflict")


def _arguments(kind: str, fields: dict[str, JSON], config: Config) -> list[str]:
    names = {camel(name): name for name in FIELDS[kind].split()}
    if fields.keys() - names.keys():
        raise invalid()
    argv = []
    for key, value in fields.items():
        if isinstance(value, bool) or value is None or isinstance(value, dict):
            raise invalid()
        values = value if isinstance(value, list) and names[key] == "reading" else [value]
        if isinstance(value, list) and names[key] != "reading":
            raise invalid()
        for item in values:
            if isinstance(item, (dict, list, bool)) or item is None:
                raise invalid()
            if isinstance(item, str) and len(item) > 2000:
                raise invalid()
            argv.append("--" + names[key].replace("_", "-") + "=" + str(item))
    if "timezone" in names and "timezone" not in fields:
        argv.append("--timezone=" + config.zone.key)
    return argv


def _circumference_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--measured-at-local", required=True)
    parser.add_argument("--timezone", required=True)
    parser.add_argument("--site", required=True, choices=("waist", "chest", "upper_arm", "thigh"))
    parser.add_argument("--side", choices=("left", "right"))
    parser.add_argument("--reading", required=True, action="append")
    parser.add_argument("--measurement-site", required=True)
    parser.add_argument("--source", default="manual_entry")
    parser.add_argument("--notes", default="")
    parser.set_defaults(data_dir=Path("data"))
    return parser


def namespace(kind: str, fields: dict[str, JSON], config: Config) -> argparse.Namespace:
    if kind not in FIELDS:
        raise invalid()
    argv = _arguments(kind, fields, config)
    if kind == "circumference":
        parser = _circumference_parser()
    else:
        module = "log_workout" if kind.startswith("workout-") else "log_" + kind.replace("-", "_")
        parser = cast(argparse.ArgumentParser, legacy.module(module)._parser())
        if kind.startswith("workout-"):
            argv.insert(0, kind.split("-", 1)[1])

    def reject(_message: str) -> Never:
        raise invalid()

    # Parser diagnostics can contain supplied values. Convert to a safe typed
    # error before argparse prints anything, including in subcommand parsers.
    parser.error = reject  # type: ignore[method-assign]
    parser.allow_abbrev = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                child.error = reject
                child.allow_abbrev = False
    try:
        result = parser.parse_args(argv)
        for value in vars(result).values():
            if isinstance(value, Decimal) and (not value.is_finite() or abs(value) > 1_000_000):
                raise invalid()
            if isinstance(value, int) and abs(value) > 1_000_000:
                raise invalid()
        if hasattr(result, "timezone"):
            ZoneInfo(result.timezone)
        return result
    except (ValueError, OverflowError, ZoneInfoNotFoundError, argparse.ArgumentTypeError) as exc:
        raise invalid() from exc


def equipment(config: Config, rows: list[dict[str, str]]) -> None:
    aliases = {(item["exercise"], alias): item for item in config.values["equipment"] for alias in item["aliases"] + [item["id"]]}
    identifiers = {item["id"]: item for item in config.values["equipment"]}
    for row in rows:
        known = identifiers.get(row["equipment"]) or aliases.get((row["exercise"], row["equipment"]))
        if known and (row["exercise"] != known["exercise"] or row["load_basis"] != known["loadBasis"]):
            raise invalid()


def validate_input(kind: str, fields: dict[str, JSON], config: Config) -> None:
    """Validate typed row values before retry lookup; no store reads here."""
    args = namespace(kind, fields, config)
    try:
        if kind == "circumference":
            legacy.module("log_circumference").build_row(args)
        elif not kind.startswith("workout-"):
            legacy.module("log_" + kind.replace("-", "_"))._row(args)
        else:
            writer = legacy.module("log_workout")
            writer._nonempty("session_id", args.session_id)
            if hasattr(args, "date"):
                writer._date(args.date)
            for field in ("duration_min", "load_lb", "duration_minutes", "duration_seconds", "level", "steps_per_min", "speed_mph", "incline_percent", "distance_value", "vertical_feet", "floors_climbed", "calories", "avg_heart_rate_bpm", "max_heart_rate_bpm"):
                value = getattr(args, field, None)
                if value is not None and value < 0:
                    raise invalid()
            for field in ("set_number", "set_count", "reps", "segment_number", "bodyweight_lb"):
                value = getattr(args, field, None)
                if value is not None and value <= 0:
                    raise invalid()
            if getattr(args, "rir", None) is not None and not 0 <= args.rir <= 10:
                raise invalid()
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise invalid() from exc


def _rows(files: dict[str, str], path: str, fields: list[str]) -> list[dict[str, str]]:
    return parse_csv(files[path], fields) if path in files else []


def _append(rows: list[dict[str, str]], row: dict[str, str], keys: tuple[str, ...]) -> tuple[list[dict[str, str]], bool]:
    matches = [item for item in rows if all(item[key] == row[key] for key in keys)]
    if len(matches) > 1:
        raise ServiceError(409, "reconciliation_required")
    if matches:
        if matches[0] != row:
            raise conflict()
        return rows, True
    return [*rows, row], False


def completed(files: dict[str, str], payload: dict[str, Any], config: Config, as_of: date) -> tuple[dict[str, str], dict[str, JSON]]:
    validator = legacy.module("workout_store")
    session, sets = validator.normalize(payload, as_of=as_of)
    equipment(config, sets)
    sessions = _rows(files, "data/sessions.csv", validator.SESSION_FIELDS)
    old_sets = _rows(files, "data/sets.csv", validator.SET_FIELDS)
    duplicate = validator._matches(session, sets, sessions, old_sets)
    changes = {} if duplicate else {
        "data/sessions.csv": csv_text(validator.SESSION_FIELDS, [*sessions, session]),
        "data/sets.csv": csv_text(validator.SET_FIELDS, old_sets + sets),
    }
    return changes, {"saved": True, "sessionId": session["session_id"], "duplicate": bool(duplicate)}


def transition(kind: str, intent: JSON, files: dict[str, str], config: Config) -> tuple[dict[str, str], dict[str, JSON]]:
    body = object_value(intent, {"sourceId", "fields"}, {"replaceExisting"})
    identifier(body["sourceId"])
    fields = body["fields"]
    if not isinstance(fields, dict) or type(body.get("replaceExisting", False)) is not bool:
        raise invalid()
    replace = bool(body.get("replaceExisting", False))
    if replace and kind not in {"intake", "blood-pressure"}:
        raise invalid()
    args = namespace(kind, fields, config)
    try:
        if kind.startswith("workout-"):
            return _workout(kind, args, files, config)
        if kind == "circumference":
            writer = legacy.module("log_circumference")
            destination, headers, row = writer.build_row(args)
            path = "data/" + destination.name
            rows = _rows(files, path, headers)
            key = (row["measured_at_local"][:10], row.get("body_site"), row.get("side"))
            matches = [old for old in rows if (old["measured_at_local"][:10], old.get("body_site"), old.get("side")) == key]
            if len(matches) > 1 or (matches and matches[0] != row):
                raise conflict()
            duplicate = bool(matches)
            if not duplicate:
                rows.append(row)
        else:
            writer = legacy.module("log_" + kind.replace("-", "_"))
            headers, row = writer.FIELDNAMES, writer._row(args)
            path = {"measurement": "data/measurements.csv", "intake": "data/intake.csv", "blood-pressure": "data/blood_pressure.csv"}[kind]
            if kind == "intake" and path in files:
                rows = parse_csv(files[path])
                if files[path].splitlines()[0].split(",") not in (headers, writer.LEGACY_FIELDNAMES):
                    raise invalid()
                rows = [{name: old.get(name, "") for name in headers} for old in rows]
            else:
                rows = _rows(files, path, headers)
            timestamp = "event_at_local" if kind == "intake" else "measured_at_local"
            matched_indices = [index for index, old in enumerate(rows) if old[timestamp] == row[timestamp]]
            duplicate = row in rows
            if replace:
                if len(matched_indices) != 1:
                    raise conflict()
                rows[matched_indices[0]] = row
            elif kind == "intake":
                if not duplicate:
                    rows.append(row)
            elif matched_indices:
                if len(matched_indices) != 1 or not duplicate:
                    raise conflict()
            else:
                rows.append(row)
            if kind == "intake":
                rows.sort(key=lambda old: old[timestamp])
        return {path: csv_text(headers, rows)}, {"saved": True, "duplicate": duplicate}
    except (ValueError, TypeError, KeyError, OverflowError, argparse.ArgumentTypeError) as exc:
        raise invalid() from exc


def _workout(kind: str, args: argparse.Namespace, files: dict[str, str], config: Config) -> tuple[dict[str, str], dict[str, JSON]]:
    writer = legacy.module("log_workout")
    session_id = writer._nonempty("session_id", args.session_id)
    sessions = _rows(files, "data/sessions.csv", writer.SESSION_FIELDS)
    parents = [row for row in sessions if row["session_id"] == session_id]
    if len(parents) > 1:
        raise ServiceError(409, "reconciliation_required")
    row: dict[str, str]
    if kind == "workout-start":
        if args.duration_min is not None and args.duration_min < 0:
            raise invalid()
        if args.bodyweight_lb is not None and args.bodyweight_lb <= 0:
            raise invalid()
        row = {"session_id": session_id, "date": writer._date(args.date), "workout_type": writer._nonempty("workout_type", args.workout_type), "status": args.status, "duration_min": "" if args.duration_min is None else str(args.duration_min), "bodyweight_lb": writer._number(args.bodyweight_lb), "notes": args.notes}
        rows, duplicate = _append(sessions, row, ("session_id",))
        path, headers = "data/sessions.csv", writer.SESSION_FIELDS
    elif kind == "workout-finish":
        if not parents or (args.duration_min is not None and args.duration_min < 0):
            raise invalid()
        old = parents[0]
        row = {**old, "status": "complete"}
        if args.duration_min is not None:
            row["duration_min"] = str(args.duration_min)
        if args.notes is not None:
            row["notes"] = args.notes
        duplicate = row == old
        rows = [row if existing["session_id"] == session_id else existing for existing in sessions]
        path, headers = "data/sessions.csv", writer.SESSION_FIELDS
    else:
        session_date = writer._date(args.date)
        if not parents or parents[0]["date"] != session_date:
            raise invalid()
        if kind == "workout-set":
            if args.set_number <= 0 or args.reps <= 0 or (args.set_count is not None and args.set_count <= 0) or (args.load_lb is not None and args.load_lb < 0) or (args.rir is not None and not 0 <= args.rir <= 10):
                raise invalid()
            row = {"session_id": session_id, "session_date": session_date, "exercise": writer._nonempty("exercise", args.exercise), "equipment": writer._nonempty("equipment", args.equipment), "set_number": str(args.set_number), "set_count": "" if args.set_count is None else str(args.set_count), "load_lb": writer._number(args.load_lb), "load_basis": args.load_basis, "reps": str(args.reps), "rir": "" if args.rir is None else str(args.rir), "form_quality": writer._nonempty("form_quality", args.form_quality), "status": writer._nonempty("status", args.status), "notes": args.notes}
            equipment(config, [row])
            path, headers, keys = "data/sets.csv", writer.SET_FIELDS, ("session_id", "exercise", "set_number")
        else:
            duration = args.duration_seconds if args.duration_seconds is not None else int((args.duration_minutes * 60).to_integral_value(rounding=ROUND_HALF_UP))
            if args.segment_number <= 0 or duration <= 0:
                raise invalid()
            numeric = {name: getattr(args, name) for name in "level steps_per_min speed_mph incline_percent distance_value vertical_feet floors_climbed calories avg_heart_rate_bpm max_heart_rate_bpm".split()}
            if any(value is not None and value < 0 for value in numeric.values()):
                raise invalid()
            row = {"session_id": session_id, "session_date": session_date, "activity": writer._nonempty("activity", args.activity), "equipment": writer._nonempty("equipment", args.equipment), "segment_number": str(args.segment_number), "duration_seconds": str(duration), **{name: writer._number(value) for name, value in numeric.items()}, "distance_unit": args.distance_unit, "source": writer._nonempty("source", args.source), "notes": args.notes}
            path, headers, keys = "data/cardio.csv", writer.CARDIO_FIELDS, ("session_id", "activity", "segment_number")
        rows, duplicate = _append(_rows(files, path, headers), row, keys)
    return {path: csv_text(headers, rows)}, {"saved": True, "sessionId": session_id, "duplicate": duplicate}
