"""Durable admission, current revocation and purpose-separated secret handling."""

import json
import sqlite3
from dataclasses import replace

import pytest

from health_buddy.security_api import (
    AgentGrant, BearerProof, BootstrapProof, ProxyProof, SecurityRequest, SessionProof,
)
from health_buddy.security_runtime import open_runtime, read_credential, setup_security
from health_buddy.service_api import Principal, Request, ServiceError
from tests.canonical_fixtures import decoded, intent
from tests.security_fixtures import action, secured


def test_no_automatic_authority_and_guessed_credential_ids_fail_closed(tmp_path):
    root = tmp_path / "owner"
    runtime = open_runtime(root)
    for handle in (None, Principal("local-development-owner"), Principal("guessed")):
        response = runtime.operations.execute(handle, Request("capabilities"))
        assert response.status in {401, 503}
        assert decoded(response)["meta"] == {}
    assert not (root / "security/authority.sqlite").exists()
    runtime, owner, _ = secured(tmp_path / "configured")
    with sqlite3.connect(tmp_path / "configured/security/authority.sqlite") as database:
        credential_id = database.execute("SELECT id FROM credentials WHERE kind='owner'").fetchone()[0]
    response = runtime.operations.execute(Principal(credential_id), Request("capabilities"))
    assert response.status == 401 and decoded(response)["meta"] == {}
    assert runtime.operations.execute(owner.principal, Request("capabilities")).status == 200


def test_bootstrap_consumes_once_checks_tuple_and_expires(tmp_path, monkeypatch):
    root = tmp_path / "owner"
    runtime = open_runtime(root)
    output = root / "secrets/bootstrap"
    setup_security(root, output)
    proof = BootstrapProof(json.loads(output.read_text())["proof"])
    identity = runtime.operations.journal.state().identity
    with pytest.raises(ServiceError) as wrong:
        runtime.security.execute(None, SecurityRequest(
            "bootstrap.redeem", proof=proof,
            identity=replace(identity, restore_epoch="wrong"),
        ))
    assert wrong.value.status == 409
    reply = runtime.security.execute(None, SecurityRequest("bootstrap.redeem", proof=proof, identity=identity))
    assert reply.secret.kind == "owner-token" and reply.cookie is None
    with pytest.raises(ServiceError) as replay:
        runtime.security.execute(None, SecurityRequest("bootstrap.redeem", proof=proof, identity=identity))
    assert replay.value.status == 401
    expired_root = tmp_path / "expired"
    expired = open_runtime(expired_root)
    output = expired_root / "secrets/bootstrap"
    setup_security(expired_root, output)
    proof = BootstrapProof(json.loads(output.read_text())["proof"])
    import time
    now = time.time()
    monkeypatch.setattr("health_buddy.security_store.time.time", lambda: now + 301)
    with pytest.raises(ServiceError) as expired_error:
        expired.security.execute(None, SecurityRequest(
            "bootstrap.redeem", proof=proof, identity=expired.operations.journal.state().identity,
        ))
    assert expired_error.value.status == 401


def test_scoped_agent_current_revoke_precedes_exact_health_replay(tmp_path):
    runtime, owner, _ = secured(tmp_path / "owner")
    grant = action(runtime, owner, "grants.create", payload=AgentGrant(
        "Synthetic writer", ("records:read", "records:write"), ("manual",),
        ("manual",), None, ("id", "value", "unit"),
    ))
    agent = runtime.security.authenticate(BearerProof(grant.secret.value))
    write = intent(runtime.operations, owner.principal)
    original = runtime.operations.execute(agent.principal, write)
    assert original.status == 200
    listing = runtime.operations.execute(agent.principal, Request("records.list"))
    assert set(decoded(listing)["data"]["records"][0]) == {"id", "value", "unit"}
    inventory = action(runtime, owner, "grants.list").data["items"][0]
    assert inventory["readSources"] == ["manual"]
    assert inventory["readKinds"] is None
    assert inventory["readFields"] == ["id", "value", "unit"]
    assert runtime.operations.execute(agent.principal, Request("dashboard.read")).status == 403
    assert runtime.operations.execute(agent.principal, Request("context.intent", payload={})).status == 403
    revision = runtime.operations.journal.state().revision
    action(runtime, owner, "grants.revoke", resource=grant.data["id"])
    response = runtime.operations.execute(agent.principal, write)
    assert response.status == 401 and decoded(response)["meta"] == {}
    assert runtime.operations.journal.state().revision == revision
    with pytest.raises(ServiceError):
        runtime.security.authenticate(BearerProof(grant.secret.value))


@pytest.mark.parametrize("grants", [("devices:manage",), ("operations:admin",), ("extensions:manage",), ("healthkit:ingest",)])
def test_agent_cannot_gain_maintenance_or_device_role(tmp_path, grants):
    runtime, owner, _ = secured(tmp_path / "owner")
    with pytest.raises(ServiceError) as result:
        action(runtime, owner, "grants.create", payload=AgentGrant("Synthetic", grants))
    assert result.value.status == 403


def test_rotation_retains_actor_but_revokes_prior_proof_and_handles(tmp_path):
    root = tmp_path / "owner"
    runtime, owner, _ = secured(root)
    grant = action(runtime, owner, "grants.create", payload=AgentGrant("Synthetic", ("records:read",), read_sources=None, read_kinds=None, read_fields=None))
    first = runtime.security.authenticate(BearerProof(grant.secret.value))
    rotated = action(runtime, owner, "grants.rotate", resource=grant.data["id"])
    with pytest.raises(ServiceError):
        runtime.security.describe(first.principal)
    reopened = open_runtime(root)
    second = reopened.security.authenticate(BearerProof(rotated.secret.value))
    assert first.client == second.client
    assert reopened.operations.execute(first.principal, Request("capabilities")).status == 401


def test_session_csrf_is_per_presentation_and_logout_does_not_restore_owner(tmp_path):
    runtime, owner, _ = secured(tmp_path / "owner")
    session = action(runtime, owner, "session.create")
    token = session.cookie.value
    ordinary = runtime.security.authenticate(SessionProof(token))
    assert not ordinary.csrf_verified
    read = action(runtime, ordinary, "session.get")
    assert read.secret.kind == "csrf"
    verified = runtime.security.authenticate(SessionProof(token, read.secret.value))
    assert verified.csrf_verified
    missing = runtime.security.authenticate(SessionProof(token))
    incorrect = runtime.security.authenticate(SessionProof(token, "0" * 64))
    assert verified.principal == missing.principal == incorrect.principal
    assert not missing.csrf_verified and not incorrect.csrf_verified
    assert action(runtime, owner, "session.get").secret is None
    action(runtime, verified, "session.revoke")
    assert runtime.operations.execute(verified.principal, Request("dashboard.read")).status == 401
    with pytest.raises(ServiceError):
        runtime.security.authenticate(SessionProof(token))


def test_proxy_capability_requires_runtime_instance_config_and_exact_subject(tmp_path):
    runtime, _, _ = secured(tmp_path / "owner", proxy=True)
    subject = runtime.ingress.owner_subject
    for proof in (ProxyProof(subject, object()), ProxyProof("another@example.invalid", runtime.proxy_boundary)):
        with pytest.raises(ServiceError):
            runtime.security.authenticate(proof)
    admitted = runtime.security.authenticate(ProxyProof(subject, runtime.proxy_boundary))
    assert runtime.operations.execute(admitted.principal, Request("capabilities")).status == 403
    assert action(runtime, admitted, "session.create").cookie.action == "issue"
    direct, _, _ = secured(tmp_path / "direct")
    with pytest.raises(ServiceError):
        direct.security.authenticate(ProxyProof(subject, object()))


@pytest.mark.parametrize("missing", ["security/authority.sqlite", "security/epoch.json", "operations/security-binding.json"])
def test_lost_metadata_never_reinitializes_and_explicit_recovery_revokes_all(tmp_path, missing):
    root = tmp_path / "owner"
    runtime, owner, owner_token = secured(root)
    before = runtime.operations.journal.state()
    (root / missing).unlink()
    assert runtime.operations.execute(owner.principal, Request("capabilities")).status == 503
    with pytest.raises(ServiceError):
        setup_security(root, root / "secrets/rebootstrap")
    output = root / "secrets/recovered-owner"
    with pytest.raises(ServiceError):
        setup_security(root, output, recover=True)
    setup_security(root, output, recover=True, confirm_revoke_all=True)
    recovered = open_runtime(root)
    fresh = recovered.security.authenticate(BearerProof(read_credential(output)))
    assert fresh.client.security_epoch != owner.client.security_epoch
    assert recovered.operations.journal.state() == before
    with pytest.raises(ServiceError):
        recovered.security.authenticate(BearerProof(owner_token))
    assert recovered.operations.execute(fresh.principal, Request("capabilities")).status == 200


def test_raw_security_health_files_and_reprs_do_not_contain_delivered_tokens(tmp_path):
    root = tmp_path / "owner"
    runtime, owner, token = secured(root)
    agent = action(runtime, owner, "grants.create", payload=AgentGrant("Synthetic", ("records:read",)))
    session = action(runtime, owner, "session.create")
    secrets = [token, agent.secret.value, session.cookie.value]
    for path in (root / "security/authority.sqlite", root / "operations/control.sqlite", root / "security/epoch.json"):
        raw = path.read_bytes()
        assert all(value.encode() not in raw for value in secrets)
    for value in secrets:
        assert value not in repr(agent) + repr(session) + repr(BearerProof(value))
    with runtime.operations.backup(owner.principal) as inventory:
        assert {root / "security/authority.sqlite", root / "security/epoch.json", root / "operations/security-binding.json"} <= set(inventory.required_paths)


def test_session_expiry_rechecks_cached_handle(tmp_path, monkeypatch):
    runtime, owner, _ = secured(tmp_path / "owner")
    session = action(runtime, owner, "session.create")
    admitted = runtime.security.authenticate(SessionProof(session.cookie.value))
    import time
    now = time.time()
    monkeypatch.setattr("health_buddy.security_store.time.time", lambda: now + runtime.ingress.session_seconds + 1)
    assert runtime.operations.execute(admitted.principal, Request("capabilities")).status == 401
    with pytest.raises(ServiceError):
        runtime.security.authenticate(SessionProof(session.cookie.value))


def test_failed_authentication_budget_is_durable_global_and_bounded(tmp_path):
    root = tmp_path / "owner"
    runtime, _, _ = secured(root)
    exhausted = False
    for _ in range(245):
        with pytest.raises(ServiceError) as rejection:
            runtime.security.authenticate(BearerProof("x" * 43))
        assert rejection.value.status in {401, 429}
        if rejection.value.status == 429:
            exhausted = True
            break
    assert exhausted
    reopened = open_runtime(root)
    with pytest.raises(ServiceError) as persistent:
        reopened.security.authenticate(BearerProof("y" * 43))
    assert persistent.value.status == 429
    with sqlite3.connect(root / "security/authority.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM budgets").fetchone()[0] <= 3
        assert connection.execute("SELECT count(*) FROM credentials").fetchone()[0] == 2


def test_handles_deduplicate_expire_and_never_become_database_authority(tmp_path, monkeypatch):
    runtime, owner, token = secured(tmp_path / "owner")
    for _ in range(50):
        assert runtime.security.authenticate(BearerProof(token)).principal == owner.principal
    assert len(runtime.security.handles) == 1
    # Exercise the isolated bounded-cache helper without fabricating hundreds
    # of durable actors or disguising its unit coverage as authentication.
    for index in range(280):
        runtime.security._handle("nonexistent-" + str(index), owner.client.security_epoch, "bearer")
    assert len(runtime.security.handles) == 256
    forged = Principal(next(iter(runtime.security.handles)))
    assert runtime.operations.execute(forged, Request("capabilities")).status == 401
    fresh = runtime.security.authenticate(BearerProof(token))
    import time
    now = time.monotonic()
    monkeypatch.setattr("health_buddy.security.time.monotonic", lambda: now + 301)
    with pytest.raises(ServiceError):
        runtime.security.describe(fresh.principal)
    renewed = runtime.security.authenticate(BearerProof(token))
    assert renewed.principal != fresh.principal and renewed.client == fresh.client
