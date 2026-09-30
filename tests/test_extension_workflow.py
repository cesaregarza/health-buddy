"""Synthetic extension-event workflow durability and authority tests.

Source-only candidate: parent integrates and the dedicated queue runs it.
"""

from __future__ import annotations

import base64
import json
from dataclasses import replace

import pytest

from health_buddy.client_workflow import ClientWorkflow, WorkflowNamespace
from health_buddy.domain import digest, encode, envelope
from health_buddy.security_api import AgentGrant, BearerProof
from health_buddy.security_runtime import open_runtime
from health_buddy.service_api import Identity, Principal, Request, ServiceError
from health_buddy.workspace import initialize
from tests.security_fixtures import action, secured

IDENTITY = Identity(
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
)
EXTENSION = "synthetic.connector"


class SyntheticOperations:
    """Small deterministic operation seam with commit-before-lost-ack behavior."""

    def __init__(self):
        self.revision = 0
        self.requests = []
        self.receipts = {}
        self.lose_once = False

    def preflight(self, principal, operation):
        return None

    def execute(self, principal, request):
        if request.operation == "capabilities":
            return envelope({"writable": True}, IDENTITY, self.revision)
        self.requests.append(replace(request, deadline=None))
        if request.idempotency_key not in self.receipts:
            self.revision += 1
            self.receipts[request.idempotency_key] = envelope(
                {"saved": True}, IDENTITY, self.revision
            )
        if self.lose_once:
            self.lose_once = False
            raise TimeoutError("synthetic committed reply was lost")
        return self.receipts[request.idempotency_key]


def namespace(event_id):
    return WorkflowNamespace(EXTENSION, event_id)


def state_path(config, event_id):
    return config.path(
        f"personal/extensions/{EXTENSION}/state/requests/"
        + digest({"eventId": event_id}) + ".json"
    )


def make_workflow(config, operations, event_id):
    return ClientWorkflow(
        config, operations, Principal("synthetic-handle"), namespace=namespace(event_id)
    )


def emit(workflow, value="same"):
    payload = {"sourceId": "synthetic", "fields": {"value": value}}
    return workflow.write(
        "logs.write",
        lambda: payload,
        intent=["synthetic-event", value],
        resource_id="intake",
    )


def load_state(config, event_id):
    return json.loads(state_path(config, event_id).read_bytes())


def test_events_are_isolated_and_never_touch_native_client_state(tmp_path):
    config = initialize(tmp_path / "owner")
    (config.path(f"personal/extensions/{EXTENSION}")).mkdir(mode=0o700, parents=True)
    operations = SyntheticOperations()
    first = make_workflow(config, operations, "event-one")
    second = make_workflow(config, operations, "event-two")

    emit(first, "one")
    emit(second, "two")
    first_state, second_state = (
        load_state(config, "event-one"),
        load_state(config, "event-two"),
    )

    assert first_state["state"] == second_state["state"] == "complete"
    assert first_state["cursor"] == second_state["cursor"] == 1
    assert first_state["envelope"] != second_state["envelope"]
    assert first_state["receipt"] != second_state["receipt"]
    assert not config.path("personal/state/native-client.json").exists()
    assert operations.revision == 2
    second_bytes = state_path(config, "event-two").read_bytes()
    first_bytes = state_path(config, "event-one").read_bytes()
    emit(first, "one")
    assert state_path(config, "event-one").read_bytes() == first_bytes
    assert state_path(config, "event-two").read_bytes() == second_bytes
    assert operations.revision == 2


def test_lost_ack_reopen_reuses_exact_envelope_and_one_canonical_receipt(tmp_path):
    config = initialize(tmp_path / "owner")
    config.path(f"personal/extensions/{EXTENSION}").mkdir(mode=0o700, parents=True)
    operations = SyntheticOperations()
    operations.lose_once = True
    first = make_workflow(config, operations, "lost-ack")
    with pytest.raises(ServiceError, match="outcome_unknown"):
        emit(first)
    pending = load_state(config, "lost-ack")
    assert pending["state"] == "pending" and pending["cursor"] == 0
    assert pending["receipt"] is None

    reopened = make_workflow(config, operations, "lost-ack")
    result = emit(reopened)
    complete = load_state(config, "lost-ack")
    assert complete["envelope"] == pending["envelope"]
    assert operations.requests[0] == operations.requests[1]
    assert complete["state"] == "complete" and complete["cursor"] == 1
    assert base64.b64decode(complete["receipt"]["bodyBase64"]) == encode(result)
    assert operations.revision == 1
    assert reopened.retry() == result
    assert load_state(config, "lost-ack")["cursor"] == 1
    assert operations.revision == 1


def test_completed_event_rejects_changed_intent_and_cannot_be_discarded(tmp_path):
    config = initialize(tmp_path / "owner")
    config.path(f"personal/extensions/{EXTENSION}").mkdir(mode=0o700, parents=True)
    operations = SyntheticOperations()
    workflow = make_workflow(config, operations, "immutable-event")
    emit(workflow, "original")
    path = state_path(config, "immutable-event")
    before = path.read_bytes()
    with pytest.raises(ServiceError, match="source_event_conflict"):
        emit(workflow, "changed")
    with pytest.raises(ServiceError, match="source_event_requires_reconciliation"):
        workflow.discard(acknowledge_possible_save=True)
    assert path.read_bytes() == before
    assert operations.revision == 1 and len(operations.requests) == 1


def test_real_authority_rotation_recovers_lost_ack_for_same_actor(
    tmp_path, monkeypatch
):
    """Canonical write commits once; revocation denies retry until token rotation."""
    from health_buddy.app import App
    from health_buddy.client_workflow import decoded

    root = tmp_path / "secured-owner"
    runtime, owner, owner_token = secured(root)
    grant = action(
        runtime,
        owner,
        "grants.create",
        payload=AgentGrant(
            "Fabricated extension agent",
            ("records:write",),
            source_ids=("manual",),
            read_sources=(),
            read_kinds=(),
            read_fields=(),
        ),
    )
    original_token = grant.secret.value
    app = App.authenticated(root, proof=BearerProof(original_token), runtime=runtime)
    binding = app.operations.describe()
    (app.config.path(f"personal/extensions/{EXTENSION}")).mkdir(
        mode=0o700, parents=True
    )
    event = "real-authority-event"
    app.workflow = ClientWorkflow(
        app.config,
        app.operations,
        app.principal,
        client_identity=app.operations.describe,
        namespace=namespace(event),
    )
    arguments = [
        "--event-at-local",
        "2030-01-01T08:00:00+00:00",
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
    saved_replies, sent_requests = [], []
    base_revision = runtime.operations.journal.state().revision
    execute = runtime.operations.execute

    def lose_ack(principal, request, _execute=execute):
        reply = _execute(principal, request)
        if request.operation == "logs.write":
            saved_replies.append(reply)
            sent_requests.append(replace(request, deadline=None))
            raise TimeoutError("synthetic committed reply was lost")
        return reply

    with monkeypatch.context() as patch:
        patch.setattr(runtime.operations, "execute", lose_ack)
        with pytest.raises(ServiceError, match="outcome_unknown"):
            app.log_record("intake", arguments)
    path = state_path(app.config, event)
    pending_bytes = path.read_bytes()
    pending = json.loads(pending_bytes)
    first_revision = runtime.operations.journal.state().revision
    assert pending["state"] == "pending" and pending["cursor"] == 0
    assert first_revision == base_revision + 1

    rotated = action(runtime, owner, "grants.rotate", resource=grant.data["id"])
    with pytest.raises(ServiceError) as denied:
        app.workflow.retry()
    assert denied.value.status == 401
    assert path.read_bytes() == pending_bytes
    del app, runtime, execute, lose_ack

    reopened = open_runtime(root)
    with pytest.raises(ServiceError) as denied:
        App.authenticated(root, proof=BearerProof(original_token), runtime=reopened)
    assert denied.value.status == 401
    after = App.authenticated(
        root, proof=BearerProof(rotated.secret.value), runtime=reopened
    )
    assert after.operations.describe() == binding
    after.workflow = ClientWorkflow(
        after.config,
        after.operations,
        after.principal,
        client_identity=after.operations.describe,
        namespace=namespace(event),
    )
    replay_replies, replay_requests = [], []
    execute_reopened = reopened.operations.execute

    def observe_replay(principal, request):
        reply = execute_reopened(principal, request)
        if request.operation == "logs.write":
            replay_replies.append(reply)
            replay_requests.append(replace(request, deadline=None))
        return reply

    with monkeypatch.context() as patch:
        patch.setattr(reopened.operations, "execute", observe_replay)
        after.workflow.retry()
    complete = json.loads(path.read_bytes())
    assert complete["envelope"] == pending["envelope"]
    assert complete["clientIdentity"] == pending["clientIdentity"]
    assert complete["state"] == "complete" and complete["cursor"] == 1
    assert sent_requests == replay_requests and len(sent_requests) == 1
    assert saved_replies[0].body == replay_replies[0].body
    assert base64.b64decode(complete["receipt"]["bodyBase64"]) == saved_replies[0].body
    assert reopened.operations.journal.state().revision == first_revision
    audit = App.authenticated(root, proof=BearerProof(owner_token), runtime=reopened)
    records = decoded(
        audit.operations.execute(
            audit.principal,
            Request(
                "records.list",
                query={
                    "kinds": "intake",
                    "from": "2030-01-01T00:00:00Z",
                    "to": "2030-01-02T00:00:00Z",
                },
            ),
        )
    )
    assert len(records["data"]["records"]) == 1


def test_replay_marker_is_not_retained_and_success_clears_prior_error(tmp_path):
    config = initialize(tmp_path / "owner")
    config.path(f"personal/extensions/{EXTENSION}").mkdir(mode=0o700, parents=True)
    operations = SyntheticOperations()
    workflow = make_workflow(config, operations, "receipt-marker")
    result = emit(workflow)
    path = state_path(config, "receipt-marker")
    original = path.read_bytes()
    execute = operations.execute
    denied = False

    def with_marker(principal, request):
        if denied:
            raise ServiceError(503, "source_unavailable", retryable=True)
        response = execute(principal, request)
        return replace(response, headers=(
            *response.headers, ("iDeMpOtEnCy-RePlAyEd", "true")
        ))

    operations.execute = with_marker
    assert workflow.retry() == result
    assert path.read_bytes() == original
    denied = True
    with pytest.raises(ServiceError, match="source_unavailable"):
        workflow.retry()
    failed = json.loads(path.read_bytes())
    assert failed["state"] == "complete" and failed["cursor"] == 1
    assert failed["lastError"]["code"] == "source_unavailable"
    denied = False
    assert workflow.retry() == result
    assert path.read_bytes() == original
    assert operations.revision == 1
