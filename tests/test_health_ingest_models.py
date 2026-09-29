from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

import pytest

from health_ingest.models import BatchValidationError, parse_batch


def batch_payload(device_id: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "batchId": str(uuid4()),
        "deviceId": device_id or str(uuid4()),
        "generatedAt": "2026-08-29T10:00:00-05:00",
        "records": [
            {
                "recordId": "daily:steps:2026-08-29:America-Chicago",
                "recordKind": "dailyAggregate",
                "typeIdentifier": "HKQuantityTypeIdentifierStepCount",
                "startDate": "2026-08-29T00:00:00-05:00",
                "endDate": "2026-08-30T00:00:00-05:00",
                "creationDate": "2026-08-29T10:00:00-05:00",
                "localDate": "2026-08-29",
                "timezone": "America/Chicago",
                "value": 4123,
                "unit": "count",
                "source": {
                    "bundleIdentifier": "com.apple.Health",
                    "name": "Health",
                },
                "device": None,
                "workout": None,
            }
        ],
        "deletions": [],
    }


def test_parse_valid_daily_batch() -> None:
    parsed = parse_batch(batch_payload())

    assert parsed.schema_version == 1
    assert parsed.records[0].value == 4123
    assert parsed.records[0].local_date == "2026-08-29"


@pytest.mark.parametrize("field", ["metadata", "route", "location"])
def test_rejects_unallowlisted_record_fields(field: str) -> None:
    payload = batch_payload()
    payload["records"][0][field] = {"sensitive": True}

    with pytest.raises(BatchValidationError, match="unsupported fields"):
        parse_batch(payload)


def test_rejects_unallowlisted_health_type() -> None:
    payload = batch_payload()
    payload["records"][0]["typeIdentifier"] = "HKClinicalTypeIdentifierAllergyRecord"

    with pytest.raises(BatchValidationError, match="not allowlisted"):
        parse_batch(payload)


def test_rejects_mismatched_record_kind() -> None:
    payload = batch_payload()
    payload["records"][0]["recordKind"] = "quantity"

    with pytest.raises(BatchValidationError, match="must be dailyAggregate"):
        parse_batch(payload)


def test_rejects_empty_batch() -> None:
    payload = batch_payload()
    payload["records"] = []

    with pytest.raises(BatchValidationError, match="must contain"):
        parse_batch(payload)


def test_normalization_is_stable() -> None:
    payload = batch_payload()
    reordered = deepcopy(payload)
    reordered["records"][0] = dict(reversed(reordered["records"][0].items()))

    assert parse_batch(payload).normalized == parse_batch(reordered).normalized
