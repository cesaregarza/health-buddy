#!/usr/bin/env python3
"""Start, update, and finish idempotent workout logs."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

SESSION_FIELDS = [
    "session_id",
    "date",
    "workout_type",
    "status",
    "duration_min",
    "bodyweight_lb",
    "notes",
]
SET_FIELDS = [
    "session_id",
    "session_date",
    "exercise",
    "equipment",
    "set_number",
    "set_count",
    "load_lb",
    "load_basis",
    "reps",
    "rir",
    "form_quality",
    "status",
    "notes",
]
CARDIO_FIELDS = [
    "session_id",
    "session_date",
    "activity",
    "equipment",
    "segment_number",
    "duration_seconds",
    "level",
    "steps_per_min",
    "speed_mph",
    "incline_percent",
    "distance_value",
    "distance_unit",
    "vertical_feet",
    "floors_climbed",
    "calories",
    "avg_heart_rate_bpm",
    "max_heart_rate_bpm",
    "source",
    "notes",
]
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SESSIONS_FILE = REPO_ROOT / "data" / "sessions.csv"
DEFAULT_SETS_FILE = REPO_ROOT / "data" / "sets.csv"
DEFAULT_CARDIO_FILE = REPO_ROOT / "data" / "cardio.csv"


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"not a number: {value}") from exc


def _number(value: Decimal | None) -> str:
    if value is None:
        return ""
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")


def _date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("date must use valid YYYY-MM-DD format") from exc


def _nonempty(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _read(
    path: Path, fields: list[str], *, required: bool = False
) -> list[dict[str, str]]:
    path = path.expanduser().resolve()
    if not path.exists():
        if required:
            raise ValueError(f"required CSV does not exist: {path}")
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != fields:
            raise ValueError(f"unexpected CSV header in {path}")
        return list(reader)


def _append(
    path: Path,
    fields: list[str],
    row: dict[str, str],
    *,
    key_fields: tuple[str, ...],
) -> str:
    path = path.expanduser().resolve()
    for existing in _read(path, fields):
        if not all(existing[field] == row[field] for field in key_fields):
            continue
        if existing == row:
            return "unchanged"
        key = ", ".join(f"{field}={row[field]}" for field in key_fields)
        raise ValueError(f"a conflicting row already exists for {key}")

    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        if new_file:
            writer.writeheader()
        writer.writerow(row)
    return "inserted"


def _rewrite(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        newline="",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Idempotently record live workout sessions and sets.",
        epilog=(
            "Example: scripts/log_workout.py start --session-id "
            "2026-08-08-upper-gym-01 --date 2026-08-08 "
            "--workout-type upper_body"
        ),
    )
    parser.add_argument("--sessions-file", type=Path, default=DEFAULT_SESSIONS_FILE)
    parser.add_argument("--sets-file", type=Path, default=DEFAULT_SETS_FILE)
    parser.add_argument("--cardio-file", type=Path, default=DEFAULT_CARDIO_FILE)
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="create a workout session")
    start.add_argument("--session-id", required=True)
    start.add_argument("--date", required=True)
    start.add_argument("--workout-type", required=True)
    start.add_argument(
        "--status",
        choices=("planned", "in_progress", "complete", "deferred"),
        default="in_progress",
    )
    start.add_argument("--duration-min", type=int)
    start.add_argument("--bodyweight-lb", type=_decimal)
    start.add_argument("--notes", default="")

    set_command = commands.add_parser("set", help="append one completed set")
    set_command.add_argument("--session-id", required=True)
    set_command.add_argument("--date", required=True)
    set_command.add_argument("--exercise", required=True)
    set_command.add_argument("--equipment", required=True)
    set_command.add_argument("--set-number", required=True, type=int)
    set_command.add_argument("--set-count", type=int)
    set_command.add_argument("--load-lb", type=_decimal)
    set_command.add_argument("--load-basis", default="")
    set_command.add_argument("--reps", required=True, type=int)
    set_command.add_argument("--rir", type=int)
    set_command.add_argument("--form-quality", default="not_reported")
    set_command.add_argument("--status", default="completed")
    set_command.add_argument("--notes", default="")

    cardio = commands.add_parser("cardio", help="append one completed cardio segment")
    cardio.add_argument("--session-id", required=True)
    cardio.add_argument("--date", required=True)
    cardio.add_argument("--activity", required=True)
    cardio.add_argument("--equipment", default="not_reported")
    cardio.add_argument("--segment-number", required=True, type=int)
    duration = cardio.add_mutually_exclusive_group(required=True)
    duration.add_argument("--duration-seconds", type=int)
    duration.add_argument("--duration-minutes", type=_decimal)
    cardio.add_argument("--level", type=_decimal)
    cardio.add_argument("--steps-per-min", type=_decimal)
    cardio.add_argument("--speed-mph", type=_decimal)
    cardio.add_argument("--incline-percent", type=_decimal)
    cardio.add_argument("--distance-value", type=_decimal)
    cardio.add_argument("--distance-unit", default="")
    cardio.add_argument("--vertical-feet", type=_decimal)
    cardio.add_argument("--floors-climbed", type=_decimal)
    cardio.add_argument("--calories", type=_decimal)
    cardio.add_argument("--avg-heart-rate-bpm", type=_decimal)
    cardio.add_argument("--max-heart-rate-bpm", type=_decimal)
    cardio.add_argument("--source", default="user_reported")
    cardio.add_argument("--notes", default="")

    finish = commands.add_parser("finish", help="mark a session complete")
    finish.add_argument("--session-id", required=True)
    finish.add_argument("--duration-min", type=int)
    finish.add_argument("--notes")
    return parser


def _start(namespace: argparse.Namespace) -> tuple[str, str]:
    if namespace.duration_min is not None and namespace.duration_min < 0:
        raise ValueError("duration_min must not be negative")
    if namespace.bodyweight_lb is not None and namespace.bodyweight_lb <= 0:
        raise ValueError("bodyweight_lb must be positive")
    row = {
        "session_id": _nonempty("session_id", namespace.session_id),
        "date": _date(namespace.date),
        "workout_type": _nonempty("workout_type", namespace.workout_type),
        "status": namespace.status,
        "duration_min": ""
        if namespace.duration_min is None
        else str(namespace.duration_min),
        "bodyweight_lb": _number(namespace.bodyweight_lb),
        "notes": namespace.notes,
    }
    status = _append(
        namespace.sessions_file,
        SESSION_FIELDS,
        row,
        key_fields=("session_id",),
    )
    return status, row["session_id"]


def _set(namespace: argparse.Namespace) -> tuple[str, str]:
    session_id = _nonempty("session_id", namespace.session_id)
    session_date = _date(namespace.date)
    if namespace.set_number <= 0:
        raise ValueError("set_number must be positive")
    if namespace.set_count is not None and namespace.set_count <= 0:
        raise ValueError("set_count must be positive")
    if namespace.load_lb is not None and namespace.load_lb < 0:
        raise ValueError("load_lb must not be negative")
    if namespace.reps <= 0:
        raise ValueError("reps must be positive")
    if namespace.rir is not None and not 0 <= namespace.rir <= 10:
        raise ValueError("rir must be between 0 and 10")

    sessions = _read(namespace.sessions_file, SESSION_FIELDS, required=True)
    matches = [row for row in sessions if row["session_id"] == session_id]
    if not matches:
        raise ValueError(f"session does not exist: {session_id}")
    if matches[0]["date"] != session_date:
        raise ValueError("set date does not match session date")

    row = {
        "session_id": session_id,
        "session_date": session_date,
        "exercise": _nonempty("exercise", namespace.exercise),
        "equipment": _nonempty("equipment", namespace.equipment),
        "set_number": str(namespace.set_number),
        "set_count": "" if namespace.set_count is None else str(namespace.set_count),
        "load_lb": _number(namespace.load_lb),
        "load_basis": namespace.load_basis,
        "reps": str(namespace.reps),
        "rir": "" if namespace.rir is None else str(namespace.rir),
        "form_quality": _nonempty("form_quality", namespace.form_quality),
        "status": _nonempty("status", namespace.status),
        "notes": namespace.notes,
    }
    status = _append(
        namespace.sets_file,
        SET_FIELDS,
        row,
        key_fields=("session_id", "exercise", "set_number"),
    )
    return status, f"{session_id}:{row['exercise']}:{row['set_number']}"


def _cardio(namespace: argparse.Namespace) -> tuple[str, str]:
    session_id = _nonempty("session_id", namespace.session_id)
    session_date = _date(namespace.date)
    if namespace.segment_number <= 0:
        raise ValueError("segment_number must be positive")

    if namespace.duration_seconds is not None:
        duration_seconds = namespace.duration_seconds
    else:
        duration_seconds = int(
            (namespace.duration_minutes * 60).to_integral_value(
                rounding=ROUND_HALF_UP
            )
        )
    if duration_seconds <= 0:
        raise ValueError("cardio duration must be positive")

    numeric_fields = {
        "level": namespace.level,
        "steps_per_min": namespace.steps_per_min,
        "speed_mph": namespace.speed_mph,
        "incline_percent": namespace.incline_percent,
        "distance_value": namespace.distance_value,
        "vertical_feet": namespace.vertical_feet,
        "floors_climbed": namespace.floors_climbed,
        "calories": namespace.calories,
        "avg_heart_rate_bpm": namespace.avg_heart_rate_bpm,
        "max_heart_rate_bpm": namespace.max_heart_rate_bpm,
    }
    for name, value in numeric_fields.items():
        if value is not None and value < 0:
            raise ValueError(f"{name} must not be negative")

    sessions = _read(namespace.sessions_file, SESSION_FIELDS, required=True)
    matches = [row for row in sessions if row["session_id"] == session_id]
    if not matches:
        raise ValueError(f"session does not exist: {session_id}")
    if matches[0]["date"] != session_date:
        raise ValueError("cardio date does not match session date")

    row = {
        "session_id": session_id,
        "session_date": session_date,
        "activity": _nonempty("activity", namespace.activity),
        "equipment": _nonempty("equipment", namespace.equipment),
        "segment_number": str(namespace.segment_number),
        "duration_seconds": str(duration_seconds),
        **{name: _number(value) for name, value in numeric_fields.items()},
        "distance_unit": namespace.distance_unit,
        "source": _nonempty("source", namespace.source),
        "notes": namespace.notes,
    }
    status = _append(
        namespace.cardio_file,
        CARDIO_FIELDS,
        row,
        key_fields=("session_id", "activity", "segment_number"),
    )
    return status, f"{session_id}:{row['activity']}:{row['segment_number']}"


def _finish(namespace: argparse.Namespace) -> tuple[str, str]:
    session_id = _nonempty("session_id", namespace.session_id)
    if namespace.duration_min is not None and namespace.duration_min < 0:
        raise ValueError("duration_min must not be negative")
    rows = _read(namespace.sessions_file, SESSION_FIELDS, required=True)
    matched = False
    changed = False
    for row in rows:
        if row["session_id"] != session_id:
            continue
        matched = True
        updates = {"status": "complete"}
        if namespace.duration_min is not None:
            updates["duration_min"] = str(namespace.duration_min)
        if namespace.notes is not None:
            updates["notes"] = namespace.notes
        if any(row[key] != value for key, value in updates.items()):
            row.update(updates)
            changed = True
    if not matched:
        raise ValueError(f"session does not exist: {session_id}")
    if changed:
        _rewrite(namespace.sessions_file, SESSION_FIELDS, rows)
    return ("updated" if changed else "unchanged"), session_id


def main(argv: Sequence[str] | None = None) -> int:
    namespace = _parser().parse_args(argv)
    try:
        if namespace.command == "start":
            status, key = _start(namespace)
        elif namespace.command == "set":
            status, key = _set(namespace)
        elif namespace.command == "cardio":
            status, key = _cardio(namespace)
        else:
            status, key = _finish(namespace)
    except (OSError, ValueError) as exc:
        print(f"log_workout: error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {"command": namespace.command, "key": key, "status": status},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
