"""Stable authenticated actor/epoch ownership of exact native pending bytes."""
from dataclasses import replace
import json

import pytest

from health_buddy.client_workflow import ClientWorkflow
from health_buddy.security_api import ClientIdentity
from health_buddy.service_api import Principal, ServiceError
from health_buddy.workspace import initialize
from tests.test_client_workflow import IDENTITY, ScriptedOperations, write


def test_rotated_handle_same_authenticated_actor_retries_original(tmp_path):
    config = initialize(tmp_path / "owner")
    operations = ScriptedOperations(config)
    operations.lose_once = True
    identity = ClientIdentity("actor-one", "epoch-one", IDENTITY)
    before = ClientWorkflow(config, operations, Principal("old-handle"), client_identity=lambda: identity)
    with pytest.raises(ServiceError, match="outcome_unknown"):
        write(before)
    path = config.path("personal/state/native-client.json")
    original = json.loads(path.read_bytes())
    after = ClientWorkflow(config, operations, Principal("new-handle"), client_identity=lambda: identity)
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
    before = ClientWorkflow(config, operations, Principal("handle"), client_identity=lambda: identity)
    with pytest.raises(ServiceError):
        write(before)
    path = config.path("personal/state/native-client.json")
    original = path.read_bytes()
    new = replace(identity, actor_binding="actor-two") if change == "actor" else replace(identity, security_epoch="epoch-two") if change == "epoch" else replace(identity, identity=replace(IDENTITY, restore_epoch="00000000-0000-4000-8000-000000000009"))
    after = ClientWorkflow(config, operations, Principal("new-handle"), client_identity=lambda: new)
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
    new = ClientWorkflow(config, operations, Principal("production-handle"), client_identity=lambda: ClientIdentity("actor-one", "epoch-one", IDENTITY))
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


def test_corrupt_resolution_fsync_failure_never_claims_durable_success(tmp_path, monkeypatch):
    config = initialize(tmp_path / "owner")
    operations = ScriptedOperations(config)
    workflow = ClientWorkflow(config, operations, Principal("handle"), client_identity=lambda: ClientIdentity("actor", "epoch", IDENTITY))
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
    restarted = ClientWorkflow(config, operations, Principal("new"), client_identity=lambda: ClientIdentity("actor", "epoch", IDENTITY))
    assert restarted.inspect()["state"] == "empty"
    assert archives[0].read_bytes() == original


def test_authenticated_wrapper_rechecks_revoked_proof_before_each_operation(tmp_path):
    from health_buddy.app import App
    from health_buddy.security_api import BearerProof, Runtime
    from tests.auth_transport_fixtures import FakeSecurity, TOKEN, fake_runtime

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
