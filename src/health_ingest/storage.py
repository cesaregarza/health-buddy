"""SQLite persistence, token authentication, and idempotent batch ingestion."""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from health_ingest.models import Batch

SCHEMA_VERSION = 1
TOKEN_BYTES = 32
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1

DAILY_EXPORT_FIELDS = (
    "local_date",
    "timezone",
    "type_identifier",
    "value",
    "unit",
    "source_bundle_identifier",
    "source_name",
    "record_id",
    "received_at",
)


class AuthenticationError(PermissionError):
    """Raised when a device token is absent, invalid, or revoked."""


class BatchConflictError(RuntimeError):
    """Raised when a batch ID is reused with different content."""


@dataclass(frozen=True)
class IngestResult:
    batch_id: str
    duplicate_batch: bool
    records_accepted: int
    deletions_accepted: int


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _device_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError("device_id must be a UUID") from exc


def _hash_token(token: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        token.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )


def _canonical_hash(batch: Batch) -> str:
    payload = json.dumps(
        batch.normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class HealthRepository:
    """Own the receiver database without exposing health-data reads over HTTP."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.database_path.exists():
            os.chmod(self.database_path, 0o600)
        with closing(self._connect()) as connection, connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, SCHEMA_VERSION}:
                raise RuntimeError(
                    f"unsupported health-ingest database version {version}"
                )
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE devices (
                        device_id TEXT PRIMARY KEY,
                        label TEXT NOT NULL,
                        token_salt BLOB NOT NULL,
                        token_hash BLOB NOT NULL,
                        created_at TEXT NOT NULL,
                        revoked_at TEXT
                    );

                    CREATE TABLE batches (
                        device_id TEXT NOT NULL,
                        batch_id TEXT NOT NULL,
                        payload_sha256 TEXT NOT NULL,
                        generated_at TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        record_count INTEGER NOT NULL,
                        deletion_count INTEGER NOT NULL,
                        PRIMARY KEY (device_id, batch_id),
                        FOREIGN KEY (device_id) REFERENCES devices(device_id)
                    );

                    CREATE TABLE records (
                        device_id TEXT NOT NULL,
                        record_id TEXT NOT NULL,
                        record_kind TEXT NOT NULL,
                        type_identifier TEXT NOT NULL,
                        start_at TEXT NOT NULL,
                        end_at TEXT NOT NULL,
                        creation_at TEXT,
                        local_date TEXT,
                        timezone TEXT,
                        value_json TEXT,
                        unit TEXT,
                        source_json TEXT,
                        device_json TEXT,
                        workout_json TEXT,
                        record_sha256 TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        deleted_at TEXT,
                        PRIMARY KEY (device_id, record_id),
                        FOREIGN KEY (device_id) REFERENCES devices(device_id)
                    );

                    CREATE INDEX records_type_start_idx
                    ON records(type_identifier, start_at);

                    CREATE INDEX records_daily_idx
                    ON records(record_kind, local_date, type_identifier);

                    CREATE TABLE tombstones (
                        device_id TEXT NOT NULL,
                        record_id TEXT NOT NULL,
                        type_identifier TEXT NOT NULL,
                        observed_at TEXT NOT NULL,
                        received_at TEXT NOT NULL,
                        PRIMARY KEY (device_id, record_id),
                        FOREIGN KEY (device_id) REFERENCES devices(device_id)
                    );

                    PRAGMA user_version = 1;
                    """
                )
        os.chmod(self.database_path, 0o600)
        for suffix in ("-wal", "-shm"):
            companion = Path(f"{self.database_path}{suffix}")
            if companion.exists():
                os.chmod(companion, 0o600)

    def issue_device(self, device_id: str, label: str) -> str:
        normalized_id = _device_uuid(device_id)
        normalized_label = label.strip()
        if not normalized_label:
            raise ValueError("label must not be empty")
        if len(normalized_label) > 100:
            raise ValueError("label exceeds 100 characters")
        token = secrets.token_urlsafe(TOKEN_BYTES)
        salt = secrets.token_bytes(16)
        token_hash = _hash_token(token, salt)
        with closing(self._connect()) as connection, connection:
            self._require_historical_store(connection)
            try:
                connection.execute(
                    """
                    INSERT INTO devices(
                        device_id, label, token_salt, token_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (normalized_id, normalized_label, salt, token_hash, _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"device already exists: {normalized_id}") from exc
        return token

    def revoke_device(self, device_id: str) -> bool:
        normalized_id = _device_uuid(device_id)
        with closing(self._connect()) as connection, connection:
            self._require_historical_store(connection)
            cursor = connection.execute(
                """
                UPDATE devices
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE device_id = ?
                """,
                (_now(), normalized_id),
            )
        return cursor.rowcount == 1

    def authenticate(self, device_id: str, token: str) -> None:
        normalized_id = _device_uuid(device_id)
        if not token:
            raise AuthenticationError("invalid device credentials")
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT token_salt, token_hash, revoked_at
                FROM devices
                WHERE device_id = ?
                """,
                (normalized_id,),
            ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise AuthenticationError("invalid device credentials")
        candidate = _hash_token(token, row["token_salt"])
        if not hmac.compare_digest(candidate, row["token_hash"]):
            raise AuthenticationError("invalid device credentials")

    def ingest(self, batch: Batch) -> IngestResult:
        """Historical internal helper; supported entrypoints use canonical ops."""
        with closing(self._connect()) as connection, connection:
            self._require_historical_store(connection)
            connection.execute("BEGIN IMMEDIATE")
            return self.apply_batch(connection, batch, _now())

    @staticmethod
    def _require_historical_store(connection: sqlite3.Connection) -> None:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='canonical_receiver'").fetchone():
            raise ValueError("Use canonical operations for an adopted receiver")

    def apply_batch(
        self, connection: sqlite3.Connection, batch: Batch, received_at: str
    ) -> IngestResult:
        """Apply to the caller's transaction; no commit or clock read here.

        Canonical operations validate in a rolled-back savepoint, then install
        the prepared effect and its transaction marker in one SQLite commit.
        """
        payload_hash = _canonical_hash(batch)
        existing = connection.execute(
            """
            SELECT payload_sha256
            FROM batches
            WHERE device_id = ? AND batch_id = ?
            """,
            (batch.device_id, batch.batch_id),
        ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] != payload_hash:
                raise BatchConflictError(
                    "batchId was already used with different content"
                )
            return IngestResult(
                batch_id=batch.batch_id,
                duplicate_batch=True,
                records_accepted=0,
                deletions_accepted=0,
            )

        for record in batch.records:
            record_json = json.dumps(
                record.normalized,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            record_hash = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
            tombstone = connection.execute(
                """
                SELECT observed_at, type_identifier
                FROM tombstones
                WHERE device_id = ? AND record_id = ?
                """,
                (batch.device_id, record.record_id),
            ).fetchone()
            if tombstone and tombstone["type_identifier"] != record.type_identifier:
                raise BatchConflictError(
                    "recordId conflicts with a tombstone for another type"
                )
            existing_record = connection.execute(
                """
                SELECT record_kind, type_identifier, record_sha256
                FROM records
                WHERE device_id = ? AND record_id = ?
                """,
                (batch.device_id, record.record_id),
            ).fetchone()
            if existing_record is not None:
                if existing_record["type_identifier"] != record.type_identifier:
                    raise BatchConflictError(
                        "recordId conflicts with a record for another type"
                    )
                if (
                    record.record_kind != "dailyAggregate"
                    and existing_record["record_sha256"] != record_hash
                ):
                    raise BatchConflictError(
                        "an immutable HealthKit record changed content"
                    )
            deleted_at = tombstone["observed_at"] if tombstone else None
            connection.execute(
                """
                INSERT INTO records(
                    device_id, record_id, record_kind, type_identifier,
                    start_at, end_at, creation_at, local_date, timezone,
                    value_json, unit, source_json, device_json, workout_json,
                    record_sha256, received_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, record_id) DO UPDATE SET
                    record_kind = excluded.record_kind,
                    type_identifier = excluded.type_identifier,
                    start_at = excluded.start_at,
                    end_at = excluded.end_at,
                    creation_at = excluded.creation_at,
                    local_date = excluded.local_date,
                    timezone = excluded.timezone,
                    value_json = excluded.value_json,
                    unit = excluded.unit,
                    source_json = excluded.source_json,
                    device_json = excluded.device_json,
                    workout_json = excluded.workout_json,
                    record_sha256 = excluded.record_sha256,
                    received_at = excluded.received_at,
                    deleted_at = COALESCE(records.deleted_at, excluded.deleted_at)
                """,
                (
                    batch.device_id,
                    record.record_id,
                    record.record_kind,
                    record.type_identifier,
                    record.start_date,
                    record.end_date,
                    record.creation_date,
                    record.local_date,
                    record.timezone,
                    json.dumps(record.value, ensure_ascii=False),
                    record.unit,
                    json.dumps(record.source, ensure_ascii=False, sort_keys=True),
                    json.dumps(record.device, ensure_ascii=False, sort_keys=True),
                    json.dumps(record.workout, ensure_ascii=False, sort_keys=True),
                    record_hash,
                    received_at,
                    deleted_at,
                ),
            )

        for deletion in batch.deletions:
            existing_record = connection.execute(
                """
                SELECT type_identifier
                FROM records
                WHERE device_id = ? AND record_id = ?
                """,
                (batch.device_id, deletion.record_id),
            ).fetchone()
            if (
                existing_record is not None
                and existing_record["type_identifier"] != deletion.type_identifier
            ):
                raise BatchConflictError(
                    "deletion type does not match the stored record"
                )
            connection.execute(
                """
                INSERT INTO tombstones(
                    device_id, record_id, type_identifier,
                    observed_at, received_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id, record_id) DO UPDATE SET
                    type_identifier = excluded.type_identifier,
                    observed_at = excluded.observed_at,
                    received_at = excluded.received_at
                """,
                (
                    batch.device_id,
                    deletion.record_id,
                    deletion.type_identifier,
                    deletion.observed_at,
                    received_at,
                ),
            )
            connection.execute(
                """
                UPDATE records
                SET deleted_at = ?
                WHERE device_id = ? AND record_id = ?
                """,
                (deletion.observed_at, batch.device_id, deletion.record_id),
            )

        connection.execute(
            """
            INSERT INTO batches(
                device_id, batch_id, payload_sha256, generated_at,
                received_at, record_count, deletion_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch.device_id,
                batch.batch_id,
                payload_hash,
                batch.generated_at,
                received_at,
                len(batch.records),
                len(batch.deletions),
            ),
        )

        return IngestResult(
            batch_id=batch.batch_id,
            duplicate_batch=False,
            records_accepted=len(batch.records),
            deletions_accepted=len(batch.deletions),
        )

    def device_status(self, device_id: str) -> dict[str, str | int | None]:
        normalized_id = _device_uuid(device_id)
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT
                    d.label,
                    d.created_at,
                    d.revoked_at,
                    (
                        SELECT MAX(b.received_at)
                        FROM batches b
                        WHERE b.device_id = d.device_id
                    ) AS last_batch_at,
                    (
                        SELECT COUNT(*)
                        FROM batches b
                        WHERE b.device_id = d.device_id
                    ) AS batch_count,
                    (
                        SELECT COUNT(*)
                        FROM records r
                        WHERE r.device_id = d.device_id
                          AND r.deleted_at IS NULL
                    ) AS record_count
                FROM devices d
                WHERE d.device_id = ?
                """,
                (normalized_id,),
            ).fetchone()
        if row is None:
            raise AuthenticationError("invalid device credentials")
        return {
            "deviceId": normalized_id,
            "label": row["label"],
            "createdAt": row["created_at"],
            "revokedAt": row["revoked_at"],
            "lastBatchAt": row["last_batch_at"],
            "batchCount": row["batch_count"],
            "recordCount": row["record_count"],
        }

    def export_daily(self, output_path: Path) -> int:
        destination = output_path.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT
                    local_date,
                    timezone,
                    type_identifier,
                    value_json,
                    unit,
                    source_json,
                    record_id,
                    received_at
                FROM records
                WHERE record_kind = 'dailyAggregate' AND deleted_at IS NULL
                ORDER BY local_date, type_identifier, record_id
                """
            ).fetchall()

        temporary = destination.with_name(f".{destination.name}.tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=DAILY_EXPORT_FIELDS)
            writer.writeheader()
            for row in rows:
                source = json.loads(row["source_json"]) if row["source_json"] else {}
                writer.writerow(
                    {
                        "local_date": row["local_date"],
                        "timezone": row["timezone"],
                        "type_identifier": row["type_identifier"],
                        "value": json.loads(row["value_json"]),
                        "unit": row["unit"],
                        "source_bundle_identifier": source.get("bundleIdentifier", ""),
                        "source_name": source.get("name", ""),
                        "record_id": row["record_id"],
                        "received_at": row["received_at"],
                    }
                )
        temporary.replace(destination)
        return len(rows)
