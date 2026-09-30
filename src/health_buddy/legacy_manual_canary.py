"""One explicit combined manual canary; no receiver or private Git adoption."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from . import config, loggers, plans, records
from .backup import disk_required
from .domain import MAX_PLAN_BODY, decode, digest, encode, identifier
from .durability import atomic_bytes, fsync_path
from .legacy_import import (
    MAX_BYTES,
    MAX_RECORDS,
    MEASUREMENTS,
    _path,
    _read,
    adopt_snapshot,
)
from .legacy_import import (
    _snapshot as measurement_snapshot,
)
from .legacy_store import headers, parse_csv
from .legacy_workout_import import _snapshot as workout_snapshot
from .runtime_manifest import GIT_SHA, SHA256
from .service_api import JSON, ServiceError

FAMILY = "manual-canary"
INPUTS = ("measurements", "workouts", "intake", "plan", "preferences")
INTAKE = "data/intake.csv"
PLAN = "plans/current_program.json"


def _preferences(raw: str) -> dict[str, Any]:
    value = decode(raw, limit=16_384)
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schemaVersion", "displayName", "timezone", "goals", "equipment"}
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
    ):
        raise ServiceError(422, "import_unsupported_preferences")
    settings = config.defaults()
    settings.update(
        timezone=value["timezone"], goals=value["goals"], equipment=value["equipment"]
    )
    settings["identity"] = {"displayName": value["displayName"]}
    try:
        config.validate(settings, Path("/nonexistent"))
    except config.ConfigError:
        raise ServiceError(422, "import_unsupported_preferences") from None
    return settings


def _contents(
    value: dict[str, JSON],
) -> tuple[dict[str, str], dict[str, tuple[str, dict[str, str]]], dict[str, Any], str]:
    inputs, hashes = value.get("inputs"), value.get("sourceHashes")
    if (
        not isinstance(inputs, dict)
        or set(inputs) != set(INPUTS)
        or not isinstance(hashes, dict)
        or set(hashes) != set(INPUTS)
    ):
        raise ServiceError(422, "import_invalid_snapshot")
    text: dict[str, str] = {}
    for name in INPUTS:
        raw, reviewed = inputs[name], hashes[name]
        if (
            not isinstance(raw, str)
            or not isinstance(reviewed, str)
            or not SHA256.fullmatch(reviewed)
        ):
            raise ServiceError(422, "import_invalid_snapshot")
        if hashlib.sha256(raw.encode()).hexdigest() != reviewed:
            raise ServiceError(409, "import_input_changed")
        text[name] = raw
    settings = _preferences(text["preferences"])
    measurement, rows, ids = measurement_snapshot(text["measurements"].encode())
    workout, files, identities = workout_snapshot(text["workouts"].encode())
    source_id = identifier(measurement["sourceId"])
    if workout["sourceId"] != source_id:
        raise ServiceError(409, "import_source_namespace_conflict")
    selected = files | {MEASUREMENTS: str(measurement["csv"])}
    for record_id, row in zip(ids, rows, strict=True):
        if record_id in identities:
            raise ServiceError(409, "import_record_id_collision")
        identities[record_id] = (MEASUREMENTS, row)
    normalized_intake = records.normalize_intake({INTAKE: text["intake"]}).get(
        INTAKE, text["intake"]
    )
    intake_rows = parse_csv(normalized_intake, headers()[INTAKE])
    if not intake_rows:
        raise ServiceError(422, "import_requires_nonempty_intake")
    if len(intake_rows) + len(identities) > MAX_RECORDS:
        raise ServiceError(413, "import_snapshot_too_large")
    seen: set[str] = set()
    cfg = config.Config(Path("/nonexistent"), settings)
    for row in intake_rows:
        fields: dict[str, JSON] = {
            loggers.camel(key): raw for key, raw in row.items() if raw
        }
        loggers.validate_input("intake", fields, cfg)
        records.row_time(row, cfg.zone.key)
        key = digest(records.natural_key(INTAKE, row))
        if key in seen:
            raise ServiceError(409, "import_duplicate_natural_key")
        seen.add(key)
        record_id = str(
            uuid5(NAMESPACE_URL, "health-buddy:legacy:" + source_id + ":intake:" + key)
        )
        if record_id in identities:
            raise ServiceError(409, "import_record_id_collision")
        identities[record_id] = (INTAKE, row)
    if len(identities) > MAX_RECORDS:
        raise ServiceError(413, "import_snapshot_too_large")
    selected[INTAKE] = normalized_intake
    try:
        retained = decode(text["plan"], limit=MAX_PLAN_BODY)
        program = plans.validate_plan(plans.to_wire(retained))
    except (ValueError, TypeError, KeyError, ServiceError):
        raise ServiceError(422, "import_unsupported_plan") from None
    selected[PLAN] = encode(program).decode() + "\n"
    # Every date-only record uses the explicitly reviewed portable source zone.
    for _path_name, row in identities.values():
        records.row_time(row, cfg.zone.key)
    return selected, identities, settings, source_id


def export_manual_canary(
    inputs: dict[str, Path],
    output: Path,
    *,
    reviewed_hashes: dict[str, str],
    source_revision: str,
) -> dict[str, Any]:
    if set(inputs) != set(INPUTS) or set(reviewed_hashes) != set(INPUTS):
        raise ServiceError(422, "import_requires_explicit_manual_inputs")
    if not GIT_SHA.fullmatch(source_revision):
        raise ServiceError(422, "import_requires_declared_source_revision")
    text: dict[str, JSON] = {}
    total = 0
    for name in INPUTS:
        raw = _read(inputs[name], reviewed_hashes[name])
        total += len(raw)
        if total > MAX_BYTES:
            raise ServiceError(413, "import_snapshot_too_large")
        try:
            text[name] = raw.decode()
        except UnicodeError:
            raise ServiceError(422, "import_invalid_encoding") from None
    value: dict[str, JSON] = {
        "schemaVersion": 1,
        "family": FAMILY,
        "inputs": text,
        "sourceHashes": cast(dict[str, JSON], reviewed_hashes),
        "sourceRevision": source_revision,
        "sourceRevisionVerified": False,
    }
    _selected, identities, _settings, _source_id = _contents(value)
    content = encode(value)
    if len(content) > MAX_BYTES:
        raise ServiceError(413, "import_snapshot_too_large")
    output = _path(output)
    if output.exists():
        raise ServiceError(409, "import_output_exists")
    for name in INPUTS:
        _read(inputs[name], reviewed_hashes[name])
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


def import_manual_canary(
    target: Path, snapshot: Path, *, expected_snapshot_sha256: str
) -> dict[str, Any]:
    raw = _read(snapshot, expected_snapshot_sha256)
    value = decode(raw, limit=MAX_BYTES)
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schemaVersion",
            "family",
            "inputs",
            "sourceHashes",
            "sourceRevision",
            "sourceRevisionVerified",
        }
        or type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["family"] != FAMILY
        or value["sourceRevisionVerified"] is not False
        or not isinstance(value["sourceRevision"], str)
        or not GIT_SHA.fullmatch(value["sourceRevision"])
    ):
        raise ServiceError(422, "import_invalid_snapshot")
    files, identities, preferences, source_id = _contents(value)
    provenance: dict[str, JSON] = {
        "schemaVersion": 1,
        "family": FAMILY,
        "sourceId": source_id,
        "sourceHashes": value["sourceHashes"],
        "sourceRevision": value["sourceRevision"],
        "sourceRevisionVerified": False,
        "snapshotSha256": expected_snapshot_sha256,
        "records": len(identities),
    }
    return adopt_snapshot(
        target, raw, provenance, files, identities, preferences=preferences
    )
