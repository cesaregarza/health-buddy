"""Bounded, read-only progression overlay for reviewed strength templates."""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    from scripts.set_classification import is_nonworking_set
except ModuleNotFoundError:  # direct scripts/next_workout.py execution
    from set_classification import (  # type: ignore[import-not-found,no-redef]
        is_nonworking_set,
    )


class ProgressionError(ValueError):
    """Raised when a progression configuration or history row is malformed."""


SET_COLUMNS = (
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
)


@dataclass(frozen=True)
class SetRecord:
    session_id: str
    session_date: date
    exercise: str
    equipment: str
    set_number: int | None
    load: float | None
    load_basis: str
    reps: int
    rir: int | None
    form_quality: str
    status: str
    notes: str


def _text(value: str | None, name: str, context: str) -> str:
    if value is None or not value.strip():
        raise ProgressionError(f"{context}: missing required {name}")
    return value.strip()


def _integer(
    value: str | None, name: str, context: str, *, required: bool
) -> int | None:
    if value is None or not value.strip():
        if required:
            raise ProgressionError(f"{context}: missing required {name}")
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ProgressionError(f"{context}: invalid {name}") from exc


def _load(value: str | None, context: str) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ProgressionError(f"{context}: invalid load_lb") from exc
    if not parsed.is_finite():
        raise ProgressionError(f"{context}: invalid load_lb")
    return float(parsed)


def load_sets(path: Path) -> list[SetRecord]:
    """Validate and load set rows; aggregate rows remain non-qualifying."""

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [
            field for field in SET_COLUMNS if field not in (reader.fieldnames or [])
        ]
        if missing:
            raise ProgressionError(
                f"{path}: missing required set columns: {', '.join(missing)}"
            )
        records: list[SetRecord] = []
        for line, row in enumerate(reader, start=2):
            context = f"{path}:{line}"
            session_id = _text(row.get("session_id"), "session_id", context)
            raw_date = _text(row.get("session_date"), "session_date", context)
            try:
                session_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise ProgressionError(f"{context}: invalid session_date") from exc
            exercise = _text(row.get("exercise"), "exercise", context)
            equipment = _text(row.get("equipment"), "equipment", context)
            status = _text(row.get("status"), "status", context)
            notes = row.get("notes", "") or ""
            aggregate = (
                status.casefold() == "reported_aggregate"
                or "aggregate" in notes.casefold()
            )
            count = _integer(
                row.get("set_count"), "set_count", context, required=aggregate
            )
            if count is not None and count < 1:
                raise ProgressionError(f"{context}: set_count must be positive")
            set_number = _integer(
                row.get("set_number"), "set_number", context, required=not aggregate
            )
            if set_number is not None and set_number < 1:
                raise ProgressionError(f"{context}: set_number must be positive")
            load = _load(row.get("load_lb"), context)
            load_basis = _text(row.get("load_basis"), "load_basis", context)
            reps = _integer(row.get("reps"), "reps", context, required=True)
            assert reps is not None
            if reps < 0:
                raise ProgressionError(f"{context}: reps must not be negative")
            rir = _integer(row.get("rir"), "rir", context, required=False)
            if rir is not None and rir < 0:
                raise ProgressionError(f"{context}: rir must not be negative")
            form = _text(row.get("form_quality"), "form_quality", context)
            records.append(
                SetRecord(
                    session_id,
                    session_date,
                    exercise,
                    equipment,
                    set_number,
                    load,
                    load_basis,
                    reps,
                    rir,
                    form,
                    status,
                    notes,
                )
            )
    return records


def _number(value: float | int) -> str:
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _excluded(record: SetRecord) -> bool:
    return is_nonworking_set(record.status, record.notes)


def _working(record: SetRecord) -> bool:
    return record.status.casefold() not in {
        "planned",
        "in_progress",
        "skipped",
        "deferred",
    } and not _excluded(record)


def _good_form(record: SetRecord) -> bool:
    return record.status.casefold() in {
        "completed",
        "complete",
    } and record.form_quality.casefold() in {"clean", "controlled"}


def _finite_positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value > 0
    )


def _config(exercise: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any] | None:
    value = exercise.get("progression")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ProgressionError(
            f"{exercise.get('exercise', 'exercise')}: progression must be an object"
        )
    required = (
        "exercise_id",
        "equipment_id",
        "load_basis",
        "baseline_load",
        "sets",
        "rep_min",
        "rep_max",
        "baseline_reps",
        "next_load",
    )
    for key in required:
        if key not in value:
            raise ProgressionError(
                f"progression {exercise.get('exercise', 'exercise')}: missing {key}"
            )
    for key in ("exercise_id", "equipment_id", "load_basis"):
        if not isinstance(value[key], str) or not value[key]:
            raise ProgressionError(f"progression {key} must be non-empty text")
    if not _finite_positive(value["baseline_load"]):
        raise ProgressionError("progression baseline_load must be finite and positive")
    if (
        not isinstance(value["sets"], int)
        or isinstance(value["sets"], bool)
        or value["sets"] < 1
    ):
        raise ProgressionError("progression sets must be a positive integer")
    if (
        not isinstance(value["rep_min"], int)
        or not isinstance(value["rep_max"], int)
        or value["rep_min"] < 1
        or value["rep_max"] < value["rep_min"]
    ):
        raise ProgressionError("progression rep range is invalid")
    reps = value["baseline_reps"]
    if (
        not isinstance(reps, list)
        or len(reps) != value["sets"]
        or any(
            not isinstance(rep, int)
            or isinstance(rep, bool)
            or not value["rep_min"] <= rep <= value["rep_max"]
            for rep in reps
        )
    ):
        raise ProgressionError(
            "progression baseline_reps must match sets and rep range"
        )
    next_load = value["next_load"]
    if next_load is not None and not _finite_positive(next_load):
        raise ProgressionError(
            "progression next_load must be null or finite and positive"
        )
    fallback = value.get("fallback_load")
    if fallback is not None and not _finite_positive(fallback):
        raise ProgressionError("progression fallback_load must be finite and positive")
    defaults = (
        ("qualifying_rir", 2),
        ("small_increment_max_fraction", 0.15),
        ("qualifying_exposures_for_small_increment", 1),
    )
    for key, default in defaults:
        if policy.get(key, default) != default:
            raise ProgressionError(f"unsupported progression_policy {key}")
    for key in ("backoff_sets", "backoff_rep_min", "backoff_rep_max"):
        if key in value and (
            not isinstance(value[key], int)
            or isinstance(value[key], bool)
            or value[key] < 1
        ):
            raise ProgressionError(f"progression {key} must be a positive integer")
    if value.get("backoff_rep_max", value.get("backoff_rep_min", 1)) < value.get(
        "backoff_rep_min", 1
    ):
        raise ProgressionError("progression backoff rep range is invalid")
    return value


def _session_groups(
    records: list[SetRecord],
) -> list[tuple[date, str, list[SetRecord]]]:
    grouped: dict[tuple[date, str], list[SetRecord]] = {}
    for record in records:
        grouped.setdefault((record.session_date, record.session_id), []).append(record)
    return [(day, sid, rows) for (day, sid), rows in sorted(grouped.items())]


def _full_primary(
    rows: list[SetRecord], load: float, sets: int
) -> tuple[list[SetRecord] | None, bool]:
    candidates = [row for row in rows if row.load is not None and row.load == load]
    by_number: dict[int, SetRecord] = {}
    duplicate = False
    for row in candidates:
        if row.set_number is None:
            continue
        if row.set_number in by_number:
            duplicate = True
        by_number[row.set_number] = row
    if duplicate:
        return None, True
    if len(by_number) < sets:
        return None, False
    # Ordinals include warm-ups in the canonical log; work may be sets 2/3.
    return [by_number[number] for number in sorted(by_number)[:sets]], False


def _qualifies(rows: list[SetRecord], rep_min: int) -> bool:
    return bool(rows) and all(
        _good_form(row) and row.rir is not None and row.rir >= 2 and row.reps >= rep_min
        for row in rows
    )


def _successful_mixed_trial(
    rows: list[SetRecord], config: dict[str, Any], accepted: float
) -> tuple[list[SetRecord], float, float] | None:
    """Recognize two clean mixed-load sets without claiming two at the higher load."""
    if config["sets"] != 2 or len(rows) != 2:
        return None
    ordered = sorted(rows, key=lambda row: row.set_number or 0)
    if any(row.set_number is None for row in ordered):
        return None
    if ordered[0].set_number == ordered[1].set_number:
        return None
    loads: list[float] = []
    for row in ordered:
        load = row.load
        if load is None:
            return None
        loads.append(load)
    lower, higher = sorted(loads)
    if (
        lower < accepted
        or higher <= lower
        or not _qualifies(ordered, config["rep_min"])
    ):
        return None
    # For a coarse jump, the first set must be the trial and the second its
    # backoff. Small-step increases may be tested after a lighter first set.
    if (higher - lower) / lower > 0.15 and ordered[0].load != higher:
        return None
    return ordered, higher, lower


def _base_result(
    config: dict[str, Any], status: str, source_date: str | None, source_id: str | None
) -> dict[str, Any]:
    return {
        "status": status,
        "exercise_id": config["exercise_id"],
        "equipment_id": config["equipment_id"],
        "load_basis": config["load_basis"],
        "source_date": source_date,
        "source_session_id": source_id,
        "baseline_load": config["baseline_load"],
        "current_load": config["baseline_load"],
        "demonstrated_load": None,
        "baseline_promoted": False,
        "recommended_load": None,
        "fallback_load": config.get("fallback_load"),
        "rep_targets": list(config["baseline_reps"]),
        "evidence_sets": [],
    }


def _load_label(load: float, basis: str) -> str:
    suffix = "lb per hand" if basis == "per_hand" else "lb stack"
    return f"{_number(load)} {suffix}"


def _target(
    result: dict[str, Any], config: dict[str, Any], *, reviewed: str | None = None
) -> str:
    if reviewed is not None:
        return reviewed
    reps = result["rep_targets"]
    rep_text = (
        str(reps[0]) if len(set(reps)) == 1 else "/".join(str(rep) for rep in reps)
    )
    prefix = (
        f"{_load_label(result['current_load'], result['load_basis'])}; "
        f"{len(reps)} primary sets x {rep_text} reps"
    )
    status = result["status"]
    if status == "eligible_next_load":
        if result.get("recommended_load") is None:
            return (
                prefix + "; verify the smallest available increment before loading it"
            )
        return (
            f"All {len(reps)} primary sets at "
            f"{_load_label(result['recommended_load'], result['load_basis'])}, "
            f"starting at {config['rep_min']} reps"
        )
    if status == "coarse_increment_trial":
        return (
            f"Trial one primary set at "
            f"{_load_label(result['recommended_load'], result['load_basis'])} "
            f"for {config['rep_min']} reps with at least 2 RIR; complete the "
            f"remaining primary sets at "
            f"{_load_label(result['current_load'], result['load_basis'])}. "
            "An attempted trial counts toward the set total even if it falls short."
        )
    if status == "mixed_trial_succeeded":
        fallback = _load_label(result["fallback_load"], result["load_basis"])
        return (
            f"Try {len(reps)} primary sets at "
            f"{_load_label(result['recommended_load'], result['load_basis'])} "
            f"for {config['rep_min']} reps with at least 2 RIR; if a set cannot "
            f"meet that standard, use {fallback} "
            "for that remaining set. Do not add a replacement set."
        )
    if status == "baseline_promoted":
        return (
            prefix
            + "; demonstrated new baseline; build reps here before another increment"
        )
    if status in {"incomplete", "no_comparable_data"}:
        target = prefix + "; evidence incomplete, preserve the reviewed baseline target"
        if result.get("fallback_load") is not None:
            target += (
                f"; use {_number(result['fallback_load'])} "
                f"{result['load_basis']} as the reviewed fallback"
            )
        return target
    return prefix + "; add reps only while every set remains at least 2 RIR"


def _backoff_text(config: dict[str, Any], fallback_load: float | None = None) -> str:
    count = config.get("backoff_sets", 0)
    if fallback_load is None:
        fallback_load = config.get("fallback_load")
    if not count or fallback_load is None:
        return ""
    low = config.get("backoff_rep_min", config["rep_min"])
    high = config.get("backoff_rep_max", config["rep_max"])
    reps = str(low) if low == high else f"{low}-{high}"
    return (
        f"; then {count} backoff set at "
        f"{_load_label(fallback_load, config['load_basis'])} x {reps} reps"
    )


def _evaluate(
    config: dict[str, Any],
    sessions: list[Any],
    records: list[SetRecord],
    target_date: date,
    policy: dict[str, Any],
) -> dict[str, Any]:
    session_ids = {
        str(getattr(session, "session_id", "")): session.session_date
        for session in sessions
        if getattr(session, "session_id", None)
        and session.status == "complete"
        and session.session_date <= target_date
    }
    comparable: list[tuple[date, str, list[SetRecord]]] = []
    for day, sid, rows in _session_groups(records):
        joined = sid in session_ids and session_ids[sid] == day
        filtered = [
            row
            for row in rows
            if row.exercise == config["exercise_id"]
            and row.equipment == config["equipment_id"]
            and row.load_basis == config["load_basis"]
            and _working(row)
        ]
        if joined and filtered:
            comparable.append((day, sid, filtered))
    if not comparable:
        result = _base_result(config, "no_comparable_data", None, None)
        result["message"] = (
            "No comparable completed working-set exposure; reviewed baseline preserved."
        )
        result["target"] = _target(result, config)
        return result
    accepted = float(config["baseline_load"])
    accepted_date: str | None = None
    ambiguous_days = {
        day
        for day, _, _ in comparable
        if sum(other_day == day for other_day, _, _ in comparable) > 1
    }
    for day, _sid, rows in comparable:
        if day in ambiguous_days:
            continue
        possible: list[tuple[float, list[SetRecord]]] = []
        for load in sorted(
            {row.load for row in rows if row.load is not None}, reverse=True
        ):
            primary, duplicate = _full_primary(rows, load, config["sets"])
            if (
                primary is not None
                and not duplicate
                and load >= accepted
                and _qualifies(primary, config["rep_min"])
            ):
                possible.append((load, primary))
        if possible:
            accepted, _ = possible[0]
            accepted_date = day.isoformat()
    day, sid, latest_rows = comparable[-1]
    primary, duplicate = _full_primary(latest_rows, accepted, config["sets"])
    result = _base_result(config, "incomplete", day.isoformat(), sid)
    result["current_load"] = accepted
    result["demonstrated_load"] = (
        accepted if accepted != config["baseline_load"] else None
    )
    result["baseline_promoted"] = accepted != config["baseline_load"]
    result["message"] = f"Latest comparable exposure: {day.isoformat()}."
    if day in ambiguous_days:
        result["source_session_id"] = None
        result["review_required"] = True
        result["message"] += " Multiple same-day exposures have no verified order."
        result["target"] = _target(result, config)
        return result
    if accepted_date and accepted_date != day.isoformat():
        result["message"] += (
            f" Current reviewed baseline remains {_number(accepted)} "
            f"from {accepted_date}."
        )
    if duplicate:
        result["review_required"] = True
        result["message"] += " Duplicate primary set ordinals make evidence incomplete."
    if primary is None:
        trial = _successful_mixed_trial(latest_rows, config, accepted)
        if trial is not None:
            trial_rows, higher, lower = trial
            result["status"] = "mixed_trial_succeeded"
            result["recommended_load"] = higher
            result["fallback_load"] = lower
            result["rep_targets"] = [config["rep_min"]] * config["sets"]
            result["evidence_sets"] = [
                {
                    "set_number": row.set_number,
                    "load": row.load,
                    "reps": row.reps,
                    "rir": row.rir,
                    "form_quality": row.form_quality,
                }
                for row in trial_rows
            ]
            result["message"] += (
                " One clean higher-load set and one lower-load working set "
                "support attempting both at the higher load next time; "
                "two higher-load sets are not yet demonstrated."
            )
            result["target"] = _target(result, config)
            return result
        higher_loads = sorted(
            {
                row.load
                for row in latest_rows
                if row.load is not None and row.load > accepted
            },
            reverse=True,
        )
        if len(latest_rows) == config["sets"]:
            for higher in higher_loads:
                higher_primary, higher_duplicate = _full_primary(
                    latest_rows, higher, config["sets"]
                )
                if higher_duplicate or higher_primary is None:
                    continue
                if not all(
                    _good_form(row)
                    and row.rir is not None
                    and row.rir >= 1
                    and row.reps >= config["rep_min"]
                    for row in higher_primary
                ):
                    continue
                result["status"] = "hold_2_rir"
                result["current_load"] = higher
                result["rep_targets"] = [
                    min(config["rep_max"], row.reps + 1)
                    if row.rir is not None and row.rir >= 2
                    else row.reps
                    for row in higher_primary
                ]
                result["evidence_sets"] = [
                    {
                        "set_number": row.set_number,
                        "load": row.load,
                        "reps": row.reps,
                        "rir": row.rir,
                        "form_quality": row.form_quality,
                    }
                    for row in higher_primary
                ]
                result["message"] += (
                    " Both sets at the higher load met the rep floor with "
                    "controlled form, but a 1-RIR set holds further load "
                    "progression until reserve improves."
                )
                result["target"] = _target(result, config) + _backoff_text(config)
                return result
        attempted = [
            row for row in latest_rows if row.load is not None and row.load >= accepted
        ]
        result["review_required"] = result.get("review_required", False) or any(
            not _good_form(row)
            or row.rir is None
            or row.rir == 0
            or row.reps < config["rep_min"]
            for row in attempted
        )
        result["message"] += (
            " Required matching primary sets at the current load were not demonstrated."
        )
        result["target"] = _target(result, config)
        return result
    result["evidence_sets"] = [
        {
            "set_number": row.set_number,
            "load": row.load,
            "reps": row.reps,
            "rir": row.rir,
            "form_quality": row.form_quality,
        }
        for row in primary
    ]
    if not all(_good_form(row) and row.rir is not None for row in primary):
        result["review_required"] = True
        if all(row.rir is not None for row in primary) and any(
            row.form_quality.casefold() == "not_reported" for row in primary
        ):
            result["review_reason"] = "form_not_reported"
            result["message"] += (
                " Form was not reported for every primary set; RIR was recorded."
            )
        else:
            result["review_reason"] = "form_or_rir_unverified"
            result["message"] += " Form or RIR is unverified."
        result["target"] = _target(result, config)
        return result
    if any(row.rir == 0 or row.reps < config["rep_min"] for row in primary):
        result["review_required"] = True
        result["message"] += (
            " A primary set reached zero reserve or missed the rep range."
        )
        result["target"] = _target(result, config)
        return result
    verified_rirs = [row.rir for row in primary if row.rir is not None]
    result["rep_targets"] = [
        min(config["rep_max"], row.reps + 1) if rir >= 2 else row.reps
        for row, rir in zip(primary, verified_rirs, strict=True)
    ]
    if any(rir < 2 for rir in verified_rirs):
        result["status"] = "hold_2_rir"
        result["message"] += (
            " Lower-reserve sets hold their reps while other clean sets may add one."
        )
        result["target"] = _target(result, config) + _backoff_text(config)
        return result
    result["status"] = "baseline_promoted" if result["baseline_promoted"] else "reps"
    if all(row.reps >= config["rep_max"] for row in primary):
        next_load = config.get("next_load")
        if next_load is None or next_load <= accepted:
            result["status"] = "eligible_next_load"
            result["message"] += (
                " Top range at at least 2 RIR earned an increment; verify the "
                "smallest available setting."
            )
        elif (next_load - accepted) / accepted <= 0.15:
            result["status"] = "eligible_next_load"
            result["recommended_load"] = next_load
            result["current_load"] = next_load
            result["rep_targets"] = [config["rep_min"]] * config["sets"]
            result["message"] += (
                " Top range at at least 2 RIR earned the next small increment."
            )
        else:
            result["status"] = "coarse_increment_trial"
            result["recommended_load"] = next_load
            result["fallback_load"] = accepted
            result["message"] += (
                " Top range at at least 2 RIR earned a coarse-load trial."
            )
    result["target"] = _target(result, config) + _backoff_text(config)
    return result


def _as_record(value: SetRecord | dict[str, Any]) -> SetRecord:
    if isinstance(value, SetRecord):
        return value
    try:
        raw_date = value.get("session_date", value.get("date"))
        if raw_date is None:
            raise ProgressionError("set record missing session date")
        day = (
            raw_date
            if isinstance(raw_date, date)
            else date.fromisoformat(str(raw_date))
        )
        raw_load = value.get("load", value.get("load_lb"))
        return SetRecord(
            str(value["session_id"]),
            day,
            str(value["exercise"]),
            str(value["equipment"]),
            None if value.get("set_number") in (None, "") else int(value["set_number"]),
            None if raw_load in (None, "") else float(str(raw_load)),
            str(value["load_basis"]),
            int(value["reps"]),
            None if value.get("rir") in (None, "") else int(value["rir"]),
            str(value.get("form_quality", "")),
            str(value.get("status", "")),
            str(value.get("notes", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProgressionError("set history contains a malformed record") from exc


def _render_prescription(
    exercise: dict[str, Any], config: dict[str, Any], result: dict[str, Any]
) -> None:
    """Keep load, work and coaching text distinct while preserving set counts."""
    original_target = exercise.get("next_target", "")
    if result["status"] in {"incomplete", "no_comparable_data"}:
        result["reviewed_next_target"] = original_target
        if not result.get("review_required") and not result["baseline_promoted"]:
            result["target"] = original_target
            return
        if result.get("review_reason") == "form_not_reported":
            result["target"] = (
                f"Form not logged for the {result['source_date']} primary sets. "
                f"Hold the established "
                f"{_load_label(result['current_load'], config['load_basis'])}; "
                "confirm controlled form and full range before treating those reps "
                "as progression evidence."
            )
        else:
            result["target"] = (
                f"Latest evidence needs review before adding reps or load. "
                f"Retain the established "
                f"{_load_label(result['current_load'], config['load_basis'])} "
                "as context; use the plan's form, readiness and fallback rules."
            )

    load = _load_label(result["current_load"], config["load_basis"])
    reps = result["rep_targets"]
    work = f"{config['sets']} primary sets x " + (
        str(reps[0]) if len(set(reps)) == 1 else "/".join(map(str, reps))
    )
    if result["status"] == "coarse_increment_trial":
        load = (
            f"{_load_label(result['recommended_load'], config['load_basis'])} "
            f"trial; {load} for remaining primary sets"
        )
        work = (
            f"1 trial x {config['rep_min']}; "
            f"{config['sets'] - 1} primary backoff x "
            f"{config['rep_min']}-{config['rep_max']}"
        )
    elif result["status"] == "mixed_trial_succeeded":
        load = (
            f"{_load_label(result['recommended_load'], config['load_basis'])} "
            f"for up to {config['sets']} primary sets; "
            f"{_load_label(result['fallback_load'], config['load_basis'])} fallback"
        )
    if config.get("backoff_sets"):
        load += (
            f"; {_load_label(config['fallback_load'], config['load_basis'])} "
            "for the additional backoff"
        )
        work += (
            f"; {config['backoff_sets']} additional backoff x "
            f"{config['backoff_rep_min']}-{config['backoff_rep_max']}"
        )
    exercise.update(load=load, work=work, next_target=result["target"])


def apply_progression(
    template: dict[str, Any],
    sessions: Iterable[Any],
    set_history: Iterable[SetRecord | dict[str, Any]],
    target_date: date,
    policy: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Deep-copy a strength template and overlay validated progression evidence."""
    result_template = deepcopy(template)
    records = [_as_record(value) for value in set_history]
    policy = policy or {}
    session_list = list(sessions)
    results: dict[str, dict[str, Any]] = {}
    for exercise in result_template.get("exercises", []):
        config = _config(exercise, policy)
        if config is None:
            continue
        evaluated = _evaluate(config, session_list, records, target_date, policy)
        _render_prescription(exercise, config, evaluated)
        exercise["progression_result"] = evaluated
        results[config["exercise_id"]] = evaluated
    return result_template, results


load_set_history = load_sets
progression_overlay = apply_progression
