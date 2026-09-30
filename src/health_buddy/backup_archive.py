"""Locked whole-workspace snapshot and bounded authenticated inventory reader."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import zipfile
from pathlib import Path
from typing import Any, cast

from .backup_crypto import MAX_ARCHIVE_BYTES
from .config import Config, relative_path
from .domain import decode, encode, identity_value
from .extension_files import read_file
from .operations import BackupInventory
from .service_api import ServiceError

MAX_ENTRIES = 8192
MANIFEST = "backup-manifest.json"


def snapshot(config: Config, inventory: BackupInventory) -> bytes:
    """Caller holds backup context and has quiesced external editors/importers."""
    root = inventory.workspace
    cache = config.storage("cache")
    socket_path = config.path(config.values["security"]["socketPath"])
    entries: list[dict[str, Any]] = []
    directories: list[str] = []
    output = io.BytesIO()
    total = 0
    observed_directories: dict[str, int] = {}

    def walk(directory: Path) -> None:
        nonlocal total
        if len(entries) + len(directories) >= MAX_ENTRIES:
            raise ServiceError(413, "backup_entry_limit")
        observed_directories[str(directory.relative_to(root))] = directory.lstat().st_mtime_ns
        for path in sorted(directory.iterdir()):
            if path == cache:
                continue
            info = path.lstat()
            relative = str(path.relative_to(root))
            if path == socket_path and stat.S_ISSOCK(info.st_mode):
                continue
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & ~0o700:
                raise ServiceError(422, "backup_requires_private_owned_workspace")
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
                walk(path)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > MAX_ARCHIVE_BYTES - 2 * 1024 * 1024:
                    raise ServiceError(413, "backup_size_limit")
                raw = read_file(path, MAX_ARCHIVE_BYTES)
                archive.writestr("workspace/" + relative, raw)
                entries.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "mtimeNs": info.st_mtime_ns, "mode": stat.S_IMODE(info.st_mode)})
            else:
                raise ServiceError(422, "backup_unsupported_workspace_entry")
            if len(entries) + len(directories) > MAX_ENTRIES:
                raise ServiceError(413, "backup_entry_limit")

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        walk(root)
        required = [str(path.relative_to(root)) for path in inventory.required_paths]
        if config.enabled("healthkit"):
            required.append(str(config.storage("healthkit").relative_to(root)))
        if config.enabled("sleepiq"):
            required.append(config.values["integrations"]["sleepiq"]["exportFile"])
        manifest = {
            "schemaVersion": 1,
            "identity": identity_value(inventory.identity),
            "dataRevision": inventory.data_revision,
            "files": entries,
            "directories": directories,
            "requiredPaths": required,
            "regenerableCache": str(cache.relative_to(root)),
        }
        archive.writestr(MANIFEST, encode(manifest))
        # Detect unsupported editors changing captured files despite the writer
        # lock. Canonical writers/security and supported extension edits share
        # the lock; external editing must remain quiesced by the owner.
        for relative, stamp in observed_directories.items():
            if (root / relative).lstat().st_mtime_ns != stamp:
                raise ServiceError(409, "backup_workspace_changed")
        for entry in entries:
            path = config.path(entry["path"])
            info = path.lstat()
            if info.st_mtime_ns != entry["mtimeNs"] or stat.S_IMODE(info.st_mode) != entry["mode"] or hashlib.sha256(read_file(path, MAX_ARCHIVE_BYTES)).hexdigest() != entry["sha256"]:
                raise ServiceError(409, "backup_workspace_changed")
    return output.getvalue()


def verified(raw: bytes) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Only called after envelope authentication; no extraction API is used."""
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ServiceError(413, "backup_size_limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) > MAX_ENTRIES + 1 or len(names) != len(set(names)):
                raise ValueError("entries")
            if sum(item.file_size for item in infos) > MAX_ARCHIVE_BYTES:
                raise ValueError("size")
            manifest = decode(archive.read(MANIFEST), limit=2 * 1024 * 1024, trusted=True)
            if not isinstance(manifest, dict) or set(manifest) != {"schemaVersion", "identity", "dataRevision", "files", "directories", "requiredPaths", "regenerableCache"}:
                raise ValueError("manifest")
            manifest = cast(dict[str, Any], manifest)
            if manifest["schemaVersion"] != 1 or type(manifest["dataRevision"]) is not int or manifest["dataRevision"] < 0:
                raise ValueError("version")
            files: dict[str, bytes] = {}
            if not isinstance(manifest["files"], list) or not isinstance(manifest["directories"], list) or not isinstance(manifest["requiredPaths"], list):
                raise ValueError("inventory")
            directories = [relative_path(item, "directory") for item in manifest["directories"]]
            if len(directories) != len(set(directories)):
                raise ValueError("duplicate")
            for entry in manifest["files"]:
                if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256", "mtimeNs", "mode"}:
                    raise ValueError("file")
                if type(entry["mode"]) is not int or not 0 <= entry["mode"] <= 0o700 or entry["mode"] & ~0o700:
                    raise ValueError("unsafe_file_mode")
                relative = relative_path(entry["path"], "file")
                if relative in files or relative in directories:
                    raise ValueError("duplicate")
                content = archive.read("workspace/" + relative)
                if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                    raise ValueError("hash")
                files[relative] = content
            if set(names) != {MANIFEST, *("workspace/" + path for path in files)}:
                raise ValueError("inventory")
            for required in manifest["requiredPaths"]:
                path = relative_path(required, "required")
                if path not in files and path not in directories:
                    raise ValueError("incomplete")
            # These are always authoritative, independent of an archive's own
            # list of required paths. Canonical adapters verify their contents.
            if not {"config.json", "identity.json", "operations/control.sqlite", "security/authority.sqlite", "security/epoch.json", "operations/security-binding.json"} <= files.keys() or "personal" not in directories:
                raise ValueError("incomplete")
            relative_path(manifest["regenerableCache"], "cache")
            return manifest, files
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, ServiceError):
        raise ServiceError(422, "backup_inventory_invalid") from None
