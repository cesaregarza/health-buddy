"""Compose reviewed manual, receiver and nightly exports in one isolated canary."""

from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from pathlib import Path
from typing import Any, cast

from health_buddy.backup.archive import MANIFEST, snapshot, verified
from health_buddy.backup.crypto import MAX_ARCHIVE_BYTES, read_key, unseal
from health_buddy.backup.lifecycle import private_path
from health_buddy.core import config
from health_buddy.core.domain import decode, digest, encode
from health_buddy.core.durability import atomic_bytes, exclusive, fsync_path
from health_buddy.core.files import private_directory, read_file
from health_buddy.core.operations import Service
from health_buddy.core.policy import DEVELOPMENT_PRINCIPAL, DevelopmentPolicy
from health_buddy.core.security_api import Runtime
from health_buddy.core.service_api import Principal, ServiceError
from health_buddy.legacy_import import MAX_BYTES, MAX_RECORDS, _path, _read
from health_buddy.legacy_manual_canary import import_manual_canary
from health_buddy.legacy_receiver_import import _checked, seed_adopted_receiver
from health_buddy.legacy_sleepiq_import import daily_export
from health_buddy.runtime.manifest import native_directory

RECEIPT = "operations/unified-import.json"
ORIGINAL = "stores/imported-sleepiq-nightly.csv"


def _fingerprint(manifest: dict[str, Any]) -> str:
    return digest(
        {
            "identity": manifest["identity"],
            "dataRevision": manifest["dataRevision"],
            "files": sorted(
                (
                    {key: entry[key] for key in ("path", "bytes", "sha256", "mode")}
                    for entry in manifest["files"]
                    if entry["path"] != RECEIPT
                ),
                key=lambda entry: entry["path"],
            ),
        }
    )


def _current(service: Service, principal: Principal) -> str:
    with service.backup(principal) as inventory:
        raw = snapshot(service.config, inventory)
    # This is an internally generated reconciliation inventory. An isolated
    # canary has no security authority yet; full backup verification correctly
    # requires that separate owner's authority/epoch and remains unchanged.
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        manifest = cast(
            dict[str, Any],
            decode(archive.read(MANIFEST), limit=2 * 1024 * 1024, trusted=True),
        )
    return _fingerprint(manifest)


def import_canary(
    target: Path,
    manual: Path,
    receiver: Path,
    sleepiq: Path,
    *,
    reviewed_hashes: dict[str, str],
    sleeper_id: str,
    timezone: str,
) -> dict[str, Any]:
    if set(reviewed_hashes) != {"manual", "receiver", "sleepiq"}:
        raise ServiceError(422, "import_requires_explicit_canary_inputs")
    inputs = {"manual": manual, "receiver": receiver, "sleepiq": sleepiq}
    raw = {name: _read(path, reviewed_hashes[name]) for name, path in inputs.items()}
    if sum(map(len, raw.values())) > MAX_BYTES:
        raise ServiceError(413, "import_snapshot_too_large")
    try:
        receiver_value = cast(
            dict[str, Any], _checked(decode(raw["receiver"], limit=MAX_BYTES))
        )
        daily, sleep_count = daily_export(
            raw["sleepiq"].decode(), sleeper_id=sleeper_id, timezone=timezone
        )
    except (UnicodeError, ValueError, TypeError, KeyError):
        raise ServiceError(422, "import_invalid_snapshot") from None
    selection: dict[str, Any] = {
        "schemaVersion": 1,
        "family": "unified-canary",
        "sourceHashes": reviewed_hashes,
        "sleepiqMapping": {
            "sleeperId": sleeper_id,
            "timezone": timezone,
            "datePolicy": "aware-session-end-local-wake-date",
        },
    }
    target = _path(target)
    with exclusive(target.parent / ("." + target.name + ".import.lock")):
        if target.exists():
            native_directory(target)
            private_directory(target)
            try:
                native_directory(target / "operations")
                receipt = cast(
                    dict[str, Any], decode(read_file(target / RECEIPT, 16384))
                )
            except FileNotFoundError:
                raise ServiceError(409, "import_destination_occupied") from None
            if not isinstance(receipt, dict) or receipt.get("selection") != selection:
                raise ServiceError(409, "import_destination_occupied")
            service = Service(target, DevelopmentPolicy())
            if _current(service, DEVELOPMENT_PRINCIPAL) != receipt.get(
                "workspaceDigest"
            ):
                raise ServiceError(409, "import_destination_changed")
            return dict(receipt["reconciliation"], duplicate=True)
        with tempfile.TemporaryDirectory(
            prefix=".unified-import-", dir=target.parent
        ) as folder:
            staged = Path(folder) / "workspace"
            manual_result = import_manual_canary(
                staged, manual, expected_snapshot_sha256=reviewed_hashes["manual"]
            )
            receiver_count = len(receiver_value["records"])
            if manual_result["records"] + receiver_count + sleep_count > MAX_RECORDS:
                raise ServiceError(413, "import_snapshot_too_large")
            settings = config.load(staged).values
            if settings["timezone"] != timezone:
                raise ServiceError(409, "import_sleepiq_timezone_conflict")
            settings["integrations"]["healthkit"] = {
                "enabled": True,
                "mode": "receiver",
            }
            settings["integrations"]["sleepiq"]["enabled"] = True
            atomic_bytes(staged / "config.json", encode(settings))
            atomic_bytes(
                staged / settings["integrations"]["sleepiq"]["exportFile"],
                daily.encode(),
            )
            atomic_bytes(staged / ORIGINAL, raw["sleepiq"])
            service = Service(staged, DevelopmentPolicy())
            seed_adopted_receiver(service, receiver_value, reviewed_hashes["receiver"])
            state = service.journal.verify()
            reconciliation = {
                "imported": True,
                "duplicate": False,
                "manualRecords": manual_result["records"],
                "receiverRecords": receiver_count,
                "receiverTombstones": len(receiver_value["tombstones"]),
                "receiverBatches": len(receiver_value["batches"]),
                "sleepiqRecords": sleep_count,
                "sourceHashes": reviewed_hashes,
                "dataRevision": state.revision,
                "cutoverReady": False,
                "backup": "separate_owner_guard_required",
            }
            workspace_digest = _current(service, DEVELOPMENT_PRINCIPAL)
            atomic_bytes(
                staged / RECEIPT,
                encode(
                    {
                        "selection": selection,
                        "workspaceDigest": workspace_digest,
                        "reconciliation": reconciliation,
                    }
                ),
            )
            # Every explicitly selected input is still exactly the reviewed bytes.
            for name, path in inputs.items():
                _read(path, reviewed_hashes[name])
            fsync_path(staged)
            if target.exists():
                raise ServiceError(409, "import_destination_occupied")
            staged.rename(target)
            fsync_path(target.parent)
            return reconciliation


def backup_readiness(
    runtime: Runtime,
    principal: Principal,
    archive: Path,
    key_file: Path,
    *,
    expected_archive_sha256: str,
) -> dict[str, Any]:
    """Separate owner guard; never performs or authorizes deployment/cutover."""
    if not isinstance(runtime.operations, Service):
        raise ServiceError(503, "native_coordinator_required")
    archive, key_file = private_path(archive), private_path(key_file)
    with runtime.operations.backup(principal) as inventory:
        encrypted = read_file(archive, MAX_ARCHIVE_BYTES)
        if hashlib.sha256(encrypted).hexdigest() != expected_archive_sha256:
            raise ServiceError(409, "import_backup_changed")
        manifest, files = verified(unseal(encrypted, read_key(key_file)))
        receipt = read_file(runtime.operations.config.root / RECEIPT, 16384)
        if files.get(RECEIPT) != receipt:
            raise ServiceError(409, "import_backup_canary_conflict")
        current_manifest, current_files = verified(
            snapshot(runtime.operations.config, inventory)
        )
        if (
            _fingerprint(manifest) != _fingerprint(current_manifest)
            or files != current_files
        ):
            raise ServiceError(409, "import_backup_stale")
    return {
        "backupVerified": True,
        "archiveSha256": expected_archive_sha256,
        "dataRevision": manifest["dataRevision"],
        "cutoverPerformed": False,
        "realPhoneQualification": "pending",
    }
