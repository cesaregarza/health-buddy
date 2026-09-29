"""Fabricated canonical workspaces and policy-issued authenticated handles."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from health_buddy.operations import Service
from health_buddy.service_api import (
    Authority,
    Identity,
    Principal,
    Request,
    ServiceError,
)
from health_buddy.workspace import initialize
from tests.test_health_ingest_models import batch_payload


class RegisteredPolicy:
    def __init__(self):
        self.lock = RLock()
        self.handles = {}

    def issue(self, **overrides):
        principal = Principal("authenticated-" + uuid4().hex)
        authority = Authority(
            actor_id="synthetic-owner",
            grants=frozenset({"records:read", "records:write", "operations:admin"}),
            source_ids=frozenset({"manual"}),
        )
        self.handles[principal.credential_id] = replace(authority, **overrides)
        return principal

    @contextmanager
    def guard(self, principal, _operation):
        with self.lock:
            if principal is None or principal.credential_id not in self.handles:
                raise ServiceError(401, "unauthenticated")
            yield self.handles[principal.credential_id]

    def revoke(self, principal):
        with self.lock:
            self.handles.pop(principal.credential_id, None)


def setup(root: Path, *, receiver=False, fault=None):
    initialize(root)
    if receiver:
        path = root / "config.json"
        config = json.loads(path.read_text())
        config["integrations"]["healthkit"] = {"enabled": True, "mode": "receiver"}
        path.write_text(json.dumps(config))
    policy = RegisteredPolicy()
    principal = policy.issue()
    return Service(root, policy, fault=fault), policy, principal


def decoded(response):
    return json.loads(response.body)


def metadata(service, principal):
    response = service.execute(principal, Request("capabilities"))
    assert response.status == 200, response.body
    meta = decoded(response)["meta"]
    return Identity(
        meta["installationId"], meta["datasetId"], meta["restoreEpoch"]
    ), f'"rev-{meta["dataRevision"]}"'


def intent(
    service,
    principal,
    *,
    record_id="synthetic-weight",
    key=None,
    source_id="manual",
    value=80,
):
    identity, revision = metadata(service, principal)
    payload = {
        "kind": "body-mass",
        "value": value,
        "unit": "kg",
        "observedAt": datetime.now(UTC).isoformat(),
        "sourceId": source_id,
    }
    return Request(
        "records.put",
        resource_id=record_id,
        payload=payload,
        identity=identity,
        if_match=revision,
        idempotency_key=key or uuid4().hex,
    )


def receiver_principal(service, policy, owner):
    device_id, stream_id = str(uuid4()), str(uuid4())
    service.register_source(
        owner, "synthetic-phone", "healthkit", device_id=device_id, stream_id=stream_id
    )
    handle = policy.issue(
        actor_id="synthetic-phone",
        grants=frozenset({"healthkit:ingest", "sync:status"}),
        source_ids=frozenset({"synthetic-phone"}),
        device_id=device_id,
        source_stream_id=stream_id,
    )
    identity, _revision = metadata(service, handle)
    body = batch_payload(device_id)
    return handle, Request(
        "healthkit.ingest", payload=body, identity=identity, health_device_id=device_id
    )
