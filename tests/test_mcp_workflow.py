"""Standalone retry storage with synthetic model and actual authority seams.

These tests do not qualify MCP SDK framing or a remote HTTP client.
"""

import json
import os
from dataclasses import replace

import pytest

from health_buddy.client.auth import AuthenticatedOperations
from health_buddy.client.retry_paths import RetryRoot
from health_buddy.client.workflow import ClientWorkflow, McpWorkflowNamespace
from health_buddy.core.domain import digest
from health_buddy.core.security_api import AgentGrant, BearerProof, ClientIdentity
from health_buddy.core.service_api import Principal, ServiceError
from health_buddy.security.runtime import open_runtime
from tests.security_fixtures import action, secured
from tests.test_extension_workflow import IDENTITY, SyntheticOperations, emit


def state_path(root, intent="action-1", client="synthetic-client"):
    return root.path(
        "profiles/"
        + digest({"clientId": client})
        + "/requests/"
        + digest({"intentId": intent})
        + ".json"
    )


def workflow(
    root, operations, *, binding=None, intent="action-1", client="synthetic-client"
):
    return ClientWorkflow(
        root,
        operations,
        Principal("synthetic-admission"),
        client_identity=binding
        or (lambda: ClientIdentity("actor-one", "epoch-one", IDENTITY)),
        namespace=McpWorkflowNamespace(client, intent),
    )


def test_standalone_root_retains_exact_lost_ack_and_conflicts(tmp_path):
    root = RetryRoot.create(tmp_path / "adapter")
    operations = SyntheticOperations()
    operations.lose_once = True
    with pytest.raises(ServiceError, match="outcome_unknown"):
        emit(workflow(root, operations))
    pending = json.loads(state_path(root).read_bytes())
    assert pending["state"] == "pending" and pending["cursor"] == 0
    reopened = workflow(RetryRoot(root.root), operations)
    result = reopened.retry()
    complete = json.loads(state_path(root).read_bytes())
    assert complete["envelope"] == pending["envelope"]
    assert complete["clientIdentity"] == pending["clientIdentity"]
    assert complete["state"] == "complete" and complete["cursor"] == 1
    assert operations.requests[0] == operations.requests[1]
    assert reopened.retry() == result and operations.revision == 1
    before = state_path(root).read_bytes()
    with pytest.raises(ServiceError, match="mcp_intent_conflict"):
        emit(reopened, "different")
    with pytest.raises(ServiceError, match="mcp_intent_requires_reconciliation"):
        reopened.discard(acknowledge_possible_save=True)
    assert state_path(root).read_bytes() == before
    assert {path.name for path in root.root.iterdir()} == {"profiles", "state.lock"}
    assert not (root.root / "config.json").exists()


@pytest.mark.parametrize("change", ["actor", "security_epoch", "receiver"])
def test_status_retry_and_changed_intent_refuse_other_binding(tmp_path, change):
    root = RetryRoot.create(tmp_path / "adapter")
    operations = SyntheticOperations()
    current = ClientIdentity("actor-one", "epoch-one", IDENTITY)
    client = workflow(root, operations, binding=lambda: current)
    emit(client)
    before = state_path(root).read_bytes()
    if change == "actor":
        current = replace(current, actor_binding="actor-two")
    elif change == "security_epoch":
        current = replace(current, security_epoch="epoch-two")
    else:
        current = replace(
            current,
            identity=replace(
                IDENTITY, restore_epoch="00000000-0000-4000-8000-000000000004"
            ),
        )
    for command in (client.inspect, client.retry, lambda: emit(client, "different")):
        with pytest.raises(ServiceError, match="client_identity_changed"):
            command()
    assert state_path(root).read_bytes() == before
    assert len(operations.requests) == 1


def test_copied_intent_and_corrupt_bytes_are_retained_without_send(tmp_path):
    root = RetryRoot.create(tmp_path / "adapter")
    operations = SyntheticOperations()
    emit(workflow(root, operations))
    other = workflow(root, operations, intent="action-2")
    assert other.inspect()["state"] == "empty"
    destination = state_path(root, "action-2")
    # Fabricate a retained reservation so this probes copied-state validation,
    # not the separate missing-lock corruption refusal.
    destination.with_suffix(".lock").touch(mode=0o600)
    destination.write_bytes(state_path(root).read_bytes())
    destination.chmod(0o600)
    for raw in (destination.read_bytes(), b"{corrupt"):
        destination.write_bytes(raw)
        with pytest.raises(ServiceError, match="client_state_unavailable"):
            other.retry()
        assert destination.read_bytes() == raw
    assert len(operations.requests) == 1


def test_private_paths_reject_symlinks_hardlinks_and_broad_permissions(tmp_path):
    root = RetryRoot.create(tmp_path / "adapter")
    private = root.root / "private"
    private.write_bytes(b"synthetic")
    private.chmod(0o600)
    (root.root / "link").symlink_to(private)
    with pytest.raises(ServiceError):
        root.path("link")
    os.link(private, root.root / "hardlink")
    with pytest.raises(ServiceError):
        root.path("hardlink")
    root.root.chmod(0o755)
    with pytest.raises(ServiceError):
        RetryRoot(root.root)
    for relative in ("../escape", "/absolute", "a//b", "a/./b"):
        with pytest.raises(ServiceError):
            root.path(relative)


def test_boolean_namespace_version_is_retained_and_refused_without_send(tmp_path):
    root = RetryRoot.create(tmp_path / "adapter")
    operations = SyntheticOperations()
    client = workflow(root, operations)
    emit(client)
    state = json.loads(state_path(root).read_bytes())
    assert type(state["intentNamespace"]["schemaVersion"]) is int
    # Every other persisted field is the valid, already completed action.
    # Python dictionary equality alone considers this True equal to integer 1.
    state["intentNamespace"]["schemaVersion"] = True
    corrupted = json.dumps(state).encode()
    state_path(root).write_bytes(corrupted)
    for command in (client.inspect, client.retry, lambda: emit(client)):
        with pytest.raises(ServiceError, match="client_state_unavailable"):
            command()
        assert state_path(root).read_bytes() == corrupted
    assert len(operations.requests) == 1


def test_capacity_refuses_new_intents_without_evicting_completed_receipts(tmp_path):
    root = RetryRoot.create(tmp_path / "adapter")
    operations = SyntheticOperations()
    client = workflow(root, operations)
    emit(client)
    before = state_path(root).read_bytes()
    for index in range(1023):
        path = state_path(root, "reserved-" + str(index)).with_suffix(".lock")
        path.touch(mode=0o600)
    with pytest.raises(ServiceError, match="mcp_intent_capacity"):
        emit(workflow(root, operations, intent="new-intent"))
    client.retry()
    assert state_path(root).read_bytes() == before
    assert operations.revision == 1


def test_directory_barrier_failure_retries_existing_ancestors_before_send(
    tmp_path, monkeypatch
):
    from health_buddy.client import workflow as client_workflow

    root = RetryRoot.create(tmp_path / "adapter")
    operations = SyntheticOperations()
    real = client_workflow.fsync_path
    visited = []
    failed = False

    def barrier(path):
        nonlocal failed
        visited.append(path)
        if path.name == "requests" and not failed:
            failed = True
            raise OSError("fabricated directory barrier failure")
        real(path)

    monkeypatch.setattr(client_workflow, "fsync_path", barrier)
    with pytest.raises(OSError):
        emit(workflow(root, operations))
    assert not operations.requests
    visited.clear()
    emit(workflow(root, operations))
    directory = state_path(root).parent
    assert directory in visited and directory.parent in visited
    assert root.root / "profiles" in visited and root.root in visited
    assert operations.revision == 1


def test_real_authority_rotation_and_reopen_preserve_separate_retry_root(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "health_buddy.core.operations._now", lambda: "2026-10-02T02:44:00Z"
    )
    backend = tmp_path / "backend"
    runtime, owner, _ = secured(backend)
    grant = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant(
            "Fabricated MCP actor",
            ("records:write",),
            source_ids=("manual",),
        ),
    )
    proof = BearerProof(grant.secret.value)
    operations = AuthenticatedOperations(runtime, proof)
    root = RetryRoot.create(tmp_path / "adapter")
    client = workflow(root, operations, binding=operations.describe)
    payload = {
        "sourceId": "manual",
        "fields": {
            "measuredAtLocal": "2030-01-01T08:00:00+00:00",
            "timezone": "UTC",
            "weightLb": "160",
        },
    }
    execute = runtime.operations.execute
    sent = []

    def lost(principal, request):
        reply = execute(principal, request)
        if request.operation == "logs.write":
            sent.append((replace(request, deadline=None), reply))
            raise TimeoutError("fabricated lost acknowledgement")
        return reply

    with monkeypatch.context() as patch:
        patch.setattr(runtime.operations, "execute", lost)
        with pytest.raises(ServiceError, match="outcome_unknown"):
            client.write(
                "logs.write", lambda: payload, intent=payload, resource_id="measurement"
            )
    pending = state_path(root).read_bytes()
    (warning,) = json.loads(sent[0][1].body)["data"]["warnings"]
    assert warning["code"] == "future_measurement_timestamp"
    assert warning["observedAt"] == "2030-01-01T08:00:00Z"
    monkeypatch.setattr(
        "health_buddy.core.operations._now", lambda: "2030-01-02T02:44:00Z"
    )
    rotated = action(runtime, owner, "grants.rotate", resource=grant.data["id"])
    with pytest.raises(ServiceError) as denied:
        client.retry()
    assert denied.value.status == 401 and state_path(root).read_bytes() == pending
    reopened = open_runtime(backend)
    after = AuthenticatedOperations(reopened, BearerProof(rotated.secret.value))
    result = workflow(RetryRoot(root.root), after, binding=after.describe).retry()
    complete = json.loads(state_path(root).read_bytes())
    assert complete["envelope"] == json.loads(pending)["envelope"]
    assert result == json.loads(sent[0][1].body)
    assert (
        reopened.operations.journal.state().revision == result["meta"]["dataRevision"]
    )
    assert set(path.name for path in root.root.iterdir()) == {"profiles", "state.lock"}


def test_unknown_status_and_retry_do_not_consume_write_capacity(tmp_path):
    root = RetryRoot.create(tmp_path / "adapter")
    operations = SyntheticOperations()
    for index in range(1025):
        client = workflow(root, operations, intent=f"missing-{index}")
        assert client.inspect() == {"state": "empty", "cursor": 0}
        with pytest.raises(ServiceError, match="no_pending_write"):
            client.retry()
    assert {path.name for path in root.root.iterdir()} == {"state.lock"}
    assert operations.requests == []
    operations.lose_once = True
    client = workflow(root, operations)
    with pytest.raises(ServiceError, match="outcome_unknown"):
        emit(client)
    pending = state_path(root).read_bytes()
    assert client.inspect()["state"] == "pending"
    assert state_path(root).read_bytes() == pending
    assert len(list(state_path(root).parent.iterdir())) == 2
    client.retry()
    assert client.inspect()["state"] == "complete"
    assert operations.revision == 1


def test_status_lookup_serializes_existing_writer_without_reserving_absent_id(
    tmp_path,
):
    import threading

    root = RetryRoot.create(tmp_path / "adapter")
    operations = SyntheticOperations()
    client = workflow(root, operations)
    entered, release = threading.Event(), threading.Event()
    looking, finished = threading.Event(), threading.Event()
    original = operations.execute
    errors, statuses = [], []

    def delayed(principal, request):
        if request.operation == "logs.write":
            entered.set()
            if not release.wait(5):
                raise RuntimeError("synthetic writer release timeout")
        return original(principal, request)

    def write():
        try:
            emit(client)
        except Exception as error:
            errors.append(error)

    def inspect():
        looking.set()
        try:
            statuses.append(client.inspect())
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()

    operations.execute = delayed
    writer = threading.Thread(target=write, daemon=True)
    reader = threading.Thread(target=inspect, daemon=True)
    writer.start()
    try:
        assert entered.wait(5)
        pending = json.loads(state_path(root).read_bytes())
        assert pending["state"] == "pending"
        reader.start()
        assert looking.wait(5) and not finished.wait(0.05)
        assert workflow(root, operations, intent="absent").inspect() == {
            "state": "empty",
            "cursor": 0,
        }
        assert not state_path(root, "absent").with_suffix(".lock").exists()
    finally:
        release.set()
        writer.join(5)
        if reader.ident is not None:
            reader.join(5)
    assert not writer.is_alive() and not reader.is_alive() and not errors
    assert statuses[0]["state"] == "complete" and statuses[0]["cursor"] == 1
    assert json.loads(state_path(root).read_bytes())["envelope"] == pending["envelope"]
    assert operations.revision == 1
