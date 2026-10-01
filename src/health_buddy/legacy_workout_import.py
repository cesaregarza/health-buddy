"""Retained session/set pairs, validated together before create-only adoption."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from health_buddy import config, loggers, records
from health_buddy.backup import disk_required
from health_buddy.core.git_store import csv_text, headers, parse_csv
from health_buddy.domain import decode, digest, encode, identifier
from health_buddy.durability import atomic_bytes, fsync_path
from health_buddy.legacy_import import (
    MAX_BYTES,
    MAX_RECORDS,
    _path,
    _read,
    adopt_snapshot,
)
from health_buddy.runtime_manifest import SHA256
from health_buddy.service_api import JSON, ServiceError

SESSIONS = "data/sessions.csv"
SETS = "data/sets.csv"
FAMILY = "workout-sessions-sets"


def _rows(
    files: dict[str, str], source_id: str
) -> dict[str, tuple[str, dict[str, str]]]:
    sessions = parse_csv(files[SESSIONS], headers()[SESSIONS])
    sets = parse_csv(files[SETS], headers()[SETS])
    if not sessions or not sets or len(sessions) + len(sets) > MAX_RECORDS:
        raise ServiceError(422, "import_requires_bounded_session_set_pair")
    settings = config.Config(Path("/nonexistent"), config.defaults())
    parents: dict[str, dict[str, str]] = {}
    identities: dict[str, tuple[str, dict[str, str]]] = {}
    for row in sessions:
        session_id = identifier(row["session_id"])
        if not row["status"]:
            raise ServiceError(422, "import_invalid_session_status")
        if session_id in parents:
            raise ServiceError(409, "import_duplicate_natural_key")
        fields: dict[str, JSON] = {
            loggers.camel(key): value for key, value in row.items() if value
        }
        # The retained dashboard also writes partial sessions. Validate its
        # remaining fields through the shipped start validator without rewriting.
        if fields.get("status") == "partial":
            fields["status"] = "complete"
        loggers.validate_input("workout-start", fields, settings)
        loggers.transition(
            "workout-start",
            {"sourceId": "manual", "fields": fields},
            {SESSIONS: csv_text(headers()[SESSIONS], [])},
            settings,
        )
        records.row_time(row, settings.zone.key)
        parents[session_id] = row
        identities[session_id] = (SESSIONS, row)
    seen: set[str] = set()
    for row in sets:
        if not row["status"] or not row["form_quality"]:
            raise ServiceError(422, "import_invalid_set_status")
        parent = parents.get(row["session_id"])
        if parent is None or row["session_date"] != parent["date"]:
            raise ServiceError(409, "import_set_parent_mismatch")
        key = digest(records.natural_key(SETS, row))
        if key in seen:
            raise ServiceError(409, "import_duplicate_natural_key")
        seen.add(key)
        fields = {
            loggers.camel("date" if name == "session_date" else name): value
            for name, value in row.items()
            if value
        }
        loggers.validate_input("workout-set", fields, settings)
        loggers.transition(
            "workout-set",
            {"sourceId": "manual", "fields": fields},
            {SESSIONS: files[SESSIONS]},
            settings,
        )
        records.row_time(row, settings.zone.key)
        record_id = str(
            uuid5(
                NAMESPACE_URL,
                "health-buddy:legacy:" + source_id + ":workout-set:" + key,
            )
        )
        if record_id in identities:
            raise ServiceError(409, "import_record_id_collision")
        identities[record_id] = (SETS, row)
    return identities


def export_workouts(
    sessions: Path,
    sets: Path,
    output: Path,
    *,
    source_id: str,
    expected_sessions_sha256: str,
    expected_sets_sha256: str,
) -> dict[str, Any]:
    identifier(source_id)
    raw_sessions = _read(sessions, expected_sessions_sha256)
    raw_sets = _read(sets, expected_sets_sha256)
    if len(raw_sessions) + len(raw_sets) > MAX_BYTES:
        raise ServiceError(413, "import_snapshot_too_large")
    try:
        files = {SESSIONS: raw_sessions.decode(), SETS: raw_sets.decode()}
    except UnicodeError:
        raise ServiceError(422, "import_invalid_csv_encoding") from None
    identities = _rows(files, source_id)
    hashes = {SESSIONS: expected_sessions_sha256, SETS: expected_sets_sha256}
    document: dict[str, JSON] = {
        "schemaVersion": 1,
        "family": FAMILY,
        "sourceId": source_id,
        "files": cast(dict[str, JSON], files),
        "sourceHashes": cast(dict[str, JSON], hashes),
        "recordIds": cast(list[JSON], sorted(identities)),
    }
    content = encode(document)
    if len(content) > MAX_BYTES:
        raise ServiceError(413, "import_snapshot_too_large")
    output = _path(output)
    if output.exists():
        raise ServiceError(409, "import_output_exists")
    # Both reviewed inputs must still agree before publishing the paired export.
    _read(sessions, expected_sessions_sha256)
    _read(sets, expected_sets_sha256)
    disk_required(output.parent, len(content))
    with tempfile.TemporaryDirectory(prefix=".export-", dir=output.parent) as folder:
        staged = Path(folder) / "snapshot.json"
        atomic_bytes(staged, content)
        os.link(staged, output, follow_symlinks=False)
        fsync_path(output.parent)
    return {
        "exported": True,
        "records": len(identities),
        "snapshotSha256": hashlib.sha256(content).hexdigest(),
    }


def import_workouts(
    target: Path, snapshot: Path, *, expected_snapshot_sha256: str
) -> dict[str, Any]:
    raw = _read(snapshot, expected_snapshot_sha256)
    value, selected, identities = _snapshot(raw)
    source_id = str(value["sourceId"])
    hashes = value["sourceHashes"]
    provenance: dict[str, JSON] = {
        "schemaVersion": 1,
        "family": FAMILY,
        "sourceId": source_id,
        "sourceHashes": hashes,
        "snapshotSha256": expected_snapshot_sha256,
        "records": len(identities),
    }
    return adopt_snapshot(target, raw, provenance, selected, identities)


def _snapshot(
    raw: bytes,
) -> tuple[dict[str, JSON], dict[str, str], dict[str, tuple[str, dict[str, str]]]]:
    value = decode(raw, limit=MAX_BYTES)
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schemaVersion", "family", "sourceId", "files", "sourceHashes", "recordIds"}
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["family"] != FAMILY
    ):
        raise ServiceError(422, "import_invalid_snapshot")
    source_id = identifier(value["sourceId"])
    files, hashes = value["files"], value["sourceHashes"]
    if (
        not isinstance(files, dict)
        or set(files) != {SESSIONS, SETS}
        or not isinstance(hashes, dict)
        or set(hashes) != {SESSIONS, SETS}
    ):
        raise ServiceError(422, "import_invalid_snapshot")
    selected: dict[str, str] = {}
    for path in (SESSIONS, SETS):
        csv, reviewed = files[path], hashes[path]
        if (
            not isinstance(csv, str)
            or not isinstance(reviewed, str)
            or not SHA256.fullmatch(reviewed)
        ):
            raise ServiceError(422, "import_invalid_snapshot")
        if hashlib.sha256(csv.encode()).hexdigest() != reviewed:
            raise ServiceError(409, "import_input_changed")
        selected[path] = csv
    identities = _rows(selected, source_id)
    if value["recordIds"] != sorted(identities):
        raise ServiceError(422, "import_invalid_record_ids")
    return value, selected, identities
