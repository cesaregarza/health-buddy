"""Finite schema-1 receiver data export; explicit offline synthetic adoption."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from health_buddy.backup import disk_required
from health_buddy.core import config
from health_buddy.core.domain import decode, encode, identifier, instant
from health_buddy.core.durability import atomic_bytes, exclusive, fsync_path
from health_buddy.core.operations import Service
from health_buddy.core.policy import DEVELOPMENT_PRINCIPAL, DevelopmentPolicy
from health_buddy.core.service_api import JSON, ServiceError
from health_buddy.core.workspace import initialize
from health_buddy.legacy_import import MAX_BYTES, MAX_RECORDS, _path, _read
from health_buddy.runtime_manifest import SHA256
from health_ingest.models import Batch, parse_batch

FAMILY = "legacy-healthkit-receiver"
COLUMNS = {
    "devices": "device_id label token_salt token_hash created_at revoked_at".split(),
    "batches": (
        "device_id batch_id payload_sha256 generated_at received_at "
        "record_count deletion_count"
    ).split(),
    "records": (
        "device_id record_id record_kind type_identifier start_at end_at creation_at "
        "local_date timezone value_json unit source_json device_json workout_json "
        "record_sha256 received_at deleted_at"
    ).split(),
    "tombstones": "device_id record_id type_identifier observed_at received_at".split(),
}
ACK_SCHEMA = """CREATE TABLE legacy_adoption_ack(
 device_id TEXT NOT NULL,batch_id TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
 record_count INTEGER NOT NULL,deletion_count INTEGER NOT NULL,
 snapshot_sha256 TEXT NOT NULL,
 PRIMARY KEY(device_id,batch_id),FOREIGN KEY(device_id,batch_id)
 REFERENCES batches(device_id,batch_id));"""


def _uuid(value: JSON) -> str:
    if not isinstance(value, str):
        raise ServiceError(422, "import_invalid_receiver_identity")
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except ValueError:
        raise ServiceError(422, "import_invalid_receiver_identity") from None
    return value


def _batch(
    device: str, received: str, *, record: JSON = None, deletion: JSON = None
) -> Batch:
    return parse_batch(
        {
            "schemaVersion": 1,
            "batchId": str(uuid4()),
            "deviceId": device,
            "generatedAt": received,
            "records": [record] if record else [],
            "deletions": [deletion] if deletion else [],
        }
    )


def _record(row: dict[str, JSON]) -> dict[str, JSON]:
    return {
        "recordId": row["record_id"],
        "recordKind": row["record_kind"],
        "typeIdentifier": row["type_identifier"],
        "startDate": row["start_at"],
        "endDate": row["end_at"],
        "creationDate": row["creation_at"],
        "localDate": row["local_date"],
        "timezone": row["timezone"],
        "unit": row["unit"],
        **{
            key: decode(str(row[column]), limit=65_536)
            if row[column] is not None
            else None
            for key, column in (
                ("value", "value_json"),
                ("source", "source_json"),
                ("device", "device_json"),
                ("workout", "workout_json"),
            )
        },
    }


def _checked(value: JSON) -> dict[str, JSON]:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "family",
            "sourceSha256",
            "mapping",
            "devices",
            "records",
            "batches",
            "tombstones",
        }
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["family"] != FAMILY
        or not isinstance(value["sourceSha256"], str)
        or not SHA256.fullmatch(value["sourceSha256"])
    ):
        raise ServiceError(422, "import_invalid_receiver_snapshot")
    devices = value["devices"]
    if not isinstance(devices, list) or not 1 <= len(devices) <= 8:
        raise ServiceError(422, "import_invalid_receiver_devices")
    device_ids = {_uuid(device) for device in devices}
    mapping = value["mapping"]
    if (
        not isinstance(mapping, list)
        or len(mapping) != len(device_ids)
        or len(devices) != len(device_ids)
    ):
        raise ServiceError(422, "import_invalid_receiver_mapping")
    mapped: set[str] = set()
    sources: set[str] = set()
    streams: set[str] = set()
    for entry in mapping:
        if not isinstance(entry, dict) or set(entry) != {
            "deviceId",
            "sourceId",
            "streamId",
        }:
            raise ServiceError(422, "import_invalid_receiver_mapping")
        device, source, stream = (
            _uuid(entry["deviceId"]),
            identifier(entry["sourceId"]),
            _uuid(entry["streamId"]),
        )
        if (
            device not in device_ids
            or device in mapped
            or source in sources
            or stream in streams
            or source
            in {"manual", "healthkit", "healthkit-import", "sleepiq", "sleepiq-export"}
        ):
            raise ServiceError(409, "import_receiver_mapping_conflict")
        mapped.add(device)
        sources.add(source)
        streams.add(stream)
    count = len(devices)
    keys: dict[str, set[tuple[str, str]]] = {}
    for table in ("records", "batches", "tombstones"):
        rows = value[table]
        if not isinstance(rows, list):
            raise ServiceError(422, "import_invalid_receiver_snapshot")
        count += len(rows)
        if count > MAX_RECORDS:
            raise ServiceError(413, "import_snapshot_too_large")
        keys[table] = set()
        for item in rows:
            if not isinstance(item, dict) or set(item) != set(COLUMNS[table]):
                raise ServiceError(422, "import_invalid_receiver_row")
            device = _uuid(item["device_id"])
            if device not in device_ids:
                raise ServiceError(409, "import_receiver_foreign_device")
            instant(item["received_at"])
            key = (
                device,
                str(item["batch_id"] if table == "batches" else item["record_id"]),
            )
            if key in keys[table]:
                raise ServiceError(409, "import_receiver_duplicate_row")
            keys[table].add(key)
            if table == "batches":
                _uuid(item["batch_id"])
                instant(item["generated_at"])
                if (
                    not isinstance(item["payload_sha256"], str)
                    or not SHA256.fullmatch(item["payload_sha256"])
                    or any(
                        type(item[name]) is not int
                        or not 0 <= cast(int, item[name]) <= 500
                        for name in ("record_count", "deletion_count")
                    )
                    or cast(int, item["record_count"])
                    + cast(int, item["deletion_count"])
                    == 0
                ):
                    raise ServiceError(422, "import_invalid_receiver_receipt")
            elif table == "records":
                batch = _batch(device, str(item["received_at"]), record=_record(item))
                canonical = json.dumps(
                    batch.records[0].normalized,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                if hashlib.sha256(canonical).hexdigest() != item["record_sha256"]:
                    raise ServiceError(409, "import_receiver_record_digest_conflict")
                if item["deleted_at"] is not None:
                    instant(item["deleted_at"])
            else:
                _batch(
                    device,
                    str(item["received_at"]),
                    deletion={
                        "recordId": item["record_id"],
                        "typeIdentifier": item["type_identifier"],
                        "observedAt": item["observed_at"],
                    },
                )
    tombstones = {
        (str(row["device_id"]), str(row["record_id"])): row
        for row in cast(list[dict[str, JSON]], value["tombstones"])
    }
    for row in cast(list[dict[str, JSON]], value["records"]):
        tombstone = tombstones.get((str(row["device_id"]), str(row["record_id"])))
        if (row["deleted_at"] is not None) != (tombstone is not None) or (
            tombstone is not None
            and (
                row["type_identifier"] != tombstone["type_identifier"]
                or row["deleted_at"] != tombstone["observed_at"]
            )
        ):
            raise ServiceError(409, "import_receiver_tombstone_conflict")
    return value


def _export_receiver(
    database: Path,
    mapping: Path,
    output: Path,
    *,
    expected_source_sha256: str,
    expected_mapping_sha256: str,
    confirm_quiesced: bool,
) -> dict[str, Any]:
    if not confirm_quiesced:
        raise ServiceError(422, "import_receiver_requires_quiesced_snapshot")
    raw = _read(database, expected_source_sha256)
    mapping_value = decode(_read(mapping, expected_mapping_sha256), limit=16_384)
    if any(
        Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
    ):
        raise ServiceError(409, "import_receiver_requires_closed_sqlite_snapshot")
    with tempfile.TemporaryDirectory(
        prefix=".receiver-export-", dir=_path(output).parent
    ) as folder:
        source = Path(folder) / "source.db"
        atomic_bytes(source, raw)
        with closing(
            sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as connection:
            connection.row_factory = sqlite3.Row
            expires = time.monotonic() + 2
            connection.set_progress_handler(
                lambda: int(time.monotonic() >= expires), 1000
            )
            definitions = connection.execute(
                "SELECT type,name,sql FROM sqlite_master"
            ).fetchall()
            if (
                connection.execute("PRAGMA user_version").fetchone()[0] != 1
                or {row["name"] for row in definitions if row["type"] == "table"}
                != set(COLUMNS)
                or any(row["type"] not in {"table", "index"} for row in definitions)
                or any(
                    row["type"] == "table"
                    and not str(row["sql"]).startswith("CREATE TABLE ")
                    for row in definitions
                )
            ):
                raise ServiceError(422, "import_unsupported_receiver_schema")
            for table, names in COLUMNS.items():
                info = list(connection.execute("PRAGMA table_xinfo(" + table + ")"))
                if [row["name"] for row in info] != names or any(
                    row["hidden"] for row in info
                ):
                    raise ServiceError(422, "import_unsupported_receiver_schema")
            value: dict[str, JSON] = {
                "schemaVersion": 1,
                "family": FAMILY,
                "sourceSha256": expected_source_sha256,
                "mapping": mapping_value,
                "devices": [
                    row[0]
                    for row in connection.execute(
                        "SELECT device_id FROM devices LIMIT 9"
                    )
                ],
            }
            for table in ("records", "batches", "tombstones"):
                value[table] = cast(
                    list[JSON],
                    [
                        dict(row)
                        for row in connection.execute(
                            {
                                "records": "SELECT * FROM records LIMIT 1001",
                                "batches": "SELECT * FROM batches LIMIT 1001",
                                "tombstones": "SELECT * FROM tombstones LIMIT 1001",
                            }[table]
                        )
                    ],
                )
        checked = _checked(value)
        content = encode(checked)
        if len(content) > MAX_BYTES:
            raise ServiceError(413, "import_snapshot_too_large")
        output = _path(output)
        if output.exists():
            raise ServiceError(409, "import_output_exists")
        _read(database, expected_source_sha256)
        _read(mapping, expected_mapping_sha256)
        disk_required(output.parent, len(content))
        temporary = Path(folder) / "snapshot.json"
        atomic_bytes(temporary, content)
        os.link(temporary, output, follow_symlinks=False)
        fsync_path(output.parent)
    return {
        "exported": True,
        "records": len(cast(list[JSON], checked["records"])),
        "snapshotSha256": hashlib.sha256(content).hexdigest(),
    }


def _seed(service: Service, value: dict[str, JSON], snapshot_sha256: str) -> None:
    """Offline staging only, reuse the retained per-record/tombstone transitions."""
    streams = {
        str(row["deviceId"]): str(row["streamId"])
        for row in cast(list[dict[str, JSON]], value["mapping"])
    }
    with closing(service.health.repository._connect()) as connection:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        service.health._check_identity(
            connection,
            {
                "installationId": service.journal.state().identity.installation_id,
                "datasetId": service.journal.state().identity.dataset_id,
                "restoreEpoch": service.journal.state().identity.restore_epoch,
            },
        )
        for table in ("records", "batches", "tombstones", "stream_objects"):
            queries = {
                "records": "SELECT 1 FROM records LIMIT 1",
                "batches": "SELECT 1 FROM batches LIMIT 1",
                "tombstones": "SELECT 1 FROM tombstones LIMIT 1",
                "stream_objects": "SELECT 1 FROM stream_objects LIMIT 1",
            }
            if connection.execute(queries[table]).fetchone():
                raise ServiceError(409, "import_receiver_staging_not_empty")
        for row in cast(list[dict[str, JSON]], value["records"]):
            device = str(row["device_id"])
            batch = _batch(device, str(row["received_at"]), record=_record(row))
            service.health._apply_stream(
                connection, streams[device], batch, str(row["received_at"])
            )
        for row in cast(list[dict[str, JSON]], value["tombstones"]):
            device = str(row["device_id"])
            batch = _batch(
                device,
                str(row["received_at"]),
                deletion={
                    "recordId": row["record_id"],
                    "typeIdentifier": row["type_identifier"],
                    "observedAt": row["observed_at"],
                },
            )
            service.health._apply_stream(
                connection, streams[device], batch, str(row["received_at"])
            )
        # Synthetic staging batches only materialize current record state. They
        # are never acknowledgements or historical journal entries.
        connection.execute("DELETE FROM batches")
        connection.execute(ACK_SCHEMA)
        connection.execute(
            "CREATE TABLE legacy_adoption_snapshot(snapshot_sha256 TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO legacy_adoption_snapshot VALUES (?)", (snapshot_sha256,)
        )
        for row in cast(list[dict[str, JSON]], value["batches"]):
            connection.execute(
                "INSERT INTO batches VALUES (?,?,?,?,?,?,?)",
                tuple(row[name] for name in COLUMNS["batches"]),
            )
            connection.execute(
                "INSERT INTO legacy_adoption_ack VALUES (?,?,?,?,?,?)",
                (
                    row["device_id"],
                    row["batch_id"],
                    row["payload_sha256"],
                    row["record_count"],
                    row["deletion_count"],
                    snapshot_sha256,
                ),
            )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
    service.health._sync()


def seed_adopted_receiver(
    service: Service, value: dict[str, JSON], snapshot_sha256: str
) -> None:
    """Compose the validated receiver adapter inside an unpublished workspace."""
    staged = service.config.root
    for row in cast(list[dict[str, JSON]], value["mapping"]):
        service.register_source(
            DEVELOPMENT_PRINCIPAL,
            str(row["sourceId"]),
            "healthkit",
            device_id=str(row["deviceId"]),
            stream_id=str(row["streamId"]),
        )
    with exclusive(service.lock):
        _seed(service, value, snapshot_sha256)
        service.check_receiver(service.journal.verify().identity)
    # Reopen through ordinary receiver admission, with default-deny auth.
    checked = Service(staged)
    if checked.receiver_error is not None:
        raise checked.receiver_error
    provenance = {
        "schemaVersion": 1,
        "family": FAMILY,
        "sourceSha256": value["sourceSha256"],
        "snapshotSha256": snapshot_sha256,
        "mapping": value["mapping"],
        "realPairingBridge": "unimplemented",
    }
    atomic_bytes(staged / "operations/receiver-import.json", encode(provenance))


def import_receiver(
    target: Path, snapshot: Path, *, expected_snapshot_sha256: str
) -> dict[str, Any]:
    try:
        value = _checked(
            decode(_read(snapshot, expected_snapshot_sha256), limit=MAX_BYTES)
        )
    except (ValueError, TypeError, KeyError):
        raise ServiceError(422, "import_invalid_receiver_snapshot") from None
    target = _path(target)
    with exclusive(target.parent / ("." + target.name + ".import.lock")):
        if target.exists():
            raise ServiceError(409, "import_destination_occupied")
        disk_required(target.parent, MAX_BYTES * 4)
        with tempfile.TemporaryDirectory(
            prefix=".receiver-import-", dir=target.parent
        ) as folder:
            staged = Path(folder) / "workspace"
            initialize(staged)
            settings = config.load(staged).values
            settings["integrations"]["healthkit"] = {
                "enabled": True,
                "mode": "receiver",
            }
            atomic_bytes(staged / "config.json", encode(settings))
            service = Service(staged, DevelopmentPolicy())
            seed_adopted_receiver(service, value, expected_snapshot_sha256)
            fsync_path(staged)
            if target.exists():
                raise ServiceError(409, "import_destination_occupied")
            staged.rename(target)
            fsync_path(target.parent)
    return {
        "imported": True,
        "records": len(cast(list[JSON], value["records"])),
        "tombstones": len(cast(list[JSON], value["tombstones"])),
        "batches": len(cast(list[JSON], value["batches"])),
        "realPairingBridge": "unimplemented",
    }


def export_receiver(
    database: Path,
    mapping: Path,
    output: Path,
    *,
    expected_source_sha256: str,
    expected_mapping_sha256: str,
    confirm_quiesced: bool,
) -> dict[str, Any]:
    try:
        return _export_receiver(
            database,
            mapping,
            output,
            expected_source_sha256=expected_source_sha256,
            expected_mapping_sha256=expected_mapping_sha256,
            confirm_quiesced=confirm_quiesced,
        )
    except (sqlite3.Error, ValueError, TypeError, KeyError):
        raise ServiceError(422, "import_invalid_receiver_source") from None
