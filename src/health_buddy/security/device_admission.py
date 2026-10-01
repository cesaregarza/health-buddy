"""Native owner admission of one reviewed imported stream, without credentials."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from health_buddy.core.domain import check_identity, decode, identifier, object_value
from health_buddy.core.files import read_file
from health_buddy.core.security_api import SecurityReply
from health_buddy.core.service_api import Identity, Principal, ServiceError
from health_buddy.runtime.manifest import native_directory
from health_buddy.security.actions import _name

if TYPE_CHECKING:
    from health_buddy.core.operations import Service
    from health_buddy.security.authority import SecurityAuthority

RECEIPT_FIELDS = (
    "schemaVersion",
    "family",
    "sourceSha256",
    "snapshotSha256",
    "mapping",
    "realPairingBridge",
)


def admit(
    authority: SecurityAuthority,
    principal: Principal,
    *,
    identity: Identity,
    device_id: str,
    expected_snapshot_sha256: str,
    name: str,
) -> SecurityReply:
    service = authority.service
    with authority._locked() as connection:
        handle, owner = authority._resolve(connection, principal)
        if owner["role"] != "owner" or handle.mechanism != "bearer":
            raise ServiceError(403, "import_admission_requires_bearer_owner")
        check_identity(identity, service.journal.state().identity)
        service.journal.recover()
        check_identity(identity, service.journal.verify().identity)
        service.check_receiver(identity)
        try:
            native_directory(service.config.root)
            native_directory(service.config.root / "operations")
            if not re.fullmatch(r"[0-9a-f]{64}", expected_snapshot_sha256):
                raise ValueError
            if str(UUID(device_id)) != device_id:
                raise ValueError
            name = _name(name)
            mappings = _receipt_mappings(service, expected_snapshot_sha256)
            selected = [item for item in mappings if item["deviceId"] == device_id]
            if len(selected) != 1:
                raise ValueError
            source, stream = selected[0]["sourceId"], selected[0]["streamId"]
            if service.journal.sources().get(source) != {
                "source_id": source,
                "source_kind": "healthkit",
                "device_id": device_id,
                "source_stream_id": stream,
            }:
                raise ValueError
            _validate_receiver_stream(
                service, expected_snapshot_sha256, source, stream, device_id
            )
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            raise ServiceError(409, "import_admission_binding_conflict") from None
        actor_id, active, duplicate = _device_actor(
            authority, connection, name, device_id, source, stream
        )
        return SecurityReply(
            200 if duplicate else 201,
            {
                "id": actor_id,
                "deviceId": device_id,
                "sourceId": source,
                "sourceStreamId": stream,
                "active": active,
                "duplicate": duplicate,
            },
        )


def _receipt_mappings(service: Service, expected_snapshot_sha256: str) -> list[Any]:
    """The reviewed import receipt's device mappings, each unique and well formed."""
    receipt: Any = object_value(
        decode(
            read_file(service.config.root / "operations/receiver-import.json", 16384),
            limit=16384,
        ),
        set(RECEIPT_FIELDS),
    )
    if (
        not isinstance(receipt, dict)
        or set(receipt) != set(RECEIPT_FIELDS)
        or receipt["schemaVersion"] != 1
        or receipt["family"] != "legacy-healthkit-receiver"
        or receipt["snapshotSha256"] != expected_snapshot_sha256
        or receipt["realPairingBridge"] != "unimplemented"
        or not isinstance(receipt["sourceSha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", receipt["sourceSha256"])
        or not isinstance(receipt["mapping"], list)
        or not 1 <= len(receipt["mapping"]) <= 8
    ):
        raise ValueError
    mappings: list[Any] = receipt["mapping"]
    for item in mappings:
        if not isinstance(item, dict) or set(item) != {
            "deviceId",
            "sourceId",
            "streamId",
        }:
            raise ValueError
        if str(UUID(item["deviceId"])) != item["deviceId"]:
            raise ValueError
        if str(UUID(item["streamId"])) != item["streamId"]:
            raise ValueError
        identifier(item["sourceId"])
    for key in ("deviceId", "sourceId", "streamId"):
        if len({item[key] for item in mappings}) != len(mappings):
            raise ValueError
    return mappings


def _validate_receiver_stream(
    service: Service,
    expected_snapshot_sha256: str,
    source: str,
    stream: str,
    device_id: str,
) -> None:
    """The adopted receiver store must hold this snapshot and stream binding."""
    with closing(
        sqlite3.connect(service.health.path.as_uri() + "?mode=ro", uri=True)
    ) as receiver:
        if receiver.execute(
            "SELECT snapshot_sha256 FROM legacy_adoption_snapshot"
        ).fetchall() != [(expected_snapshot_sha256,)]:
            raise ValueError
        if receiver.execute(
            "SELECT source_id,stream_id,active_device_id FROM source_streams "
            "WHERE source_id=?",
            (source,),
        ).fetchall() != [(source, stream, device_id)]:
            raise ValueError


def _device_actor(
    authority: SecurityAuthority,
    connection: sqlite3.Connection,
    name: str,
    device_id: str,
    source: str,
    stream: str,
) -> tuple[str, bool, bool]:
    """The admitted device actor: the same one again, or a new inactive one.

    Returns its ID, whether it is active, and whether it already existed.
    """
    grants = ["healthkit:ingest", "sync:status"]
    existing = connection.execute(
        "SELECT * FROM actors WHERE device_id=? OR stream_id=?",
        (device_id, stream),
    ).fetchall()
    if existing:
        actor = existing[0]
        if len(existing) != 1 or (
            actor["role"] != "device"
            or actor["name"] != name
            or actor["device_id"] != device_id
            or actor["stream_id"] != stream
            or json.loads(actor["grants"]) != grants
            or json.loads(actor["sources"]) != [source]
            or any(
                json.loads(actor[key]) != []
                for key in ("read_sources", "read_kinds", "read_fields")
            )
        ):
            raise ServiceError(409, "import_admission_actor_conflict")
        return actor["id"], bool(actor["active"]), True
    if connection.execute("SELECT count(*) FROM actors").fetchone()[0] >= 128:
        raise ServiceError(429, "actor_limit")
    actor_id = str(uuid4())
    with connection:
        authority.store.add_actor(
            connection,
            actor_id,
            "device",
            name,
            grants,
            [source],
            [],
            [],
            [],
            device=device_id,
            stream=stream,
            active=False,
        )
        authority.store.event(connection, "devices.admit-imported")
    return actor_id, False, False
