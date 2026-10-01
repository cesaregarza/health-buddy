"""Bootstrap, recovery inventory and persisted-bound proof with synthetic data."""

import json
import multiprocessing
import os
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from health_buddy.core import operations
from health_buddy.core.domain import MAX_HEALTH_BODY, MAX_MANIFEST, MAX_RESPONSE, encode
from health_buddy.core.operations import Service
from health_buddy.core.service_api import Request, Response, ServiceError
from health_buddy.core.workspace import initialize
from health_ingest.models import DEVICE_KEYS, SOURCE_KEYS, parse_batch
from tests.canonical_fixtures import (
    RegisteredPolicy,
    intent,
    receiver_principal,
    setup,
)
from tests.test_canonical_recovery import _cleanup, _crash, _joined, _reopen


def _initialize_crash(root, point):
    def fault(actual):
        if actual == point:
            os._exit(87)

    Service(Path(root), fault=fault)
    os._exit(89)


@pytest.mark.parametrize(
    "point",
    [
        "bootstrap_objects",
        "bootstrap_decision",
        "bootstrap_identity",
        "receiver_staged",
        "receiver_installed",
    ],
)
def test_first_adoption_hard_exit_can_reopen_without_resetting_owner_files(
    tmp_path, point
):
    root = tmp_path / "owner"
    initialize(root)
    personal = root / "personal" / "preserve.txt"
    personal.write_text("Fabricated owner customization\n")
    if point.startswith("receiver_"):
        values = json.loads((root / "config.json").read_text())
        values["integrations"]["healthkit"] = {"enabled": True, "mode": "receiver"}
        (root / "config.json").write_text(json.dumps(values))
    context = multiprocessing.get_context("spawn")
    child = context.Process(target=_initialize_crash, args=(str(root), point))
    try:
        child.start()
        _joined(child, 87)
    finally:
        _cleanup([child], [])
    policy = RegisteredPolicy()
    owner = policy.issue()
    service = Service(root, policy)
    assert service.journal.verify().revision == 0
    identity = service.journal.state().identity
    assert Service(root, policy).journal.state().identity == identity
    assert personal.read_text() == "Fabricated owner customization\n"
    if point.startswith("receiver_"):
        service.check_receiver(identity)
    assert service.execute(owner, intent(service, owner)).status == 200
    assert service.journal.state().revision == 1


def test_missing_adoption_ledger_never_remints_identity(tmp_path):
    root = tmp_path / "owner"
    service, _policy, _owner = setup(root)
    original = service.journal.identity_path.read_bytes()
    service.journal.path.rename(service.journal.path.with_suffix(".preserved"))
    with pytest.raises(ServiceError):
        Service(root)
    assert service.journal.identity_path.read_bytes() == original
    assert not service.journal.path.exists()


def test_pending_missing_receiver_blocks_backup_and_fresh_write_until_restored(
    tmp_path,
):
    root = tmp_path / "owner"
    service, policy, owner = setup(root, receiver=True)
    phone, request = receiver_principal(service, policy, owner)
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=_crash,
        args=(
            str(root),
            phone,
            policy.handles[phone.credential_id],
            request,
            "git_installed",
        ),
    )
    try:
        child.start()
        _joined(child, 87)
    finally:
        _cleanup([child], [])
    preserved = service.health.path.with_suffix(".preserved")
    service.health.path.rename(preserved)
    with pytest.raises(ServiceError):
        with service.backup(owner):
            pytest.fail("decided receiver effect is absent from backup")
    blocked = service.execute(
        owner,
        Request(
            "records.put",
            resource_id="not-admitted",
            payload={
                "kind": "body-mass",
                "value": 80,
                "unit": "kg",
                "observedAt": datetime.now(UTC).isoformat(),
                "sourceId": "manual",
            },
            identity=service.journal.state().identity,
            if_match='"rev-1"',
            idempotency_key="blocked-write",
        ),
    )
    assert blocked.status == 503
    preserved.rename(service.health.path)
    reopened = Service(root, policy)
    assert reopened.execute(phone, request).status == 200
    with reopened.backup(owner) as inventory:
        assert reopened.health.path in inventory.required_paths
        assert inventory.data_revision == 2


def maximal_batch(request):
    stamp = datetime.now(UTC).isoformat()
    base = request.payload["records"][0]
    # Maximum record/deletion counts and full source/device/workout metadata.
    # Long invented labels exercise >1MiB durable manifests; no private values.
    record = {
        **base,
        "recordKind": "workout",
        "typeIdentifier": "HKWorkoutTypeIdentifier",
        "startDate": stamp,
        "endDate": stamp,
        "creationDate": stamp,
        "localDate": stamp[:10],
        "timezone": "UTC",
        "value": None,
        "unit": None,
        "source": {key: "Synthetic-" + "s" * 245 for key in SOURCE_KEYS},
        "device": {key: "Synthetic-" + "d" * 245 for key in DEVICE_KEYS},
        "workout": {
            "activityType": "37",
            "durationSeconds": 30,
            "totalEnergyValue": 10,
            "totalEnergyUnit": "kcal",
            "totalDistanceValue": 1,
            "totalDistanceUnit": "km",
        },
    }
    return {
        **request.payload,
        "batchId": str(uuid4()),
        "generatedAt": stamp,
        "records": [
            {**record, "recordId": f"synthetic-workout-{index}"} for index in range(500)
        ],
        "deletions": [
            {
                "recordId": f"synthetic-absent-{index}",
                "typeIdentifier": "HKWorkoutTypeIdentifier",
                "observedAt": stamp,
            }
            for index in range(500)
        ],
    }


def test_maximal_schema_manifest_over_megabyte_recovers_after_hard_exit(
    tmp_path, record_property
):
    root = tmp_path / "owner"
    service, policy, owner = setup(root, receiver=True)
    phone, request = receiver_principal(service, policy, owner)
    body = maximal_batch(request)
    assert len(parse_batch(body).records) == len(parse_batch(body).deletions) == 500
    assert 1_048_576 < len(encode(body)) < MAX_HEALTH_BODY
    record_property("accepted_request_bytes", len(encode(body)))
    request = replace(request, payload=body)
    context = multiprocessing.get_context("spawn")
    child = context.Process(
        target=_crash,
        args=(
            str(root),
            phone,
            policy.handles[phone.credential_id],
            request,
            "commit_intent",
        ),
    )
    try:
        child.start()
        _joined(child, 87)
    finally:
        _cleanup([child], [])
    with sqlite3.connect(service.journal.path) as connection:
        length = connection.execute(
            "SELECT length(manifest) FROM transactions WHERE state='COMMIT_INTENT'"
        ).fetchone()[0]
    assert 1_048_576 < length < MAX_MANIFEST
    record_property("durable_manifest_bytes", length)
    result = _reopen(context, root, phone, policy.handles[phone.credential_id], request)
    assert result["status"] == 200 and result["healthCount"] == 500
    receipt = json.loads(result["body"])
    assert (
        receipt["duplicateBatch"] is True
        and receipt["recordsAccepted"] == receipt["deletionsAccepted"] == 500
    )
    again = _reopen(context, root, phone, policy.handles[phone.credential_id], request)
    assert again["body"] == result["body"] and again["after"] == result["after"]


def test_oversized_request_and_response_cannot_make_durable_decision(
    tmp_path, monkeypatch
):
    service, policy, owner = setup(tmp_path / "owner", receiver=True)
    phone, request = receiver_principal(service, policy, owner)
    body = deepcopy(request.payload)
    body["unsupportedPadding"] = ["x" * 10_000 for _ in range(500)]
    before = service.journal.state(), service.manual.snapshot()
    response = service.execute(phone, replace(request, payload=body))
    assert (
        response.status == 413
        and (service.journal.state(), service.manual.snapshot()) == before
    )
    ordinary = intent(service, owner)
    monkeypatch.setattr(
        operations,
        "envelope",
        lambda *_args, **_kwargs: Response(200, b"x" * (MAX_RESPONSE + 1)),
    )
    response = service.execute(owner, ordinary)
    assert response.status == 413
    assert (service.journal.state(), service.manual.snapshot()) == before
    with sqlite3.connect(service.journal.path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM transactions "
                "WHERE state IN ('PREPARED','COMMIT_INTENT')"
            ).fetchone()[0]
            == 0
        )


def test_tombstone_id_is_stable_and_removed_record_never_reappears(tmp_path):
    service, policy, owner = setup(tmp_path / "owner", receiver=True)
    phone, request = receiver_principal(service, policy, owner)
    stamp = datetime.now(UTC).isoformat()
    aggregate_deletion = {
        **request.payload,
        "records": [],
        "deletions": [
            {
                "recordId": request.payload["records"][0]["recordId"],
                "typeIdentifier": request.payload["records"][0]["typeIdentifier"],
                "observedAt": stamp,
            }
        ],
    }
    before = service.journal.state(), service.manual.snapshot()
    assert (
        service.execute(phone, replace(request, payload=aggregate_deletion)).status
        == 422
    )
    assert (service.journal.state(), service.manual.snapshot()) == before
    # Daily aggregates intentionally do not accept tombstones. Exercise deletion
    # with an explicit synthetic quantity, retaining the original batch for retry.
    record = {
        **request.payload["records"][0],
        "recordId": "synthetic-deletable-mass",
        "recordKind": "quantity",
        "typeIdentifier": "HKQuantityTypeIdentifierBodyMass",
        "startDate": stamp,
        "endDate": stamp,
        "creationDate": stamp,
        "localDate": stamp[:10],
        "timezone": "UTC",
        "value": 80,
        "unit": "kg",
    }
    request = replace(
        request,
        payload={**request.payload, "generatedAt": stamp, "records": [record]},
    )
    assert service.execute(phone, request).status == 200
    row = service.health.records()[0]
    record_id = row["observation_id"]
    deletion = {
        "recordId": request.payload["records"][0]["recordId"],
        "typeIdentifier": request.payload["records"][0]["typeIdentifier"],
        "observedAt": datetime.now(UTC).isoformat(),
    }
    body = {
        **request.payload,
        "batchId": str(uuid4()),
        "records": [],
        "deletions": [deletion],
    }
    assert service.execute(phone, replace(request, payload=body)).status == 200
    assert (
        service.execute(owner, Request("records.get", resource_id=record_id)).status
        == 404
    )
    after_deletion = service.journal.state()
    assert service.execute(phone, request).status == 200
    assert service.journal.state() == after_deletion
    assert service.health.records() == []
    with sqlite3.connect(service.health.path) as connection:
        assert (
            connection.execute("SELECT observation_id FROM stream_objects").fetchone()[
                0
            ]
            == record_id
        )


def test_backup_context_excludes_concurrent_canonical_writer(tmp_path):
    from tests.test_canonical_recovery import _race

    root = tmp_path / "owner"
    service, policy, owner = setup(root)
    request = intent(service, owner)
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_race,
        args=(
            str(root),
            owner,
            policy.handles[owner.credential_id],
            request,
            gate,
            child,
        ),
    )
    try:
        process.start()
        child.close()
        assert parent.poll(30) and parent.recv()[0] == "ready"
        with service.backup(owner) as inventory:
            gate.set()
            assert not parent.poll(0.2), (
                "writer entered during consistent backup context"
            )
            assert inventory.data_revision == service.journal.state().revision == 0
        assert parent.poll(30)
        response = parent.recv()
        _joined(process, 0)
        assert response[0] == 200
        assert service.journal.state().revision == 1
    finally:
        gate.set()
        _cleanup([process], [parent, child])
