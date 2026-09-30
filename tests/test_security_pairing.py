"""Owner-mediated single-use enrollment; every phone and proof is synthetic."""

import json
import sqlite3
import time
from dataclasses import replace
from uuid import uuid4

import pytest

from health_buddy.security_api import (
    AgentGrant,
    BearerProof,
    PairingRedemption,
    PairingReservation,
    SecurityRequest,
)
from health_buddy.service_api import Request, ServiceError
from tests.canonical_fixtures import decoded
from tests.security_fixtures import action, secured
from tests.test_health_ingest_models import batch_payload


def enroll(runtime, owner, *, device=None, predecessor=None):
    reserve = action(
        runtime,
        owner,
        "pairing.create",
        payload=PairingReservation("Synthetic phone", predecessor),
    )
    handoff = action(runtime, owner, "pairing.handoff", resource=reserve.data["id"])
    device = device or str(uuid4())
    request = SecurityRequest(
        "pairing.redeem",
        payload=PairingRedemption(handoff.secret.value, device),
        identity=runtime.operations.journal.state().identity,
    )
    return runtime.security.execute(None, request), request, reserve, handoff


def test_safe_reservation_private_handoff_upload_only_and_current_revoke(tmp_path):
    runtime, owner, _ = secured(tmp_path / "owner", receiver=True)
    receipt, request, reserve, handoff = enroll(runtime, owner)
    assert reserve.data["status"] == "awaiting_owner" and reserve.secret is None
    assert "proof" not in json.dumps(reserve.data)
    assert handoff.data["status"] == "ready" and handoff.secret.kind == "pairing-proof"
    assert handoff.data["protocolVersion"] == 1
    assert handoff.secret.value not in handoff.data["approvalPath"]
    assert 299 <= handoff.data["expiresAt"] - time.time() <= 300
    assert receipt.secret.kind == "device-token"
    phone = runtime.security.authenticate(BearerProof(receipt.secret.value))
    capabilities = decoded(
        runtime.operations.execute(phone.principal, Request("capabilities"))
    )
    assert capabilities["data"]["writable"] is False
    for operation in (
        "dashboard.read",
        "context.read",
        "plan.read",
        "records.list",
        "plan.write",
        "context.intent",
        "asset.read",
    ):
        response = runtime.operations.execute(phone.principal, Request(operation))
        assert response.status == 403, (operation, response.body)
    assert action(runtime, phone, "session.get").secret is None
    with pytest.raises(ServiceError) as manage:
        action(runtime, phone, "devices.list")
    assert manage.value.status == 403
    body = batch_payload(receipt.data["deviceId"])
    upload = Request(
        "healthkit.ingest",
        payload=body,
        identity=request.identity,
        health_device_id=body["deviceId"],
    )
    first = runtime.operations.execute(phone.principal, upload)
    assert first.status == 200, first.body
    replay = runtime.operations.execute(phone.principal, upload)
    assert replay.status == 200 and decoded(replay)["duplicateBatch"] is True
    revision = runtime.operations.journal.state().revision
    action(runtime, owner, "devices.revoke", resource=receipt.data["id"])
    assert runtime.operations.execute(phone.principal, upload).status == 401
    assert runtime.operations.journal.state().revision == revision
    status = action(runtime, owner, "pairing.status", resource=reserve.data["id"])
    assert status.data["status"] == "consumed" and status.secret is None
    with pytest.raises(ServiceError) as used:
        runtime.security.execute(None, request)
    assert used.value.status == 409


def test_same_device_repair_requires_owner_selected_predecessor_and_keeps_stream(
    tmp_path,
):
    runtime, owner, _ = secured(tmp_path / "owner", receiver=True)
    first, _, _, _ = enroll(runtime, owner)
    original = runtime.security.authenticate(BearerProof(first.secret.value))
    with pytest.raises(ServiceError) as stolen:
        enroll(runtime, owner, device=first.data["deviceId"])
    assert stolen.value.code == "reconciliation_required"
    with pytest.raises(ServiceError) as replacement:
        enroll(runtime, owner, predecessor=first.data["id"], device=str(uuid4()))
    assert replacement.value.code == "reconciliation_required"
    before = runtime.operations.journal.state().revision
    second, _, _, _ = enroll(
        runtime, owner, predecessor=first.data["id"], device=first.data["deviceId"]
    )
    assert first.data == second.data
    assert runtime.operations.journal.state().revision == before
    with pytest.raises(ServiceError):
        runtime.security.describe(original.principal)
    rotated = runtime.security.authenticate(BearerProof(second.secret.value))
    assert rotated.client == original.client


def test_pairing_tuple_protocol_and_expiry_are_checked_before_consumption(
    tmp_path, monkeypatch
):
    runtime, owner, _ = secured(tmp_path / "owner", receiver=True)
    reservation = action(
        runtime, owner, "pairing.create", payload=PairingReservation("Synthetic")
    )
    handoff = action(runtime, owner, "pairing.handoff", resource=reservation.data["id"])
    request = SecurityRequest(
        "pairing.redeem",
        payload=PairingRedemption(handoff.secret.value, str(uuid4())),
        identity=runtime.operations.journal.state().identity,
    )
    for bad in (
        replace(
            request, identity=replace(request.identity, restore_epoch=str(uuid4()))
        ),
        replace(request, payload=replace(request.payload, protocol_version=2)),
    ):
        with pytest.raises(ServiceError):
            runtime.security.execute(None, bad)
    assert (
        action(runtime, owner, "pairing.status", resource=reservation.data["id"]).data[
            "status"
        ]
        == "ready"
    )
    now = time.time()
    monkeypatch.setattr("health_buddy.security_store.time.time", lambda: now + 301)
    with pytest.raises(ServiceError) as expired:
        runtime.security.execute(None, request)
    assert expired.value.status == 410
    assert (
        action(runtime, owner, "pairing.status", resource=reservation.data["id"]).data[
            "status"
        ]
        == "expired"
    )
    assert runtime.operations.journal.state().revision == 0


def test_agents_cannot_prepare_handoff_redeem_by_guess_or_manage_devices(tmp_path):
    runtime, owner, _ = secured(tmp_path / "owner", receiver=True)
    reply = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant("Synthetic agent", ("records:read",)),
    )
    agent = runtime.security.authenticate(BearerProof(reply.secret.value))
    for operation in (
        "pairing.create",
        "pairing.status",
        "pairing.handoff",
        "devices.list",
        "devices.revoke",
        "grants.list",
        "session.create",
    ):
        with pytest.raises(ServiceError) as rejected:
            action(runtime, agent, operation)
        assert rejected.value.status == 403
    with pytest.raises(ServiceError) as guessed:
        runtime.security.execute(
            None,
            SecurityRequest(
                "pairing.redeem",
                payload=PairingRedemption("x" * 43, str(uuid4())),
                identity=runtime.operations.journal.state().identity,
            ),
        )
    assert guessed.value.status == 401


def test_reservations_are_bounded_and_cleanup_expired_proofs_without_secret_replay(
    tmp_path, monkeypatch
):
    runtime, owner, _ = secured(tmp_path / "owner", receiver=True)
    initial = time.time()
    # Keep separate admission windows while reaching the explicit row bound.
    for offset in range(64):
        monkeypatch.setattr(
            "health_buddy.security_store.time.time",
            lambda offset=offset: initial + offset,
        )
        action(
            runtime, owner, "pairing.create", payload=PairingReservation("Synthetic")
        )
    with pytest.raises(ServiceError) as full:
        action(
            runtime, owner, "pairing.create", payload=PairingReservation("Synthetic")
        )
    assert full.value.code == "pairing_limit"
    monkeypatch.setattr("health_buddy.security_store.time.time", lambda: initial + 1000)
    action(runtime, owner, "pairing.create", payload=PairingReservation("After expiry"))
    with sqlite3.connect(tmp_path / "owner/security/authority.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM pairing").fetchone()[0] == 1


def test_plaintext_pairing_proof_and_device_token_never_persist(tmp_path):
    root = tmp_path / "owner"
    runtime, owner, _ = secured(root, receiver=True)
    receipt, _, _, handoff = enroll(runtime, owner)
    values = (receipt.secret.value, handoff.secret.value)
    for path in (
        root / "security/authority.sqlite",
        root / "operations/control.sqlite",
        root / "stores/healthkit.db",
    ):
        assert all(value.encode() not in path.read_bytes() for value in values)


def test_device_revocation_invalidates_preexisting_repair_proof(tmp_path):
    runtime, owner, _ = secured(tmp_path / "owner", receiver=True)
    original, _, _, _ = enroll(runtime, owner)
    reservation = action(
        runtime,
        owner,
        "pairing.create",
        payload=PairingReservation(
            "Replacement",
            original.data["id"],
        ),
    )
    handoff = action(runtime, owner, "pairing.handoff", resource=reservation.data["id"])
    awaiting = action(
        runtime,
        owner,
        "pairing.create",
        payload=PairingReservation(
            "Awaiting replacement",
            original.data["id"],
        ),
    )
    action(runtime, owner, "devices.revoke", resource=original.data["id"])
    with pytest.raises(ServiceError) as old_proof:
        runtime.security.execute(
            None,
            SecurityRequest(
                "pairing.redeem",
                payload=PairingRedemption(
                    handoff.secret.value, original.data["deviceId"]
                ),
                identity=runtime.operations.journal.state().identity,
            ),
        )
    assert old_proof.value.status == 401
    assert (
        action(runtime, owner, "pairing.status", resource=reservation.data["id"]).data[
            "status"
        ]
        == "revoked"
    )
    assert (
        action(runtime, owner, "pairing.status", resource=awaiting.data["id"]).data[
            "status"
        ]
        == "revoked"
    )
    with pytest.raises(ServiceError) as awaiting_handoff:
        action(runtime, owner, "pairing.handoff", resource=awaiting.data["id"])
    assert awaiting_handoff.value.status == 409
    with pytest.raises(ServiceError):
        runtime.security.authenticate(BearerProof(original.secret.value))
    fresh, _, _, _ = enroll(
        runtime,
        owner,
        device=original.data["deviceId"],
        predecessor=original.data["id"],
    )
    assert fresh.data == original.data
    assert (
        runtime.security.authenticate(
            BearerProof(fresh.secret.value)
        ).client.actor_binding
        == original.data["id"]
    )
