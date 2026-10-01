"""Stable authenticated actor/epoch ownership of exact native pending bytes."""

import json
from dataclasses import replace

import pytest

from health_buddy.client_workflow import ClientWorkflow
from health_buddy.core.security_api import ClientIdentity
from health_buddy.core.service_api import Principal, ServiceError
from health_buddy.core.workspace import initialize
from tests.test_client_workflow import IDENTITY, ScriptedOperations, write


def test_rotated_handle_same_authenticated_actor_retries_original(tmp_path):
    config = initialize(tmp_path / "owner")
    operations = ScriptedOperations(config)
    operations.lose_once = True
    identity = ClientIdentity("actor-one", "epoch-one", IDENTITY)
    before = ClientWorkflow(
        config, operations, Principal("old-handle"), client_identity=lambda: identity
    )
    with pytest.raises(ServiceError, match="outcome_unknown"):
        write(before)
    path = config.path("personal/state/native-client.json")
    original = json.loads(path.read_bytes())
    after = ClientWorkflow(
        config, operations, Principal("new-handle"), client_identity=lambda: identity
    )
    after.retry()
    saved = json.loads(path.read_bytes())
    assert saved["schemaVersion"] == 2 and "principalBinding" not in saved
    assert saved["envelope"] == original["envelope"]
    assert operations.requests[0] == operations.requests[1]
    assert operations.revision == 1 and saved["cursor"] == 1


@pytest.mark.parametrize("change", ["actor", "epoch", "receiver"])
def test_changed_authenticated_binding_preserves_pending_bytes(tmp_path, change):
    config = initialize(tmp_path / "owner")
    operations = ScriptedOperations(config)
    operations.lose_once = True
    identity = ClientIdentity("actor-one", "epoch-one", IDENTITY)
    before = ClientWorkflow(
        config, operations, Principal("handle"), client_identity=lambda: identity
    )
    with pytest.raises(ServiceError):
        write(before)
    path = config.path("personal/state/native-client.json")
    original = path.read_bytes()
    new = (
        replace(identity, actor_binding="actor-two")
        if change == "actor"
        else replace(identity, security_epoch="epoch-two")
        if change == "epoch"
        else replace(
            identity,
            identity=replace(
                IDENTITY, restore_epoch="00000000-0000-4000-8000-000000000009"
            ),
        )
    )
    after = ClientWorkflow(
        config, operations, Principal("new-handle"), client_identity=lambda: new
    )
    with pytest.raises(ServiceError, match="client_identity_changed"):
        after.retry()
    assert path.read_bytes() == original and len(operations.requests) == 1


def test_legacy_pending_is_preserved_until_acknowledged_private_archive(tmp_path):
    config = initialize(tmp_path / "owner")
    operations = ScriptedOperations(config)
    operations.lose_once = True
    old = ClientWorkflow(config, operations, Principal("development-handle"))
    with pytest.raises(ServiceError):
        write(old)
    path = config.path("personal/state/native-client.json")
    original = path.read_bytes()
    new = ClientWorkflow(
        config,
        operations,
        Principal("production-handle"),
        client_identity=lambda: ClientIdentity("actor-one", "epoch-one", IDENTITY),
    )
    with pytest.raises(ServiceError, match="legacy_client_state_requires_resolution"):
        new.retry()
    with pytest.raises(ServiceError, match="acknowledgement_required"):
        new.discard()
    assert path.read_bytes() == original
    assert new.discard(acknowledge_possible_save=True)["retained"] is True
    assert not path.exists()
    archives = list(path.parent.glob("native-client.resolved-*.json"))
    assert len(archives) == 1 and archives[0].read_bytes() == original
    assert archives[0].stat().st_mode & 0o077 == 0


def test_corrupt_resolution_fsync_failure_never_claims_durable_success(
    tmp_path, monkeypatch
):
    config = initialize(tmp_path / "owner")
    operations = ScriptedOperations(config)
    workflow = ClientWorkflow(
        config,
        operations,
        Principal("handle"),
        client_identity=lambda: ClientIdentity("actor", "epoch", IDENTITY),
    )
    directory = config.path("personal/state")
    directory.mkdir(mode=0o700)
    path = directory / "native-client.json"
    original = b'{"unfinished":'
    path.write_bytes(original)
    path.chmod(0o600)

    def fail_sync(_path):
        raise OSError("synthetic sync failure")

    monkeypatch.setattr("health_buddy.client_workflow.fsync_path", fail_sync)
    with pytest.raises(ServiceError, match="client_resolution_outcome_unknown"):
        workflow.discard(acknowledge_possible_save=True)
    archives = list(directory.glob("native-client.resolved-*.json"))
    assert len(archives) == 1 and archives[0].read_bytes() == original
    restarted = ClientWorkflow(
        config,
        operations,
        Principal("new"),
        client_identity=lambda: ClientIdentity("actor", "epoch", IDENTITY),
    )
    assert restarted.inspect()["state"] == "empty"
    assert archives[0].read_bytes() == original


def test_authenticated_wrapper_rechecks_revoked_proof_before_each_operation(tmp_path):
    from health_buddy.app import App
    from health_buddy.core.security_api import BearerProof, Runtime
    from tests.auth_transport_fixtures import TOKEN, FakeSecurity, fake_runtime

    config = initialize(tmp_path / "owner")
    service = ScriptedOperations(config)
    security = FakeSecurity()
    runtime = Runtime(service, security, fake_runtime().ingress)
    app = App.authenticated(config.root, proof=BearerProof(TOKEN), runtime=runtime)
    write(app.workflow)
    original = config.path("personal/state/native-client.json").read_bytes()

    def denied(_proof):
        raise ServiceError(401, "unauthenticated")

    security.authenticate = denied
    with pytest.raises(ServiceError, match="unauthenticated"):
        app.workflow.retry()
    assert config.path("personal/state/native-client.json").read_bytes() == original
    assert len(service.requests) == 1


def test_real_authority_lost_ack_reopen_and_same_actor_rotation(tmp_path, monkeypatch):
    """Actual authority/canonical commit; only reply delivery is fault injected."""
    import base64
    from datetime import UTC, datetime

    from health_buddy.app import App
    from health_buddy.client_workflow import decoded
    from health_buddy.core.security_api import AgentGrant, BearerProof
    from health_buddy.security_runtime import open_runtime
    from health_buddy.core.service_api import Request
    from tests.security_fixtures import action, secured

    root = tmp_path / "owner"
    runtime, owner, _owner_token = secured(root)
    grant = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant(
            "Fabricated native agent",
            ("records:read", "records:write"),
            source_ids=("manual",),
            read_sources=("manual",),
            read_kinds=None,
            read_fields=None,
        ),
    )
    original_token = grant.secret.value
    app = App.authenticated(root, proof=BearerProof(original_token), runtime=runtime)
    binding = app.operations.describe()
    first_revision = runtime.operations.journal.state().revision
    replies, requests = [], []
    execute = runtime.operations.execute

    def lose_ack(principal, request, execute=execute):
        reply = execute(principal, request)
        if request.operation == "logs.write":
            assert reply.status == 200
            replies.append(reply)
            requests.append(replace(request, deadline=None))
            raise TimeoutError("Fabricated lost delivery after canonical commit")
        return reply

    arguments = [
        "--event-at-local",
        datetime.now(UTC).isoformat(timespec="seconds"),
        "--timezone",
        "UTC",
        "--item-name",
        "Fabricated retry oats",
        "--calories-kcal",
        "123",
        "--status",
        "consumed",
        "--category",
        "meal",
        "--source",
        "synthetic-entry",
    ]
    with monkeypatch.context() as patch:
        patch.setattr(runtime.operations, "execute", lose_ack)
        with pytest.raises(ServiceError, match="outcome_unknown"):
            app.log_record("intake", arguments)
    state_path = app.config.path("personal/state/native-client.json")
    pending_bytes = state_path.read_bytes()
    pending = json.loads(pending_bytes)
    assert pending["state"] == "pending" and pending["cursor"] == 0
    assert runtime.operations.journal.state().revision == first_revision + 1
    rotated = action(runtime, owner, "grants.rotate", resource=grant.data["id"])
    with pytest.raises(ServiceError) as denied:
        app.workflow.retry()
    assert denied.value.status == 401
    assert state_path.read_bytes() == pending_bytes
    del app, runtime, execute, lose_ack  # Per-operation connections are already closed.

    reopened = open_runtime(root)
    with pytest.raises(ServiceError) as denied:
        App.authenticated(root, proof=BearerProof(original_token), runtime=reopened)
    assert denied.value.status == 401
    after = App.authenticated(
        root, proof=BearerProof(rotated.secret.value), runtime=reopened
    )
    assert after.operations.describe() == binding
    execute_reopened = reopened.operations.execute

    def observe_replay(principal, request):
        reply = execute_reopened(principal, request)
        if request.operation == "logs.write":
            requests.append(replace(request, deadline=None))
            replies.append(reply)
        return reply

    with monkeypatch.context() as patch:
        patch.setattr(reopened.operations, "execute", observe_replay)
        after.workflow.retry()
    complete = json.loads(state_path.read_bytes())
    assert complete["envelope"] == pending["envelope"]
    assert complete["clientIdentity"] == pending["clientIdentity"]
    assert complete["state"] == "complete" and complete["cursor"] == 1
    assert requests[0] == requests[1]
    assert replies[0].status == replies[1].status == 200
    assert replies[0].body == replies[1].body
    assert base64.b64decode(complete["receipt"]["bodyBase64"]) == replies[0].body
    assert reopened.operations.journal.state().revision == first_revision + 1
    records = decoded(
        after.operations.execute(
            after.principal,
            Request(
                "records.list",
                query={"kinds": "intake"},
            ),
        )
    )
    assert len(records["data"]["records"]) == 1
