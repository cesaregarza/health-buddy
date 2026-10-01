"""Deliberate native connector source/grant preparation with recoverable intent.

No code activation, implicit bootstrap, second credential store, or secret
receipt. A lost one-time handoff requires explicit rotation of the same actor.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from health_buddy.core.config import Config
from health_buddy.core.domain import digest, encode, identifier
from health_buddy.core.durability import atomic_bytes, exclusive, fsync_path
from health_buddy.core.extension_api import PrepareConnector
from health_buddy.core.files import (
    extension_id,
    private_directory,
    read_file,
    read_json,
)
from health_buddy.core.security_api import (
    AgentGrant,
    Authenticated,
    BearerProof,
    ClientIdentity,
    Runtime,
    SecurityRequest,
)
from health_buddy.core.service_api import JSON, ServiceError
from health_buddy.extension.jobs import job_lock
from health_buddy.extension.manifest import parse_manifest
from health_buddy.extension.registry import Registry
from health_buddy.security.runtime import read_credential


def _identity(
    runtime: Runtime, proof: BearerProof
) -> tuple[Authenticated, ClientIdentity]:
    admitted = runtime.security.authenticate(proof)
    return admitted, runtime.security.describe(admitted.principal)


def _actors(runtime: Runtime, proof: BearerProof) -> list[dict[str, JSON]]:
    admitted, _ = _identity(runtime, proof)
    reply = runtime.security.execute(admitted.principal, SecurityRequest("grants.list"))
    values = reply.data.get("items")
    if not isinstance(values, list) or any(
        not isinstance(item, dict) for item in values
    ):
        raise ServiceError(503, "extension_preparation_unavailable")
    return cast(list[dict[str, JSON]], values)


def _matches(actor: dict[str, JSON], name: str, source: str) -> bool:
    return (
        actor.get("name") == name
        and actor.get("role") == "agent"
        and actor.get("active") is True
        and actor.get("grants") == ["records:write"]
        and actor.get("sourceIds") == [source]
        and actor.get("readSources") == []
        and actor.get("readKinds") == []
        and actor.get("readFields") == []
        and actor.get("deviceId") is None
        and actor.get("sourceStreamId") is None
    )


def prepare(
    config: Config,
    runtime: Runtime,
    owner_proof: BearerProof,
    intent: PrepareConnector,
    *,
    fault: Callable[[str], None] | None = None,
) -> dict[str, JSON]:
    from health_buddy.core.operations import Service

    if not isinstance(runtime.operations, Service):
        raise ServiceError(503, "native_coordinator_required")
    service = runtime.operations
    fault = fault or (lambda _point: None)
    name = extension_id(intent.extension_id)
    source = identifier(intent.source_id)
    reference = intent.credential_reference
    if not reference.startswith("secrets/") or len(Path(reference).parts) != 2:
        raise ServiceError(422, "extension_secret_binding_invalid")
    output = config.path(reference)
    private_directory(output.parent)
    # Never reserve an output outside the current owner workspace or overwrite
    # a file. Credential bytes are consumed only through read_credential.
    state_path = config.path(f"personal/extensions/{name}/state/preparation.json")
    with job_lock(config, name):
        retained = _connector_state(config, service.lock, name, state_path)
        grant_name = "extension-" + digest({"id": name, "source": source})[:48]
        admitted, _client = _identity(runtime, owner_proof)
        # grants.list is owner-only and exposes exact current restrictions.
        actors = _actors(runtime, owner_proof)
        if retained is None:
            retained = _first_record(
                name, source, grant_name, reference, actors, output
            )
            with exclusive(service.lock):
                atomic_bytes(state_path, encode(retained))
        record = _validated_record(retained, name, source, grant_name)
        # Canonical registration is idempotent for the exact source descriptor.
        # This explicit side effect may remain if subsequent grant work fails.
        registered = service.register_source(admitted.principal, source, "connector")
        if registered.status not in (200, 201):
            raise ServiceError(503, "extension_source_preparation_failed")
        fault("source_registered")
        actors = _actors(runtime, owner_proof)
        actor_id = record["actorId"]
        if actor_id is None and record["phase"] in {"grant_pending", "handoff_pending"}:
            actor_id = record["actorId"] = _interrupted_grant(
                actors, record["priorActorIds"], grant_name, source
            )
        if actor_id is None and any(
            actor.get("name") == grant_name for actor in actors
        ):
            raise ServiceError(409, "extension_preparation_requires_reconciliation")
        if actor_id is not None:
            current = [actor for actor in actors if actor["id"] == actor_id]
            if len(current) != 1 or not _matches(current[0], grant_name, source):
                raise ServiceError(409, "extension_grant_requires_reconciliation")
            if output.exists():
                if reference != record["credentialReference"]:
                    raise ServiceError(409, "credential_file_exists")
                _validate_retained_credential(runtime, output, actor_id)
                record["phase"] = "ready"
                with exclusive(service.lock):
                    atomic_bytes(state_path, encode(record))
                return _prepared(source, actor_id, reference)
            if not intent.rotate_existing:
                with exclusive(service.lock):
                    atomic_bytes(state_path, encode(record))
                raise ServiceError(409, "extension_private_handoff_requires_rotation")
        elif intent.rotate_existing:
            raise ServiceError(409, "extension_no_retained_actor")
        if output.exists():
            raise ServiceError(409, "credential_file_exists")
        _hand_off(
            service.lock,
            runtime,
            owner_proof,
            output,
            state_path,
            record,
            reference=reference,
            fault=fault,
        )
        return _prepared(source, record["actorId"], reference)


def _prepared(source: str, actor_id: JSON, reference: str) -> dict[str, JSON]:
    return {
        "prepared": True,
        "sourceId": source,
        "actorId": actor_id,
        "credentialReference": reference,
        "enabled": False,
    }


def _connector_state(
    config: Config, lock: Path, name: str, state_path: Path
) -> JSON | None:
    """The retained preparation record of a connector extension, if any."""
    with exclusive(lock):
        manifest = parse_manifest(
            read_file(Registry(config).root(name) / "extension.json", 32_768)
        )
        if manifest.kind != "connector-workflow":
            raise ServiceError(422, "extension_not_connector")
        return read_json(state_path, 32_768) if state_path.exists() else None


def _first_record(
    name: str,
    source: str,
    grant_name: str,
    reference: str,
    actors: list[dict[str, JSON]],
    output: Path,
) -> dict[str, JSON]:
    """Nothing of a first preparation may exist before its intent is recorded."""
    if output.exists():
        raise ServiceError(409, "credential_file_exists")
    if any(actor.get("name") == grant_name for actor in actors):
        raise ServiceError(409, "extension_preparation_requires_reconciliation")
    return {
        "schemaVersion": 1,
        "extensionId": name,
        "sourceId": source,
        "grantName": grant_name,
        "actorId": None,
        "phase": "source_pending",
        "credentialReference": reference,
        "priorActorIds": [actor["id"] for actor in actors],
    }


def _validated_record(
    record: object, name: str, source: str, grant_name: str
) -> dict[str, Any]:
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "schemaVersion",
            "extensionId",
            "sourceId",
            "grantName",
            "actorId",
            "phase",
            "credentialReference",
            "priorActorIds",
        }
        or type(record["schemaVersion"]) is not int
        or record["schemaVersion"] != 1
        or record["extensionId"] != name
        or record["sourceId"] != source
        or record["grantName"] != grant_name
        or not isinstance(record["phase"], str)
        or record["phase"]
        not in {"source_pending", "grant_pending", "handoff_pending", "ready"}
        or (record["actorId"] is not None and not isinstance(record["actorId"], str))
        or not isinstance(record["credentialReference"], str)
        or not record["credentialReference"].startswith("secrets/")
        or len(Path(record["credentialReference"]).parts) != 2
        or not isinstance(record["priorActorIds"], list)
        or len(record["priorActorIds"]) > 128
        or any(not isinstance(item, str) for item in record["priorActorIds"])
    ):
        raise ServiceError(409, "extension_preparation_requires_reconciliation")
    return record


def _interrupted_grant(
    actors: list[dict[str, JSON]], prior: list[str], grant_name: str, source: str
) -> str | None:
    """The grant a run created before recording its actor, adopted not duplicated."""
    candidates = [
        actor
        for actor in actors
        if actor["id"] not in prior and actor.get("name") == grant_name
    ]
    if len(candidates) > 1:
        raise ServiceError(409, "extension_preparation_ambiguous_revoke_and_reconcile")
    if not candidates:
        return None
    if not _matches(candidates[0], grant_name, source):
        raise ServiceError(409, "extension_grant_requires_reconciliation")
    candidate_id = candidates[0]["id"]
    if not isinstance(candidate_id, str):
        raise ServiceError(503, "extension_preparation_unavailable")
    return candidate_id


def _validate_retained_credential(
    runtime: Runtime, output: Path, actor_id: str
) -> None:
    try:
        retained = runtime.security.authenticate(BearerProof(read_credential(output)))
        retained_client = runtime.security.describe(retained.principal)
        # Security actorBinding is the durable actor identifier.
        if retained_client.actor_binding != actor_id:
            raise ServiceError(409, "extension_credential_mismatch")
    except ServiceError:
        raise ServiceError(409, "extension_private_handoff_requires_rotation") from None


def _hand_off(
    lock: Path,
    runtime: Runtime,
    owner_proof: BearerProof,
    output: Path,
    state_path: Path,
    record: dict[str, Any],
    *,
    reference: str,
    fault: Callable[[str], None],
) -> None:
    """Reserve the credential file, then create or rotate the grant into it."""
    actor_id = record["actorId"]
    descriptor: int | None = None
    try:
        with exclusive(lock):
            descriptor = os.open(
                output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
            )
            fsync_path(output.parent)
            record["phase"] = "grant_pending" if actor_id is None else "handoff_pending"
            record["credentialReference"] = reference
            atomic_bytes(state_path, encode(record))
        fault("handoff_reserved")
        admitted, client = _identity(runtime, owner_proof)
        reply = runtime.security.execute(
            admitted.principal,
            SecurityRequest(
                "grants.create" if actor_id is None else "grants.rotate",
                payload=AgentGrant(
                    record["grantName"], ("records:write",), (record["sourceId"],)
                )
                if actor_id is None
                else None,
                resource_id=actor_id,
                identity=client.identity,
            ),
        )
        fault("grant_created")
        if reply.secret is None or reply.secret.kind != "agent-token":
            raise ServiceError(503, "extension_private_handoff_incomplete")
        record["actorId"] = reply.data["id"]
        record["phase"] = "handoff_pending"
        with exclusive(lock):
            atomic_bytes(state_path, encode(record))
            fault("actor_recorded")
            with os.fdopen(descriptor, "wb", closefd=False) as target:
                target.write((reply.secret.value + "\n").encode("ascii"))
                target.flush()
                os.fsync(target.fileno())
            fsync_path(output.parent)
            fault("credential_written")
            record["phase"] = "ready"
            atomic_bytes(state_path, encode(record))
    finally:
        if descriptor is not None:
            os.close(descriptor)
