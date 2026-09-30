"""Model API tests for durable tool intents, proposal review and safe errors.

These exercise actual private files, not TLS or canonical server transactions.
"""

import json
from contextlib import contextmanager
from dataclasses import replace

import pytest

from health_buddy.domain import encode, envelope, identity_value
from health_buddy.mcp_proposals import Proposals
from health_buddy.mcp_schemas import SPECS, validate
from health_buddy.mcp_settings import Settings
from health_buddy.mcp_tools import ToolService
from health_buddy.plans import to_wire
from health_buddy.security_api import ClientIdentity
from health_buddy.service_api import ServiceError
from tests.synthetic_workspace import program
from tests.test_extension_workflow import IDENTITY
from tests.test_mcp_settings import settings_file


class ModelApi:
    def __init__(self):
        self.client = ClientIdentity("synthetic-actor", "synthetic-security-epoch", IDENTITY)
        self.available = {spec.operation for spec in SPECS}
        self.receipts = {}
        self.requests = []
        self.revision = 0
        self.lose = False
        self.revoked = False

    @contextmanager
    def dispatch(self, deadline):
        yield

    def describe(self):
        if self.revoked:
            raise ServiceError(401, "unauthorized")
        return self.client

    def preflight(self, principal, operation):
        return None

    def execute(self, principal, request):
        self.describe()
        if request.operation == "capabilities":
            return envelope({"availableOperations": sorted(self.available)}, self.client.identity, self.revision)
        assert request.operation in self.available
        self.requests.append(replace(request, deadline=None))
        if request.operation == "context.read":
            return envelope({"missing": None, "zero": 0, "truncated": True}, IDENTITY, self.revision)
        if request.idempotency_key in self.receipts:
            return self.receipts[request.idempotency_key]
        if request.if_match != f'"rev-{self.revision}"':
            raise ServiceError(409, "revision_conflict")
        self.revision += 1
        data = {"saved": True}
        if request.operation == "plan.write":
            data["planId"] = request.payload["programId"]
        result = envelope(data, IDENTITY, self.revision)
        self.receipts[request.idempotency_key] = result
        if self.lose:
            self.lose = False
            raise TimeoutError("synthetic-secret-in-transport-exception")
        return result


def setup(tmp_path):
    path, _ = settings_file(tmp_path)
    settings = Settings.read(path)
    api = ModelApi()
    return ToolService(settings, api), settings, api


def intent():
    return {"intentId": "synthetic-observation", "identity": identity_value(IDENTITY),
            "expectedRevision": 0, "kind": "measurement", "sourceId": "manual",
            "fields": {"measuredAtLocal": "2026-09-01T10:00:00+00:00", "timezone": "UTC", "weightLb": 180}}


def test_lost_ack_restart_preserves_body_key_revision_and_rechecks_actor(tmp_path):
    tools, settings, api = setup(tmp_path)
    api.lose = True
    failed = tools.call("log_health", intent())
    assert failed["ok"] is False and "synthetic-secret" not in json.dumps(failed)
    pending_path = next(settings.retry_root.glob("profiles/*/requests/*.json"))
    pending = json.loads(pending_path.read_bytes())
    assert pending["state"] == "pending" and api.revision == 1
    reopened = ToolService(settings, api)
    result = reopened.call("retry_write", {"intentId": intent()["intentId"]})
    assert result["ok"] is True and api.revision == 1
    after = json.loads(pending_path.read_bytes())
    assert after["envelope"] == pending["envelope"] and after["cursor"] == 1
    assert api.requests[0] == api.requests[1]
    api.client = replace(api.client, actor_binding="different-actor")
    assert reopened.call("write_status", {"intentId": intent()["intentId"]})["error"]["code"] == "client_identity_changed"
    assert reopened.call("retry_write", {"intentId": intent()["intentId"]})["error"]["code"] == "client_identity_changed"
    assert len(api.requests) == 2


def test_changed_intent_cannot_replace_original_or_refresh_revision(tmp_path):
    tools, _, api = setup(tmp_path)
    assert tools.call("log_health", intent())["ok"] is True
    changed = intent()
    changed["fields"]["weightLb"] = 181
    assert tools.call("log_health", changed)["error"]["code"] == "mcp_intent_conflict"
    changed = intent()
    changed["expectedRevision"] = 1
    assert tools.call("log_health", changed)["error"]["code"] == "mcp_intent_conflict"
    assert api.revision == len(api.requests) == 1


def test_proposal_requires_exact_review_body_original_cas_and_actor(tmp_path):
    tools, _, api = setup(tmp_path)
    args = {"intentId": "plan-review-1", "identity": identity_value(IDENTITY), "expectedRevision": 0, "plan": to_wire(program())}
    prepared = tools.call("propose_plan", args)
    assert prepared["ok"] is True and prepared["result"]["applied"] is False
    assert api.requests == [] and api.revision == 0
    review = prepared["result"]
    apply = {"proposalId": review["proposalId"], "reviewDigest": review["reviewDigest"], "confirm": True}
    assert tools.call("apply_plan", {**apply, "reviewDigest": "0" * 64})["error"]["code"] == "proposal_review_required"
    changed = {**args, "plan": {**args["plan"], "title": "Edited synthetic plan"}}
    assert tools.call("propose_plan", changed)["error"]["code"] == "proposal_conflict"
    assert tools.call("apply_plan", apply)["ok"] is True
    assert tools.call("apply_plan", apply)["ok"] is True
    assert api.revision == 1 and api.requests[0].payload == args["plan"]
    assert api.requests[0] == api.requests[1]
    stale = {**args, "intentId": "another-plan"}
    second = tools.call("propose_plan", stale)["result"]
    assert tools.call("apply_plan", {"proposalId": second["proposalId"], "reviewDigest": second["reviewDigest"], "confirm": True})["error"]["code"] == "revision_conflict"
    assert api.revision == 1


def test_catalog_current_capabilities_and_no_implicit_context(tmp_path):
    tools, _, api = setup(tmp_path)
    api.available = {"capabilities", "context.read"}
    assert {spec.name for spec in tools.catalog()} == {"get_context", "write_status", "retry_write"}
    assert api.requests == []
    result = tools.call("get_context", {"scopes": ["weight"]})
    assert result["result"]["data"] == {"missing": None, "zero": 0, "truncated": True}
    assert api.requests[-1].query == {"scopes": "weight", "days": "7", "limit": "100"}
    assert tools.call("log_health", intent())["error"]["code"] == "capability_unavailable"
    assert len(api.requests) == 1


@pytest.mark.parametrize("name,args", [("shell", {"command": "ignored"}), ("get_context", {"scopes": ["secrets"]}), ("log_health", {**intent(), "url": "https://invalid.example"}), ("log_health", {**intent(), "expectedRevision": True})])
def test_strict_finite_schemas(name, args):
    with pytest.raises(ServiceError, match="invalid_request"):
        validate(name, args)


def test_corrupt_proposal_retained_and_unreadable(tmp_path):
    tools, _, api = setup(tmp_path)
    args = {"intentId": "plan-review-1", "identity": identity_value(IDENTITY), "expectedRevision": 0, "plan": to_wire(program())}
    assert tools.call("propose_plan", args)["ok"] is True
    path = next(tools.root.root.glob("proposals/*/*.json"))
    raw = json.loads(path.read_bytes())
    raw["schemaVersion"] = True
    contents = encode(raw)
    path.write_bytes(contents)
    with pytest.raises(ServiceError, match="proposal_unavailable"):
        Proposals(tools.root, tools.settings.client_id).selected(args["intentId"], "0" * 64, api.client)
    assert path.read_bytes() == contents and api.requests == []
