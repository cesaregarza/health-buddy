"""Create-only personal installation; copy reviewed inputs, never execute them."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from uuid import uuid4

from .config import Config
from .durability import atomic_bytes, exclusive, fsync_path
from .extension_api import MAX_EXTENSIONS, MAX_MANIFEST_BYTES
from .extension_files import bounded_children, private_directory
from .extension_manifest import parse_manifest
from .service_api import ServiceError

MAX_INSTALL_FILES = 512
MAX_INSTALL_BYTES = 8_388_608


def _package(source: Path) -> tuple[str, dict[str, bytes], set[str]]:
    source = source.expanduser().absolute()
    if ".." in source.parts or any(path.is_symlink() for path in (source, *source.parents)):
        raise ServiceError(422, "extension_install_path_invalid")
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    total = 0
    count = 0

    def walk(root: Path) -> None:
        nonlocal total, count
        before = root.lstat()
        if not stat.S_ISDIR(before.st_mode):
            raise ServiceError(422, "extension_install_path_invalid")
        for path in bounded_children(root, MAX_INSTALL_FILES - count):
            relative = path.relative_to(source)
            count += 1
            if count > MAX_INSTALL_FILES or len(relative.parts) > 10 or len(str(relative)) > 240:
                raise ServiceError(413, "extension_install_too_large")
            if any(part in {".git", ".hg", ".svn"} for part in relative.parts):
                raise ServiceError(422, "extension_install_private_history")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                directories.add(relative.as_posix())
                walk(path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if info.st_size > MAX_INSTALL_BYTES - total:
                    raise ServiceError(413, "extension_install_too_large")
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as handle:
                    opened = os.fstat(handle.fileno())
                    raw = handle.read(MAX_INSTALL_BYTES - total + 1)
                    after = os.fstat(handle.fileno())
                if (
                    len(raw) > MAX_INSTALL_BYTES - total
                    or (info.st_ino, info.st_dev) != (opened.st_ino, opened.st_dev)
                    or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                    != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    or path.lstat() != after
                ):
                    raise ServiceError(409, "extension_files_changing")
                files[relative.as_posix()] = raw
                total += len(raw)
            else:
                raise ServiceError(422, "extension_install_path_invalid")
        after = root.lstat()
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            raise ServiceError(409, "extension_files_changing")

    walk(source)
    raw_manifest = files.get("extension.json", b"")
    if len(raw_manifest) > MAX_MANIFEST_BYTES:
        raise ServiceError(413, "extension_manifest_too_large")
    manifest = parse_manifest(raw_manifest)
    if not {"src", "assets", "config", "tests", "notes", "migrations", "state"} <= directories:
        raise ServiceError(422, "extension_layout_invalid")
    return manifest.id, files, directories


def install(config: Config, source: Path) -> str:
    name, files, directories = _package(source)
    with exclusive(config.path("operations/manual.lock")):
        base = config.path("personal/extensions")
        private_directory(base, create=True)
        if len(bounded_children(base, MAX_EXTENSIONS)) >= MAX_EXTENSIONS:
            raise ServiceError(413, "extension_limit")
        target = config.path("personal/extensions/" + name)
        if target.exists():
            raise ServiceError(409, "extension_already_installed")
        staging = config.path("personal/.install-" + uuid4().hex)
        staging.mkdir(mode=0o700)
        # Incomplete staging remains discoverable for deliberate recovery. No
        # cleanup recursively removes owner files after interruption.
        for relative in sorted(directories, key=lambda item: (item.count("/"), item)):
            (staging / relative).mkdir(mode=0o700)
        for relative, raw in files.items():
            atomic_bytes(staging / relative, raw)
        for relative in sorted(directories, key=lambda item: item.count("/"), reverse=True):
            fsync_path(staging / relative)
        fsync_path(staging)
        staging.rename(target)
        fsync_path(base)
        fsync_path(config.path("personal"))
        return name
