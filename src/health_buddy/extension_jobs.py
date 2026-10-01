"""Explicit bounded connector jobs; canonical ClientWorkflow owns every write."""

from __future__ import annotations

import time
from contextlib import AbstractContextManager

from health_buddy.client.auth import AuthenticatedOperations
from health_buddy.client.workflow import ClientWorkflow, WorkflowNamespace
from health_buddy.core.config import Config
from health_buddy.core.domain import digest, instant, number, object_value, text
from health_buddy.core.durability import check_deadline, exclusive
from health_buddy.core.files import extension_id, private_directory
from health_buddy.core.security_api import BearerProof, Runtime, SecurityRequest
from health_buddy.core.service_api import JSON, ServiceError
from health_buddy.extension_diagnostics import observed_call
from health_buddy.extension_registry import Registry


def job_lock(config: Config, name: str) -> AbstractContextManager[None]:
    # Directory creation is protected by the same workspace lock as backup.
    with exclusive(config.path("operations/manual.lock")):
        state = config.path(f"personal/extensions/{extension_id(name)}/state")
        private_directory(state)
    return exclusive(state / "job.lock", time.monotonic() + 15)


def run_event(
    config: Config,
    runtime: Runtime,
    proof: BearerProof,
    name: str,
    event: dict[str, JSON],
) -> dict[str, JSON]:
    name = extension_id(name)
    deadline = time.monotonic() + 15
    with job_lock(config, name):
        admitted = runtime.security.authenticate(proof)
        own = runtime.security.execute(
            admitted.principal, SecurityRequest("session.get")
        )
        if own.data.get("role") != "agent":
            raise ServiceError(403, "extension_scoped_credential_required")
        with exclusive(config.path("operations/manual.lock"), deadline):
            reviewed = Registry(config).ready_locked(name)
        if reviewed.manifest.kind != "connector-workflow":
            raise ServiceError(422, "extension_not_connector")
        original = object_value(
            event, {"eventId", "observedAt", "sourceId", "value", "unit"}
        )
        event_id = text(original["eventId"], limit=128)
        source = text(original["sourceId"], limit=128)
        if source not in reviewed.source_ids:
            raise ServiceError(403, "extension_source_mismatch")
        instant(original["observedAt"])
        number(original["value"], 0, 1_000_000_000)
        text(original["unit"], limit=40)
        operations = AuthenticatedOperations(runtime, proof)
        workflow = ClientWorkflow(
            config,
            operations,
            admitted.principal,
            client_identity=operations.describe,
            namespace=WorkflowNamespace(name, event_id),
        )

        def build() -> JSON:
            check_deadline(deadline)
            normalized = object_value(
                observed_call(
                    config,
                    name,
                    reviewed.manifest.entrypoints["connector"],
                    reviewed.root,
                    {"schemaVersion": 1, "event": original, "config": reviewed.config},
                ),
                {"kind", "value", "unit", "observedAt", "sourceId"},
            )
            if (
                normalized["sourceId"] != source
                or normalized["observedAt"] != original["observedAt"]
            ):
                raise ServiceError(422, "extension_provenance_mismatch")
            if normalized["kind"] not in reviewed.manifest.write_kinds:
                raise ServiceError(422, "extension_kind_mismatch")
            number(normalized["value"], 0, 1_000_000_000)
            text(normalized["unit"], limit=40)
            # Workflow hook returns the same validated intent plus its source
            # event identity. It never receives authority or a raw store.
            plan = object_value(
                observed_call(
                    config,
                    name,
                    reviewed.manifest.entrypoints["workflow"],
                    reviewed.root,
                    {"schemaVersion": 1, "eventId": event_id, "record": normalized},
                ),
                {"eventId", "record"},
            )
            if plan["eventId"] != event_id or plan["record"] != normalized:
                raise ServiceError(422, "extension_workflow_invalid")
            check_deadline(deadline)
            return normalized

        # The original source event is the retained intent. A later code review
        # cannot reinterpret it: identical retries reuse the saved payload,
        # timestamps and CAS selection without re-running normalization.
        return workflow.write(
            "records.put",
            build,
            intent={"extensionId": name, "event": original},
            resource_id="ext:"
            + digest({"extension": name, "source": source, "event": event_id}),
        )
