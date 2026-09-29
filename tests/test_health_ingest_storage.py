from __future__ import annotations

import csv
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from health_ingest.models import parse_batch
from health_ingest.storage import (
    AuthenticationError,
    BatchConflictError,
    HealthRepository,
)
from tests.test_health_ingest_models import batch_payload


@pytest.fixture
def repository(tmp_path: Path) -> HealthRepository:
    result = HealthRepository(tmp_path / "health.db")
    result.migrate()
    return result


def test_device_token_authentication_and_revocation(
    repository: HealthRepository,
) -> None:
    device_id = str(uuid4())
    token = repository.issue_device(device_id, "health-buddy's iPhone")

    repository.authenticate(device_id, token)
    with pytest.raises(AuthenticationError):
        repository.authenticate(device_id, "incorrect-token")

    assert repository.revoke_device(device_id)
    with pytest.raises(AuthenticationError):
        repository.authenticate(device_id, token)


def test_batch_replay_is_idempotent_and_conflicts_are_atomic(
    repository: HealthRepository,
) -> None:
    device_id = str(uuid4())
    repository.issue_device(device_id, "phone")
    payload = batch_payload(device_id)

    first = repository.ingest(parse_batch(payload))
    replay = repository.ingest(parse_batch(payload))

    assert first.records_accepted == 1
    assert not first.duplicate_batch
    assert replay.duplicate_batch
    assert replay.records_accepted == 0
    assert repository.device_status(device_id)["recordCount"] == 1

    conflict = deepcopy(payload)
    conflict["records"][0]["value"] = 9999
    with pytest.raises(BatchConflictError):
        repository.ingest(parse_batch(conflict))
    assert repository.device_status(device_id)["recordCount"] == 1


def test_tombstone_survives_sample_arriving_later(repository: HealthRepository) -> None:
    device_id = str(uuid4())
    repository.issue_device(device_id, "phone")
    sample = batch_payload(device_id)
    record = sample["records"][0]
    deletion = {
        "schemaVersion": 1,
        "batchId": str(uuid4()),
        "deviceId": device_id,
        "generatedAt": "2026-08-29T11:00:00-05:00",
        "records": [],
        "deletions": [
            {
                "recordId": record["recordId"],
                "typeIdentifier": "HKQuantityTypeIdentifierHeartRate",
                "observedAt": "2026-08-29T11:00:00-05:00",
            }
        ],
    }
    # Daily aggregates are not deletable. Use a matching discrete sample.
    sample["records"][0] = {
        "recordId": record["recordId"],
        "recordKind": "quantity",
        "typeIdentifier": "HKQuantityTypeIdentifierHeartRate",
        "startDate": "2026-08-29T10:59:00-05:00",
        "endDate": "2026-08-29T10:59:00-05:00",
        "creationDate": "2026-08-29T11:00:00-05:00",
        "localDate": None,
        "timezone": None,
        "value": 87,
        "unit": "count/min",
        "source": None,
        "device": None,
        "workout": None,
    }

    repository.ingest(parse_batch(deletion))
    repository.ingest(parse_batch(sample))

    assert repository.device_status(device_id)["recordCount"] == 0


def test_daily_export_is_deterministic(
    repository: HealthRepository, tmp_path: Path
) -> None:
    device_id = str(uuid4())
    repository.issue_device(device_id, "phone")
    repository.ingest(parse_batch(batch_payload(device_id)))
    output = tmp_path / "daily.csv"

    assert repository.export_daily(output) == 1
    first = output.read_bytes()
    assert repository.export_daily(output) == 1
    assert output.read_bytes() == first

    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["value"] == "4123"
    assert rows[0]["source_name"] == "Health"


def test_daily_aggregate_can_be_replaced_as_day_changes(
    repository: HealthRepository,
) -> None:
    device_id = str(uuid4())
    repository.issue_device(device_id, "phone")
    first = batch_payload(device_id)
    repository.ingest(parse_batch(first))

    updated = deepcopy(first)
    updated["batchId"] = str(uuid4())
    updated["records"][0]["value"] = 5000
    repository.ingest(parse_batch(updated))

    assert repository.device_status(device_id)["recordCount"] == 1


def test_discrete_healthkit_record_is_immutable(
    repository: HealthRepository,
) -> None:
    device_id = str(uuid4())
    repository.issue_device(device_id, "phone")
    first = batch_payload(device_id)
    first["records"][0] = {
        "recordId": str(uuid4()),
        "recordKind": "quantity",
        "typeIdentifier": "HKQuantityTypeIdentifierHeartRate",
        "startDate": "2026-08-29T10:59:00-05:00",
        "endDate": "2026-08-29T10:59:00-05:00",
        "creationDate": "2026-08-29T11:00:00-05:00",
        "localDate": None,
        "timezone": None,
        "value": 87,
        "unit": "count/min",
        "source": None,
        "device": None,
        "workout": None,
    }
    repository.ingest(parse_batch(first))

    changed = deepcopy(first)
    changed["batchId"] = str(uuid4())
    changed["records"][0]["value"] = 120
    with pytest.raises(BatchConflictError, match="immutable"):
        repository.ingest(parse_batch(changed))
