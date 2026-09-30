"""Native owner admission of one reviewed imported stream, without credentials."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from .domain import check_identity, decode, identifier, object_value
from .extension_files import read_file
from .runtime_manifest import native_directory
from .security_actions import _name
from .security_api import SecurityReply
from .service_api import Identity, Principal, ServiceError

if TYPE_CHECKING:
    from .security import SecurityAuthority


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
            receipt: Any = object_value(
                decode(
                    read_file(
                        service.config.root / "operations/receiver-import.json", 16384
                    ),
                    limit=16384,
                )
            )
            if (
                not isinstance(receipt, dict)
                or set(receipt)
                != {
                    "schemaVersion",
                    "family",
                    "sourceSha256",
                    "snapshotSha256",
                    "mapping",
                    "realPairingBridge",
                }
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
            mappings = receipt["mapping"]
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
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            raise ServiceError(409, "import_admission_binding_conflict") from None
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
            actor_id, active, duplicate = actor["id"], bool(actor["active"]), True
        else:
            if connection.execute("SELECT count(*) FROM actors").fetchone()[0] >= 128:
                raise ServiceError(429, "actor_limit")
            actor_id, active, duplicate = str(uuid4()), False, False
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
