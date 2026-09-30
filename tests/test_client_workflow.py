"""Private client retry lifecycle, using synthetic receipts and durable files.

The scripted server models lost replies and current policy; canonical server
atomicity is covered separately by its journal integration tests.
"""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from health_buddy.app import App
from health_buddy.client_workflow import ClientWorkflow
from health_buddy.domain import encode, envelope
from health_buddy.service_api import Identity, Principal, Response, ServiceError
from health_buddy.workspace import initialize
from tests.synthetic_workspace import program

IDENTITY = Identity(
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
)
PRINCIPAL = Principal("synthetic-fixed-handle")


class ScriptedOperations:
    def __init__(self, config):
        self.config = config
        self.requests = []
        self.receipts = {}
        self.revision = 0
        self.lose_once = False
        self.allowed = True
        self.mutate = lambda result: result
        self.failure = None

    def preflight(self, principal, operation):
        return None

    def execute(self, principal, request):
        if not self.allowed:
            return Response(403, encode({"error": {"code": "forbidden"}, "meta": {}}))
        if request.operation == "capabilities":
            return envelope({"writable": True}, IDENTITY, self.revision)
        pending = json.loads(
            self.config.path("personal/state/native-client.json").read_text()
        )
        assert pending["envelope"]["idempotencyKey"] == request.idempotency_key
        assert pending["envelope"]["payload"] == request.payload
        self.requests.append(replace(request, deadline=None))
        if self.failure:
            return Response(409, encode({"error": {"code": self.failure}, "meta": {}}))
        if request.idempotency_key not in self.receipts:
            self.revision += 1
            data = {"saved": True, "projection": {"state": "pending"}}
            if request.operation == "workouts.write":
                data["sessionId"] = request.payload["session_id"]
            elif request.operation == "plan.write":
                data["planId"] = request.payload["programId"]
            elif request.operation == "records.put":
                data["recordId"] = request.resource_id
            self.receipts[request.idempotency_key] = envelope(
                data, IDENTITY, self.revision
            )
        if self.lose_once:
            self.lose_once = False
            raise TimeoutError("synthetic response lost after commit")
        return self.mutate(self.receipts[request.idempotency_key])


@pytest.fixture
def fixture(tmp_path):
    config = initialize(tmp_path / "owner")
    service = ScriptedOperations(config)
    return config, service, ClientWorkflow(config, service, PRINCIPAL)


def write(workflow, builder=None, **kwargs):
    return workflow.write(
        "logs.write",
        builder or (lambda: {"sourceId": "manual", "fields": {"notes": "synthetic"}}),
        intent=["--notes", "synthetic"],
        resource_id="measurement",
        **kwargs,
    )


def state(config):
    return json.loads(config.path("personal/state/native-client.json").read_text())


def test_lost_reply_restart_reuses_original_request_and_default_timestamp(fixture):
    config, service, workflow = fixture
    service.lose_once = True
    builder = Mock(
        return_value={
            "sourceId": "manual",
            "fields": {"measuredAtLocal": "2030-01-01T08:00:00+00:00"},
        }
    )
    with pytest.raises(ServiceError, match="outcome_unknown"):
        write(workflow, builder)
    first = state(config)
    assert first["state"] == "pending" and first["cursor"] == 0
    assert first["receipt"] is None
    restarted = ClientWorkflow(config, service, PRINCIPAL)
    receipt = write(restarted, builder)
    assert builder.call_count == 1
    assert service.requests[0] == service.requests[1]
    assert service.revision == 1
    assert state(config)["envelope"] == first["envelope"]
    assert state(config)["cursor"] == 1
    assert base64.b64decode(state(config)["receipt"]["bodyBase64"]) == encode(receipt)
    assert write(restarted, builder) == receipt
    assert state(config)["cursor"] == 1 and service.revision == 1
    assert config.path("personal/state/native-client.json").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    "failure", ["revision_conflict", "identity_mismatch", "epoch_mismatch"]
)
def test_definitive_conflict_retains_original_until_explicit_resolution(
    fixture, failure
):
    config, service, workflow = fixture
    service.failure = failure
    with pytest.raises(ServiceError, match=failure):
        write(workflow)
    original = state(config)["envelope"]
    with pytest.raises(ServiceError, match="pending_write_requires_resolution"):
        write(workflow, new_write=True)
    with pytest.raises(ServiceError, match="acknowledgement_required"):
        workflow.discard()
    assert state(config)["envelope"] == original
    assert workflow.inspect() == {
        "state": "pending",
        "cursor": 0,
        "operation": "logs.write",
        "lastError": {"code": failure, "status": 409},
    }
    assert workflow.discard(acknowledge_possible_save=True)["state"] == "discarded"
    assert state(config)["envelope"] == original
    service.failure = None
    write(workflow)
    assert state(config)["envelope"]["idempotencyKey"] != original["idempotencyKey"]


def test_completed_replay_rechecks_authority_and_explicit_new_write(fixture):
    config, service, workflow = fixture
    write(workflow)
    original = state(config)
    service.allowed = False
    with pytest.raises(ServiceError, match="forbidden"):
        workflow.retry()
    assert state(config)["receipt"] == original["receipt"]
    assert state(config)["cursor"] == 1
    service.allowed = True
    write(workflow, new_write=True)
    assert state(config)["cursor"] == 2 and service.revision == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "revision_same",
        "revision_jump",
        "epoch",
        "api_bool",
        "etag",
        "not_saved",
        "malformed",
    ],
)
def test_invalid_receipt_never_advances_cursor_or_changes_request(fixture, mutation):
    config, service, workflow = fixture

    def corrupt(result):
        body = json.loads(result.body)
        headers = result.headers
        if mutation == "revision_same":
            body["meta"]["dataRevision"] = 0
        elif mutation == "revision_jump":
            body["meta"]["dataRevision"] = 3
        elif mutation == "epoch":
            body["meta"]["restoreEpoch"] = "00000000-0000-4000-8000-000000000099"
        elif mutation == "api_bool":
            body["meta"]["apiVersion"] = True
        elif mutation == "etag":
            headers = ()
        elif mutation == "not_saved":
            body["data"]["saved"] = False
        elif mutation == "malformed":
            return Response(200, b"null")
        return Response(result.status, encode(body), headers)

    service.mutate = corrupt
    with pytest.raises(ServiceError):
        write(workflow)
    original = state(config)["envelope"]
    assert state(config)["state"] == "pending" and state(config)["cursor"] == 0
    service.mutate = lambda value: value
    workflow.retry()
    assert state(config)["envelope"] == original and state(config)["cursor"] == 1


@pytest.mark.parametrize(
    "operation,resource,payload,key",
    [
        ("records.put", "synthetic-record", {}, "recordId"),
        ("plan.write", None, {"programId": "synthetic-plan"}, "planId"),
        ("workouts.write", None, {"session_id": "synthetic-session"}, "sessionId"),
    ],
)
def test_wrong_resource_receipt_is_rejected(fixture, operation, resource, payload, key):
    config, service, workflow = fixture

    def corrupt(result):
        value = json.loads(result.body)
        value["data"][key] = "another-resource"
        return Response(result.status, encode(value), result.headers)

    service.mutate = corrupt
    with pytest.raises(ServiceError, match="invalid_receipt"):
        workflow.write(operation, lambda: payload, intent=payload, resource_id=resource)
    assert state(config)["cursor"] == 0


@pytest.mark.parametrize("changed", ["status", "body"])
def test_completed_receipt_cannot_change_on_replay(fixture, changed):
    config, service, workflow = fixture
    write(workflow)
    original = state(config)

    def mutate(result):
        value = json.loads(result.body)
        if changed == "body":
            value["data"]["projection"]["state"] = "current"
        return Response(
            201 if changed == "status" else result.status, encode(value), result.headers
        )

    service.mutate = mutate
    with pytest.raises(ServiceError, match="receipt_changed"):
        workflow.retry()
    assert state(config)["receipt"] == original["receipt"]
    assert state(config)["envelope"] == original["envelope"]
    assert state(config)["cursor"] == 1


def test_actor_handle_change_fails_closed_without_new_send(fixture):
    config, service, workflow = fixture
    service.lose_once = True
    with pytest.raises(ServiceError):
        write(workflow)
    different = ClientWorkflow(config, service, Principal("different-handle"))
    with pytest.raises(ServiceError, match="client_identity_changed"):
        different.retry()
    assert len(service.requests) == 1 and state(config)["cursor"] == 0


def test_plan_file_edits_are_new_intents_and_pending_retry_retains_old_bytes(
    fixture, tmp_path
):
    config, service, _workflow = fixture
    app = App(
        config.root, operations=service, principal=PRINCIPAL, configuration=config
    )
    path = tmp_path / "program.json"
    value = program()
    path.write_text(json.dumps(value))
    app.set_plan(path)
    value["title"] = "Changed synthetic plan"
    path.write_text(json.dumps(value))
    app.set_plan(path)
    assert service.revision == 2
    value["title"] = "Pending synthetic plan"
    path.write_text(json.dumps(value))
    service.lose_once = True
    with pytest.raises(ServiceError):
        app.set_plan(path)
    original = state(config)["envelope"]
    value["title"] = "Another selected intent"
    path.write_text(json.dumps(value))
    with pytest.raises(ServiceError, match="pending_write_requires_resolution"):
        app.set_plan(path)
    path.unlink()
    app.workflow.retry()
    assert state(config)["envelope"] == original and service.revision == 3


def test_unreadable_retry_state_is_not_overwritten(fixture):
    config, service, workflow = fixture
    write(workflow)
    path = config.path("personal/state/native-client.json")
    path.write_text("{unreadable")
    with pytest.raises(ServiceError):
        write(workflow)
    assert path.read_text() == "{unreadable" and service.revision == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation", []),
        ("ifMatch", {}),
        ("payload", []),
        ("idempotencyKey", "invalid key"),
        ("identity", None),
    ],
)
def test_malformed_pending_fields_fail_safely_without_overwrite(fixture, field, value):
    config, service, workflow = fixture
    service.lose_once = True
    with pytest.raises(ServiceError):
        write(workflow)
    saved = state(config)
    saved["envelope"][field] = value
    path = config.path("personal/state/native-client.json")
    path.write_text(json.dumps(saved))
    before = path.read_bytes()
    with pytest.raises(ServiceError, match="client_state_unavailable"):
        workflow.retry()
    assert path.read_bytes() == before and len(service.requests) == 1


def test_plan_duplicate_keys_rejected_before_send(fixture, tmp_path):
    config, service, _workflow = fixture
    app = App(
        config.root, operations=service, principal=PRINCIPAL, configuration=config
    )
    path = tmp_path / "program.json"
    path.write_text('{"schema_version":2,"schema_version":2}')
    with pytest.raises(ServiceError, match="invalid_request"):
        app.set_plan(path)
    assert not service.requests


def test_failure_to_persist_before_send_does_not_execute(fixture):
    _config, service, workflow = fixture
    with patch(
        "health_buddy.client_workflow.atomic_bytes",
        side_effect=OSError("disk unavailable"),
    ):
        with pytest.raises(OSError):
            write(workflow)
    assert not service.requests


def test_completed_replay_retains_first_receipt_when_http_date_changes(fixture):
    config, service, workflow = fixture
    service.mutate = lambda reply: replace(
        reply, headers=(*reply.headers, ("date", "Wed, 30 Sep 2026 10:00:00 GMT"))
    )
    write(workflow)
    original = state(config)
    service.mutate = lambda reply: replace(
        reply, headers=(*reply.headers, ("date", "Wed, 30 Sep 2026 10:02:00 GMT"))
    )
    ClientWorkflow(config, service, PRINCIPAL).retry()
    reopened = state(config)
    assert reopened["receipt"] == original["receipt"]
    assert reopened["envelope"] == original["envelope"]
    assert reopened["cursor"] == 1 and service.revision == 1
    assert service.requests[0] == service.requests[1]
