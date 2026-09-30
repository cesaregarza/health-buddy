"""Explicit owner backup and staged empty-host restore, never HTTP tools."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from .backup_archive import snapshot, verified
from .backup_crypto import MAX_ARCHIVE_BYTES, read_key, seal, unseal
from .config import load
from .domain import encode, identity_value
from .durability import atomic_bytes, exclusive, fsync_path
from .extension_files import read_file
from .runtime_manifest import native_directory
from .security_api import Runtime
from .security_runtime import _private_parent, open_runtime
from .security_store import SecurityStore
from .service_api import Principal, ServiceError
from .workspace import create_file


def private_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if len(path.parts) > 1 and path.parts[1] == "mnt":
        raise ServiceError(422, "backup_requires_native_paths")
    native_directory(path.parent)
    return _private_parent(path)


def disk_required(parent: Path, needed: int) -> None:
    if shutil.disk_usage(parent).free < needed + 64 * 1024 * 1024:
        raise ServiceError(507, "backup_insufficient_disk")


def create(
    runtime: Runtime, principal: Principal, archive: Path, key_file: Path, *, confirm_quiesced: bool
) -> dict[str, Any]:
    if not confirm_quiesced:
        raise ServiceError(422, "backup_requires_quiesced_external_editors")
    archive, key_file = private_path(archive), private_path(key_file)
    root = runtime.operations.config.root
    native_directory(root)
    if archive.is_relative_to(root) or key_file.is_relative_to(root) or archive == key_file:
        raise ServiceError(422, "backup_outputs_and_key_must_be_external")
    if archive.exists():
        raise ServiceError(409, "backup_output_exists")
    key = read_key(key_file)
    with runtime.operations.backup(principal) as inventory:
        raw = snapshot(runtime.operations.config, inventory)
        manifest, _files = verified(raw)
    encrypted = seal(raw, key)
    # Verify exactly the authenticated bytes that will be published.
    verified(unseal(encrypted, key))
    disk_required(archive.parent, len(encrypted))
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=archive.parent) as folder:
        temporary = Path(folder) / "archive.hbb"
        atomic_bytes(temporary, encrypted)
        os.link(temporary, archive, follow_symlinks=False)
        fsync_path(archive.parent)
    return {"schemaVersion": 1, "verified": True, "files": len(manifest["files"]), "archiveSha256": hashlib.sha256(encrypted).hexdigest(), "cache": "regenerable_excluded"}


def restore(
    target: Path, archive: Path, key_file: Path, *, confirm_revoke_all: bool
) -> dict[str, Any]:
    if not confirm_revoke_all:
        raise ServiceError(422, "restore_requires_revoke_all_acknowledgment")
    target, archive, key_file = private_path(target), private_path(archive), private_path(key_file)
    if target.exists() or target.is_symlink():
        raise ServiceError(409, "restore_requires_absent_destination")
    if archive.is_relative_to(target) or key_file.is_relative_to(target):
        raise ServiceError(422, "restore_input_overlaps_destination")
    # Full AES-GCM authentication completes before staging any workspace file.
    encrypted = read_file(archive, MAX_ARCHIVE_BYTES + 256)
    manifest, files = verified(unseal(encrypted, read_key(key_file)))
    disk_required(target.parent, sum(len(raw) for raw in files.values()) * 2)
    # A private parent-scoped maintenance lock serializes supported restores.
    # The destination must remain absent; an existing owner's tree is never
    # emptied, merged with an archive, or replaced by a restore command.
    with exclusive(target.parent / ".health-buddy-restore.lock"):
        if target.exists() or target.is_symlink():
            raise ServiceError(409, "restore_requires_absent_destination")
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=target.parent) as folder:
            staged = Path(folder) / "workspace"
            staged.mkdir(mode=0o700)
            for relative in sorted(manifest["directories"], key=lambda name: len(Path(name).parts)):
                path = staged / relative
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
            for relative, raw in files.items():
                path = staged / relative
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            config = load(staged)
            # Regenerable cache is deliberately absent in the archive.
            config.storage("cache").mkdir(mode=0o700, parents=True, exist_ok=True)
            copied = open_runtime(staged)
            service = copied.operations
            previous = service.journal.verify()
            if identity_value(previous.identity) != manifest["identity"] or previous.revision != manifest["dataRevision"]:
                raise ServiceError(422, "backup_identity_mismatch")
            current = replace(previous.identity, restore_epoch=str(uuid4()))
            security = SecurityStore(staged)
            with exclusive(service.lock), exclusive(security.lock):
                if service.journal.receiver_binding() is not None:
                    service.check_receiver(previous.identity)
                    service.health.rotate_staged_restore(previous.identity, current)
                service.journal.rotate_staged_restore(current)
                token = security.rekey_staged_restore(previous.identity, current)
                handoff = "secrets/restored-owner-" + uuid4().hex
                create_file(config.path(handoff), token + "\n")
                atomic_bytes(config.path("operations/restore-receipt.json"), encode({
                    "schemaVersion": 1, "previousIdentity": identity_value(previous.identity),
                    "currentIdentity": identity_value(current), "dataRevision": previous.revision,
                    "policy": "all_old_credentials_invalid_require_explicit_repair_or_rotation",
                }))
            reopened = open_runtime(staged)
            if reopened.operations.journal.verify().identity != current:
                raise ServiceError(422, "restore_verification_failed")
            if reopened.operations.journal.receiver_binding() is not None:
                reopened.operations.check_receiver(current)
            for directory in sorted((path for path in staged.rglob("*") if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
                fsync_path(directory)
            fsync_path(staged)
            # Destination creation is reserved with mkdir; only this empty
            # private directory can be replaced, never another owner tree.
            target.mkdir(mode=0o700)
            try:
                os.replace(staged, target)
            except BaseException:
                target.rmdir()
                raise
            fsync_path(target.parent)
    return {"schemaVersion": 1, "restored": True, "dataRevision": previous.revision, "files": len(files), "ownerCredentialReference": handoff, "requires": ["phone_repair", "agent_grant_rotation", "connector_rekey", "pending_intent_review"]}
