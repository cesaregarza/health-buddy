"""Opt-in canonical receiver on the one configured HealthKit SQLite path.

Read-only imports are never adopted here. Canonical stream/delivery metadata and
the effect marker commit with the retained receiver's batch/tombstone effects.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import cast

from health_ingest.models import Batch, parse_batch
from health_ingest.storage import BatchConflictError, HealthRepository

from .domain import decode, digest, encode, identity_value, instant, object_value, text
from .durability import fsync_path, private_file, unavailable
from .service_api import JSON, Identity, ServiceError

SCHEMA = """
CREATE TABLE canonical_receiver(identity_json TEXT NOT NULL);
CREATE TABLE canonical_effects(transaction_id TEXT PRIMARY KEY, manifest_digest TEXT NOT NULL);
CREATE TABLE source_streams(source_id TEXT PRIMARY KEY, stream_id TEXT UNIQUE NOT NULL, active_device_id TEXT UNIQUE NOT NULL);
CREATE TABLE stream_objects(
 stream_id TEXT NOT NULL, record_id TEXT NOT NULL, type_identifier TEXT NOT NULL,
 canonical_device_id TEXT, record_digest TEXT, deleted_at TEXT,
 PRIMARY KEY(stream_id,record_id));
CREATE TABLE delivery_provenance(
 stream_id TEXT NOT NULL, record_id TEXT NOT NULL, device_id TEXT NOT NULL,
 received_at TEXT NOT NULL, PRIMARY KEY(stream_id,record_id,device_id));
CREATE VIEW canonical_records AS
 SELECT r.* FROM records r JOIN stream_objects s
 ON r.device_id=s.canonical_device_id AND r.record_id=s.record_id
 WHERE s.deleted_at IS NULL;
"""


class HealthStore:
    def __init__(self, path: Path, *, receiver: bool, fault: Callable[[str], None] | None = None) -> None:
        self.path = path
        self.receiver = receiver
        self.repository = HealthRepository(path)
        self.fault = fault or (lambda _point: None)

    def initialize(self, identity: Identity) -> None:
        """Explicit receiver-mode startup; refuse nonempty imported databases."""
        if not self.receiver:
            return
        private_file(self.path, missing=True)
        if not self.path.exists():
            self.repository.migrate()
        with closing(self.repository._connect()) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_receiver'").fetchone()
            if exists:
                self._check_identity(connection, identity_value(identity))
                return
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"records", "devices", "batches", "tombstones"} <= tables:
                raise ServiceError(409, "reconciliation_required")
            for table in ("records", "devices", "batches", "tombstones"):
                if connection.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone():
                    raise ServiceError(409, "reconciliation_required", details={"reason": "nonempty_readonly_source_requires_operator_adoption"})
            # Empty-only schema adoption is an explicit receiver-mode setup,
            # not a health write or a private-data migration.
            connection.executescript("BEGIN IMMEDIATE;" + SCHEMA)
            connection.execute("INSERT INTO canonical_receiver VALUES (?)", (encode(identity_value(identity)).decode(),))
            connection.commit()
        self._sync()

    def _sync(self) -> None:
        fsync_path(self.path)
        for suffix in ("-wal", "-shm"):
            sibling = Path(str(self.path) + suffix)
            if sibling.exists():
                os.chmod(sibling, 0o600)
                fsync_path(sibling)
        fsync_path(self.path.parent)

    def _check_identity(self, connection: sqlite3.Connection, expected: dict[str, JSON]) -> None:
        rows = connection.execute("SELECT identity_json FROM canonical_receiver").fetchall()
        if len(rows) != 1 or decode(rows[0][0]) != expected:
            raise ServiceError(409, "identity_changed")

    def _apply(self, connection: sqlite3.Connection, transaction_id: str, effect: dict[str, JSON]) -> None:
        payload = object_value(effect, {"kind", "identity", "sourceId", "streamId", "deviceId", "receivedAt", "batch"})
        identity = object_value(payload["identity"], {"installationId", "datasetId", "restoreEpoch"})
        self._check_identity(connection, identity)
        effect_digest = digest(effect)
        prior = connection.execute("SELECT manifest_digest FROM canonical_effects WHERE transaction_id=?", (transaction_id,)).fetchone()
        if prior:
            if prior[0] != effect_digest:
                raise unavailable()
            return
        source_id, stream_id, device_id = (text(payload[key], limit=128) for key in ("sourceId", "streamId", "deviceId"))
        received_at = instant(payload["receivedAt"])
        if payload["kind"] == "register":
            if payload["batch"] is not None:
                raise unavailable()
            connection.execute("INSERT INTO source_streams VALUES (?,?,?)", (source_id, stream_id, device_id))
            # Disabled legacy credential row only satisfies the retained schema
            # FK. No token is minted; auth belongs to the injected policy.
            connection.execute("INSERT INTO devices VALUES (?,?,?,?,?,?)", (device_id, "Canonical receiver device", b"", b"", received_at, received_at))
        elif payload["kind"] == "batch":
            binding = connection.execute("SELECT * FROM source_streams WHERE source_id=? AND stream_id=? AND active_device_id=?", (source_id, stream_id, device_id)).fetchone()
            if binding is None:
                raise ServiceError(403, "device_mismatch")
            batch = parse_batch(payload["batch"])
            if batch.device_id != device_id:
                raise ServiceError(403, "device_mismatch")
            self._apply_stream(connection, stream_id, batch, received_at)
        else:
            raise unavailable()
        connection.execute("INSERT INTO canonical_effects VALUES (?,?)", (transaction_id, effect_digest))

    def _apply_stream(self, connection: sqlite3.Connection, stream_id: str, batch: Batch, received_at: str) -> None:
        for record in batch.records:
            record_digest = hashlib.sha256(json.dumps(record.normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
            prior = connection.execute("SELECT * FROM stream_objects WHERE stream_id=? AND record_id=?", (stream_id, record.record_id)).fetchone()
            if prior and (prior["type_identifier"] != record.type_identifier or (prior["record_digest"] is not None and prior["record_digest"] != record_digest and record.record_kind != "dailyAggregate")):
                raise BatchConflictError("Canonical stream record conflict")
            connection.execute(
                "INSERT INTO stream_objects VALUES (?,?,?,?,?,?) ON CONFLICT(stream_id,record_id) DO UPDATE SET canonical_device_id=excluded.canonical_device_id,record_digest=excluded.record_digest",
                (stream_id, record.record_id, record.type_identifier, batch.device_id, record_digest, None),
            )
            connection.execute("INSERT INTO delivery_provenance VALUES (?,?,?,?) ON CONFLICT(stream_id,record_id,device_id) DO NOTHING", (stream_id, record.record_id, batch.device_id, received_at))
        for deletion in batch.deletions:
            prior = connection.execute("SELECT type_identifier FROM stream_objects WHERE stream_id=? AND record_id=?", (stream_id, deletion.record_id)).fetchone()
            if prior and prior[0] != deletion.type_identifier:
                raise BatchConflictError("Canonical stream tombstone conflict")
            connection.execute("INSERT INTO stream_objects VALUES (?,?,?,NULL,NULL,?) ON CONFLICT(stream_id,record_id) DO UPDATE SET deleted_at=excluded.deleted_at", (stream_id, deletion.record_id, deletion.type_identifier, deletion.observed_at))
        self.repository.apply_batch(connection, batch, received_at)

    def validate(self, effect: dict[str, JSON]) -> None:
        """Domain conflict validation with rollback before the global decision."""
        if not self.receiver:
            raise ServiceError(503, "source_unavailable", details={"reason": "healthkit_receiver_mode_required"})
        private_file(self.path)
        with closing(self.repository._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._apply(connection, "validation-only", effect)
            finally:
                connection.rollback()

    def install(self, transaction_id: str, effect: dict[str, JSON]) -> None:
        if not self.receiver:
            raise unavailable()
        private_file(self.path)
        with closing(self.repository._connect()) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            self._apply(connection, transaction_id, effect)
            connection.commit()
            self.fault("health_sqlite_commit")
        self._sync()

    def records(self) -> list[dict[str, JSON]]:
        if not self.receiver:
            return []
        private_file(self.path)
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT r.*, s.stream_id, p.source_id FROM canonical_records r JOIN stream_objects s ON r.record_id=s.record_id AND r.device_id=s.canonical_device_id JOIN source_streams p ON p.stream_id=s.stream_id ORDER BY r.start_at,r.record_id").fetchall()
            return [cast(dict[str, JSON], dict(row)) for row in rows]
        finally:
            connection.close()
