"""Owner-selected immutable release preflight and compatible workspace staging."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from health_buddy.backup import create, disk_required, materialize, private_path
from health_buddy.backup_archive import verified
from health_buddy.backup_crypto import MAX_ARCHIVE_BYTES, read_key, unseal
from health_buddy.core.config import load
from health_buddy.core.domain import digest, encode, identity_value
from health_buddy.core.durability import atomic_bytes, exclusive, fsync_path
from health_buddy.core.files import read_file
from health_buddy.core.operations import Service
from health_buddy.core.security_api import Runtime
from health_buddy.core.service_api import Principal, ServiceError
from health_buddy.extension_registry import Registry
from health_buddy.personal_workspace import forks_locked
from health_buddy.runtime_manifest import (
    MAX_METADATA,
    SHA256,
    ManifestError,
    _json,
    file_digest,
)
from health_buddy.runtime_release import selected_artifact
from health_buddy.security_runtime import open_runtime


def preflight(
    runtime: Runtime,
    manifest: Path,
    manifest_sha256: str,
    architecture: str,
    *,
    check_personal: bool = True,
) -> dict[str, Any]:
    if not isinstance(runtime.operations, Service):
        raise ServiceError(503, "native_coordinator_required")
    if not SHA256.fullmatch(manifest_sha256):
        raise ServiceError(422, "upgrade_requires_manifest_sha256")
    if file_digest(manifest, MAX_METADATA)[1] != manifest_sha256:
        raise ServiceError(422, "upgrade_target_digest_mismatch")
    # This checks actual image/source bytes and every supported interface,
    # without loading an image or invoking a daemon.
    try:
        artifact = selected_artifact(manifest, architecture)
    except ManifestError:
        raise ServiceError(
            422, "upgrade_release_invalid_or_unsupported_interfaces"
        ) from None
    value = _json(manifest)
    if not isinstance(value, dict):
        raise ServiceError(422, "upgrade_invalid_manifest")
    if file_digest(manifest, MAX_METADATA)[1] != manifest_sha256:
        raise ServiceError(409, "upgrade_target_changed")
    with exclusive(runtime.operations.lock):
        forks = forks_locked(runtime.operations.config, value["sourceCommit"])
    if check_personal and any(
        not isinstance(item, dict) or item.get("state") != "recorded_compatible"
        for item in forks
    ):
        raise ServiceError(409, "upgrade_core_fork_requires_rebase_and_review")
    statuses = Registry(runtime.operations.config).compatibility(extension_api=1)
    if check_personal and any(
        item.enabled and item.state != "ready" for item in statuses
    ):
        raise ServiceError(409, "upgrade_extension_requires_review_or_disable")
    return {
        "manifestSha256": manifest_sha256,
        "architecture": architecture,
        "archiveSha256": artifact.archive_sha256,
        "imageConfigDigest": artifact.config_digest,
        "sourceCommit": value["sourceCommit"],
        "packageVersion": value["packageVersion"],
        "interfaces": value["interfaces"],
    }


def freshness(inventory: dict[str, Any]) -> str:
    """Canonical revision plus content/modes, excluding ephemeral host state."""
    files = [
        {key: item[key] for key in ("path", "sha256", "mode")}
        for item in inventory["files"]
        if not item["path"].startswith(("operations/", "security/"))
    ]
    return digest(
        {
            "identity": inventory["identity"],
            "revision": inventory["dataRevision"],
            "files": sorted(files, key=lambda item: item["path"]),
        }
    )


def stage(
    runtime: Runtime,
    principal: Principal,
    manifest: Path,
    manifest_sha256: str,
    architecture: str,
    archive: Path,
    key_file: Path,
    candidate: Path,
    *,
    confirm_quiesced: bool,
) -> dict[str, Any]:
    """Prepare a schema-compatible candidate; never activate a host service."""
    target = preflight(runtime, manifest, manifest_sha256, architecture)
    candidate = private_path(candidate)
    if not isinstance(runtime.operations, Service):
        raise ServiceError(503, "native_coordinator_required")
    root = runtime.operations.config.root
    if root.is_relative_to(candidate) or candidate.is_relative_to(root):
        raise ServiceError(422, "upgrade_candidate_overlaps_workspace")
    if candidate.exists() or candidate.is_symlink():
        raise ServiceError(409, "upgrade_requires_absent_candidate")
    backup = create(
        runtime, principal, archive, key_file, confirm_quiesced=confirm_quiesced
    )
    inventory, files = verified(
        unseal(
            read_file(private_path(archive), MAX_ARCHIVE_BYTES + 256),
            read_key(private_path(key_file)),
        )
    )
    disk_required(candidate.parent, sum(len(raw) for raw in files.values()) * 2)
    with exclusive(candidate.parent / ".health-buddy-upgrade.lock"):
        if candidate.exists() or candidate.is_symlink():
            raise ServiceError(409, "upgrade_requires_absent_candidate")
        with tempfile.TemporaryDirectory(
            prefix=".upgrade-", dir=candidate.parent
        ) as folder:
            staged = Path(folder) / "workspace"
            staged.mkdir(mode=0o700)
            materialize(staged, inventory, files)
            config = load(staged)
            config.storage("cache").mkdir(mode=0o700, parents=True, exist_ok=True)
            copied = open_runtime(staged)
            if not isinstance(copied.operations, Service):
                raise ServiceError(503, "native_coordinator_required")
            state = copied.operations.journal.verify()
            if (
                identity_value(state.identity) != inventory["identity"]
                or state.revision != inventory["dataRevision"]
            ):
                raise ServiceError(422, "upgrade_candidate_identity_mismatch")
            if copied.operations.journal.receiver_binding() is not None:
                copied.operations.check_receiver(state.identity)
            receipt = {
                "schemaVersion": 1,
                "state": "staged_requires_explicit_activation",
                "target": target,
                "backupSha256": backup["archiveSha256"],
                "freshness": freshness(inventory),
                "identity": inventory["identity"],
                "dataRevision": state.revision,
                "migration": "storage_v1_no_conversion",
                "credentialPolicy": "preserve_for_compatible_upgrade",
            }
            atomic_bytes(
                config.path("operations/upgrade-receipt.json"), encode(receipt)
            )
            for directory in sorted(
                (path for path in staged.rglob("*") if path.is_dir()),
                key=lambda path: len(path.parts),
                reverse=True,
            ):
                fsync_path(directory)
            fsync_path(staged)
            candidate.mkdir(mode=0o700)
            try:
                os.replace(staged, candidate)
            except BaseException:
                candidate.rmdir()
                raise
            fsync_path(candidate.parent)
    return receipt
