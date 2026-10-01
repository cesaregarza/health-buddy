"""Data-only native measurement export and create-only canonical adoption."""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from health_buddy import config, loggers, records
from health_buddy.backup import disk_required, private_path
from health_buddy.domain import decode, digest, encode, identifier
from health_buddy.durability import atomic_bytes, exclusive, fsync_path
from health_buddy.extension_files import private_directory, read_file
from health_buddy.journal import Journal
from health_buddy.legacy_store import Store, headers, parse_csv
from health_buddy.operations import Service
from health_buddy.runtime_manifest import SHA256, native_directory
from health_buddy.service_api import JSON, ServiceError
from health_buddy.stores import RECORD_INDEX, ManualStore
from health_buddy.workspace import initialize

MAX_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 1000
MEASUREMENTS = "data/measurements.csv"
RECEIPT = "metadata/legacy-import.json"


def _path(path: Path) -> Path:
    if not path.is_absolute():
        raise ServiceError(422, "import_requires_explicit_native_paths")
    return private_path(path)


def _read(path: Path, expected: str) -> bytes:
    if not SHA256.fullmatch(expected):
        raise ServiceError(422, "import_requires_reviewed_sha256")
    raw = read_file(_path(path), MAX_BYTES)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ServiceError(409, "import_input_changed")
    return raw


def _rows(raw: str) -> list[dict[str, str]]:
    rows = parse_csv(raw, headers()[MEASUREMENTS])
    if not rows or len(rows) > MAX_RECORDS:
        raise ServiceError(422, "import_requires_bounded_nonempty_measurements")
    seen: set[str] = set()
    settings = config.Config(Path("/nonexistent"), config.defaults())
    for row in rows:
        fields: dict[str, JSON] = {
            loggers.camel(key): value for key, value in row.items() if value
        }
        loggers.validate_input("measurement", fields, settings)
        records.row_time(row, settings.zone.key)
        key = digest(records.natural_key(MEASUREMENTS, row))
        if key in seen:
            raise ServiceError(409, "import_duplicate_natural_key")
        seen.add(key)
    return rows


def export_measurements(
    source: Path, output: Path, *, source_id: str, expected_source_sha256: str
) -> dict[str, Any]:
    """Read only the selected CSV; never enumerate its directory or Git history."""
    identifier(source_id)
    raw = _read(source, expected_source_sha256)
    try:
        rows = _rows(raw.decode("utf-8"))
    except UnicodeError:
        raise ServiceError(422, "import_invalid_csv_encoding") from None
    ids = [
        str(
            uuid5(
                NAMESPACE_URL,
                "health-buddy:legacy:"
                + source_id
                + ":"
                + digest(records.natural_key(MEASUREMENTS, row)),
            )
        )
        for row in rows
    ]
    document: dict[str, JSON] = {
        "schemaVersion": 1,
        "family": "measurement",
        "sourceId": source_id,
        "sourceSha256": expected_source_sha256,
        "csv": raw.decode("utf-8"),
        "recordIds": cast(list[JSON], ids),
    }
    content = encode(document)
    if len(content) > MAX_BYTES:
        raise ServiceError(413, "import_snapshot_too_large")
    output = _path(output)
    if output.exists():
        raise ServiceError(409, "import_output_exists")
    disk_required(output.parent, len(content))
    with tempfile.TemporaryDirectory(prefix=".export-", dir=output.parent) as folder:
        staged = Path(folder) / "snapshot.json"
        atomic_bytes(staged, content)
        os.link(staged, output, follow_symlinks=False)
        fsync_path(output.parent)
    return {
        "exported": True,
        "records": len(rows),
        "snapshotSha256": hashlib.sha256(content).hexdigest(),
    }


def _snapshot(raw: bytes) -> tuple[dict[str, JSON], list[dict[str, str]], list[str]]:
    value = decode(raw, limit=MAX_BYTES)
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schemaVersion", "family", "sourceId", "sourceSha256", "csv", "recordIds"}
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["family"] != "measurement"
        or not isinstance(value["csv"], str)
        or not isinstance(value["sourceSha256"], str)
        or not SHA256.fullmatch(value["sourceSha256"])
    ):
        raise ServiceError(422, "import_invalid_snapshot")
    source_id = identifier(value["sourceId"])
    if hashlib.sha256(value["csv"].encode()).hexdigest() != value["sourceSha256"]:
        raise ServiceError(409, "import_input_changed")
    rows = _rows(value["csv"])
    ids = [
        str(
            uuid5(
                NAMESPACE_URL,
                "health-buddy:legacy:"
                + source_id
                + ":"
                + digest(records.natural_key(MEASUREMENTS, row)),
            )
        )
        for row in rows
    ]
    if value["recordIds"] != ids:
        raise ServiceError(422, "import_invalid_record_ids")
    return value, rows, ids


def _no_health(_key: str, _effect: dict[str, JSON]) -> None:
    raise ServiceError(422, "import_healthkit_not_supported")


def import_measurements(
    target: Path, snapshot: Path, *, expected_snapshot_sha256: str
) -> dict[str, Any]:
    """Publish only a new workspace; a matching unchanged owned repeat is inert."""
    raw = _read(snapshot, expected_snapshot_sha256)
    value, rows, ids = _snapshot(raw)
    provenance: dict[str, JSON] = {
        "schemaVersion": 1,
        "family": "measurement",
        "sourceId": value["sourceId"],
        "sourceSha256": value["sourceSha256"],
        "snapshotSha256": expected_snapshot_sha256,
        "records": len(rows),
    }
    return adopt_snapshot(
        target,
        raw,
        provenance,
        {MEASUREMENTS: str(value["csv"])},
        {
            record_id: (MEASUREMENTS, row)
            for record_id, row in zip(ids, rows, strict=True)
        },
    )


def adopt_snapshot(
    target: Path,
    raw: bytes,
    provenance: dict[str, JSON],
    seeded_files: dict[str, str],
    identities: dict[str, tuple[str, dict[str, str]]],
    *,
    preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The shared create-only journal path for explicitly validated CSV families."""
    target = _path(target)
    count = len(identities)
    with exclusive(target.parent / ("." + target.name + ".import.lock")):
        if target.exists():
            native_directory(target)
            private_directory(target)
            # Do not initialize/adopt arbitrary occupied destinations.
            receipt = target / "operations/import-receipt.json"
            if not receipt.exists() or decode(read_file(receipt, 4096)) != provenance:
                raise ServiceError(409, "import_destination_occupied")
            settings = config.load(target)
            if preferences is not None and settings.values != preferences:
                raise ServiceError(409, "import_destination_preferences_changed")
            manual = ManualStore(
                Store(settings.storage("manual"), settings.path("operations"))
            )
            journal = Journal(target, manual, _no_health)
            with exclusive(settings.path("operations/manual.lock")):
                state = journal.verify()
                _head, files = manual.snapshot()
                index = records.load_object(files, RECORD_INDEX)
                if (
                    state.revision != 0
                    or any(files.get(path) != csv for path, csv in seeded_files.items())
                    or set(index) != set(identities)
                    or files.get(RECEIPT) != encode(provenance).decode() + "\n"
                ):
                    raise ServiceError(409, "import_destination_changed")
            return {
                "imported": True,
                "duplicate": True,
                "records": count,
                "dataRevision": state.revision,
            }
        disk_required(target.parent, len(raw) * 4)
        with tempfile.TemporaryDirectory(
            prefix=".import-", dir=target.parent
        ) as folder:
            staged = Path(folder) / "workspace"
            settings = initialize(staged)
            if preferences is not None:
                atomic_bytes(staged / "config.json", encode(preferences))
                settings = config.load(staged)
            manual = ManualStore(
                Store(settings.storage("manual"), settings.path("operations"))
            )

            journal = Journal(staged, manual, _no_health)
            received = datetime.now(UTC).isoformat().replace("+00:00", "Z")

            def adopt(files: dict[str, str]) -> dict[str, str]:
                seeded = files | seeded_files
                changes = records.adopt(seeded, received)
                index = records.load_object(changes, RECORD_INDEX)
                by_locator = {
                    (str(entry["path"]), str(entry["locator"])): entry
                    for entry in index.values()
                    if isinstance(entry, dict)
                }
                stable: dict[str, JSON] = {
                    record_id: by_locator[(path, digest(row))]
                    for record_id, (path, row) in identities.items()
                }
                return (
                    changes
                    | seeded_files
                    | {
                        RECORD_INDEX: encode(stable).decode() + "\n",
                        RECEIPT: encode(provenance).decode() + "\n",
                    }
                )

            with exclusive(settings.path("operations/manual.lock")):
                journal.bootstrap(adopt)
                state = journal.verify()
            Service(staged)  # Reopen through the normal canonical owner.
            atomic_bytes(staged / "operations/import-receipt.json", encode(provenance))
            fsync_path(staged)
            if target.exists():
                raise ServiceError(409, "import_destination_occupied")
            staged.rename(target)
            fsync_path(target.parent)
    return {
        "imported": True,
        "duplicate": False,
        "records": count,
        "dataRevision": state.revision,
    }
