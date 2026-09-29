"""Strict protocol-v1 validation for HealthKit upload batches."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

MAX_RECORDS_PER_BATCH = 500
MAX_DELETIONS_PER_BATCH = 500
MAX_IDENTIFIER_LENGTH = 200

DAILY_AGGREGATE_TYPES = {
    "HKQuantityTypeIdentifierActiveEnergyBurned",
    "HKQuantityTypeIdentifierAppleExerciseTime",
    "HKQuantityTypeIdentifierAppleStandTime",
    "HKQuantityTypeIdentifierBasalEnergyBurned",
    "HKQuantityTypeIdentifierDistanceWalkingRunning",
    "HKQuantityTypeIdentifierFlightsClimbed",
    "HKQuantityTypeIdentifierStepCount",
}

QUANTITY_TYPES = {
    "HKQuantityTypeIdentifierAppleWalkingSteadiness",
    "HKQuantityTypeIdentifierBodyFatPercentage",
    "HKQuantityTypeIdentifierBodyMass",
    "HKQuantityTypeIdentifierBodyMassIndex",
    "HKQuantityTypeIdentifierHeartRate",
    "HKQuantityTypeIdentifierHeartRateRecoveryOneMinute",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
    "HKQuantityTypeIdentifierLeanBodyMass",
    "HKQuantityTypeIdentifierRespiratoryRate",
    "HKQuantityTypeIdentifierRestingHeartRate",
    "HKQuantityTypeIdentifierStairAscentSpeed",
    "HKQuantityTypeIdentifierStairDescentSpeed",
    "HKQuantityTypeIdentifierVO2Max",
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage",
    "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage",
    "HKQuantityTypeIdentifierWalkingSpeed",
    "HKQuantityTypeIdentifierWalkingStepLength",
}

CATEGORY_TYPES = {"HKCategoryTypeIdentifierSleepAnalysis"}
WORKOUT_TYPE = "HKWorkoutTypeIdentifier"
ALLOWED_TYPES = DAILY_AGGREGATE_TYPES | QUANTITY_TYPES | CATEGORY_TYPES | {WORKOUT_TYPE}

RECORD_KEYS = {
    "recordId",
    "recordKind",
    "typeIdentifier",
    "startDate",
    "endDate",
    "creationDate",
    "localDate",
    "timezone",
    "value",
    "unit",
    "source",
    "device",
    "workout",
}
SOURCE_KEYS = {"bundleIdentifier", "name", "version", "productType"}
DEVICE_KEYS = {
    "name",
    "manufacturer",
    "model",
    "hardwareVersion",
    "softwareVersion",
    "localIdentifier",
    "udiDeviceIdentifier",
}
WORKOUT_KEYS = {
    "activityType",
    "durationSeconds",
    "totalEnergyValue",
    "totalEnergyUnit",
    "totalDistanceValue",
    "totalDistanceUnit",
}
DELETION_KEYS = {"recordId", "typeIdentifier", "observedAt"}
BATCH_KEYS = {
    "schemaVersion",
    "batchId",
    "deviceId",
    "generatedAt",
    "records",
    "deletions",
}

_RECORD_ID = re.compile(r"^[A-Za-z0-9:._+\-]{1,200}$")


class BatchValidationError(ValueError):
    """Raised when an upload does not conform to protocol v1."""


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BatchValidationError(f"{context} must be an object")
    return value


def _strict_keys(value: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise BatchValidationError(
            f"{context} contains unsupported fields: {', '.join(unknown)}"
        )


def _string(
    value: Any,
    context: str,
    *,
    required: bool = True,
    maximum: int = MAX_IDENTIFIER_LENGTH,
) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise BatchValidationError(f"{context} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise BatchValidationError(f"{context} exceeds {maximum} characters")
    return normalized


def _uuid(value: Any, context: str) -> str:
    normalized = _string(value, context)
    assert normalized is not None
    try:
        return str(UUID(normalized))
    except ValueError as exc:
        raise BatchValidationError(f"{context} must be a UUID") from exc


def _timestamp(value: Any, context: str, *, required: bool = True) -> str | None:
    normalized = _string(value, context, required=required, maximum=64)
    if normalized is None:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatchValidationError(f"{context} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise BatchValidationError(f"{context} must include a UTC offset")
    return normalized


def _finite_number(value: Any, context: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BatchValidationError(f"{context} must be a number")
    if not math.isfinite(float(value)):
        raise BatchValidationError(f"{context} must be finite")
    return int(value) if isinstance(value, int) else float(value)


def _optional_mapping(
    value: Any, allowed: set[str], context: str
) -> dict[str, str | None] | None:
    if value is None:
        return None
    mapping = _object(value, context)
    _strict_keys(mapping, allowed, context)
    result: dict[str, str | None] = {}
    for key, item in mapping.items():
        result[key] = _string(item, f"{context}.{key}", required=False, maximum=255)
    return result


@dataclass(frozen=True)
class HealthRecord:
    record_id: str
    record_kind: str
    type_identifier: str
    start_date: str
    end_date: str
    creation_date: str | None
    local_date: str | None
    # Native samples currently omit this field. Preserve that raw null; the
    # deployment's analysis layer uses its configured IANA presentation zone.
    timezone: str | None
    value: int | float | str | None
    unit: str | None
    source: dict[str, str | None] | None
    device: dict[str, str | None] | None
    workout: dict[str, int | float | str | None] | None
    normalized: dict[str, Any]


@dataclass(frozen=True)
class HealthDeletion:
    record_id: str
    type_identifier: str
    observed_at: str
    normalized: dict[str, Any]


@dataclass(frozen=True)
class Batch:
    schema_version: int
    batch_id: str
    device_id: str
    generated_at: str
    records: tuple[HealthRecord, ...]
    deletions: tuple[HealthDeletion, ...]
    normalized: dict[str, Any]


def _parse_workout(value: Any, context: str) -> dict[str, Any] | None:
    if value is None:
        return None
    mapping = _object(value, context)
    _strict_keys(mapping, WORKOUT_KEYS, context)
    result: dict[str, int | float | str | None] = {}
    for key, item in mapping.items():
        if key in {"durationSeconds", "totalEnergyValue", "totalDistanceValue"}:
            result[key] = _finite_number(item, f"{context}.{key}")
        else:
            result[key] = _string(item, f"{context}.{key}", required=False, maximum=255)
    return result


def _parse_record(value: Any, index: int) -> HealthRecord:
    context = f"records[{index}]"
    mapping = _object(value, context)
    _strict_keys(mapping, RECORD_KEYS, context)

    record_id = _string(mapping.get("recordId"), f"{context}.recordId")
    assert record_id is not None
    if not _RECORD_ID.fullmatch(record_id):
        raise BatchValidationError(f"{context}.recordId has unsupported characters")

    record_kind = _string(mapping.get("recordKind"), f"{context}.recordKind")
    assert record_kind is not None
    if record_kind not in {"quantity", "category", "dailyAggregate", "workout"}:
        raise BatchValidationError(f"{context}.recordKind is unsupported")

    type_identifier = _string(
        mapping.get("typeIdentifier"), f"{context}.typeIdentifier"
    )
    assert type_identifier is not None
    if type_identifier not in ALLOWED_TYPES:
        raise BatchValidationError(f"{context}.typeIdentifier is not allowlisted")

    expected_kind = (
        "dailyAggregate"
        if type_identifier in DAILY_AGGREGATE_TYPES
        else "quantity"
        if type_identifier in QUANTITY_TYPES
        else "category"
        if type_identifier in CATEGORY_TYPES
        else "workout"
    )
    if record_kind != expected_kind:
        raise BatchValidationError(
            f"{context}.recordKind must be {expected_kind} for {type_identifier}"
        )

    start_date = _timestamp(mapping.get("startDate"), f"{context}.startDate")
    end_date = _timestamp(mapping.get("endDate"), f"{context}.endDate")
    assert start_date is not None and end_date is not None
    if datetime.fromisoformat(end_date.replace("Z", "+00:00")) < datetime.fromisoformat(
        start_date.replace("Z", "+00:00")
    ):
        raise BatchValidationError(f"{context}.endDate precedes startDate")

    creation_date = _timestamp(
        mapping.get("creationDate"), f"{context}.creationDate", required=False
    )
    local_date = _string(
        mapping.get("localDate"), f"{context}.localDate", required=False, maximum=10
    )
    timezone = _string(
        mapping.get("timezone"), f"{context}.timezone", required=False, maximum=100
    )
    if record_kind == "dailyAggregate":
        if local_date is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", local_date):
            raise BatchValidationError(
                f"{context}.localDate is required for dailyAggregate"
            )
        if timezone is None:
            raise BatchValidationError(
                f"{context}.timezone is required for dailyAggregate"
            )

    raw_value = mapping.get("value")
    parsed_value: int | float | str | None
    if record_kind in {"quantity", "dailyAggregate"}:
        parsed_value = _finite_number(raw_value, f"{context}.value")
    elif record_kind == "category":
        parsed_value = _string(raw_value, f"{context}.value", maximum=255)
    else:
        if raw_value is not None:
            raise BatchValidationError(f"{context}.value must be null for a workout")
        parsed_value = None

    unit = _string(mapping.get("unit"), f"{context}.unit", required=False, maximum=80)
    if record_kind in {"quantity", "dailyAggregate"} and unit is None:
        raise BatchValidationError(f"{context}.unit is required")

    source = _optional_mapping(mapping.get("source"), SOURCE_KEYS, f"{context}.source")
    device = _optional_mapping(mapping.get("device"), DEVICE_KEYS, f"{context}.device")
    workout = _parse_workout(mapping.get("workout"), f"{context}.workout")
    if record_kind == "workout" and workout is None:
        raise BatchValidationError(f"{context}.workout is required")
    if record_kind != "workout" and workout is not None:
        raise BatchValidationError(f"{context}.workout is only valid for workouts")

    normalized = {
        "recordId": record_id,
        "recordKind": record_kind,
        "typeIdentifier": type_identifier,
        "startDate": start_date,
        "endDate": end_date,
        "creationDate": creation_date,
        "localDate": local_date,
        "timezone": timezone,
        "value": parsed_value,
        "unit": unit,
        "source": source,
        "device": device,
        "workout": workout,
    }
    return HealthRecord(
        record_id=record_id,
        record_kind=record_kind,
        type_identifier=type_identifier,
        start_date=start_date,
        end_date=end_date,
        creation_date=creation_date,
        local_date=local_date,
        timezone=timezone,
        value=parsed_value,
        unit=unit,
        source=source,
        device=device,
        workout=workout,
        normalized=normalized,
    )


def _parse_deletion(value: Any, index: int) -> HealthDeletion:
    context = f"deletions[{index}]"
    mapping = _object(value, context)
    _strict_keys(mapping, DELETION_KEYS, context)
    record_id = _string(mapping.get("recordId"), f"{context}.recordId")
    assert record_id is not None
    if not _RECORD_ID.fullmatch(record_id):
        raise BatchValidationError(f"{context}.recordId has unsupported characters")
    type_identifier = _string(
        mapping.get("typeIdentifier"), f"{context}.typeIdentifier"
    )
    assert type_identifier is not None
    if type_identifier not in ALLOWED_TYPES - DAILY_AGGREGATE_TYPES:
        raise BatchValidationError(f"{context}.typeIdentifier is not deletable")
    observed_at = _timestamp(mapping.get("observedAt"), f"{context}.observedAt")
    assert observed_at is not None
    normalized = {
        "recordId": record_id,
        "typeIdentifier": type_identifier,
        "observedAt": observed_at,
    }
    return HealthDeletion(
        record_id=record_id,
        type_identifier=type_identifier,
        observed_at=observed_at,
        normalized=normalized,
    )


def parse_batch(value: Any) -> Batch:
    """Validate and normalize one protocol-v1 upload batch."""

    mapping = _object(value, "batch")
    _strict_keys(mapping, BATCH_KEYS, "batch")
    if mapping.get("schemaVersion") != 1:
        raise BatchValidationError("schemaVersion must be 1")
    batch_id = _uuid(mapping.get("batchId"), "batchId")
    device_id = _uuid(mapping.get("deviceId"), "deviceId")
    generated_at = _timestamp(mapping.get("generatedAt"), "generatedAt")
    assert generated_at is not None

    raw_records = mapping.get("records")
    raw_deletions = mapping.get("deletions")
    if not isinstance(raw_records, list):
        raise BatchValidationError("records must be an array")
    if not isinstance(raw_deletions, list):
        raise BatchValidationError("deletions must be an array")
    if len(raw_records) > MAX_RECORDS_PER_BATCH:
        raise BatchValidationError(
            f"records exceeds the {MAX_RECORDS_PER_BATCH}-record limit"
        )
    if len(raw_deletions) > MAX_DELETIONS_PER_BATCH:
        raise BatchValidationError(
            f"deletions exceeds the {MAX_DELETIONS_PER_BATCH}-record limit"
        )
    if not raw_records and not raw_deletions:
        raise BatchValidationError("batch must contain records or deletions")

    records = tuple(
        _parse_record(item, index) for index, item in enumerate(raw_records)
    )
    deletions = tuple(
        _parse_deletion(item, index) for index, item in enumerate(raw_deletions)
    )
    record_ids = [record.record_id for record in records]
    deletion_ids = [deletion.record_id for deletion in deletions]
    if len(record_ids) != len(set(record_ids)):
        raise BatchValidationError("records contains duplicate recordId values")
    if len(deletion_ids) != len(set(deletion_ids)):
        raise BatchValidationError("deletions contains duplicate recordId values")

    normalized = {
        "schemaVersion": 1,
        "batchId": batch_id,
        "deviceId": device_id,
        "generatedAt": generated_at,
        "records": [record.normalized for record in records],
        "deletions": [deletion.normalized for deletion in deletions],
    }
    return Batch(
        schema_version=1,
        batch_id=batch_id,
        device_id=device_id,
        generated_at=generated_at,
        records=records,
        deletions=deletions,
        normalized=normalized,
    )
