"""Bounded, non-following personal-file inventory and reviewed code snapshots."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from .domain import decode, digest
from .extension_api import MAX_RUNTIME_BYTES, MAX_RUNTIME_FILES
from .service_api import JSON, ServiceError

ID = re.compile(r"[a-z][a-z0-9-]*\.[a-z][a-z0-9-]*\Z")
RUNTIME_PARTS = frozenset({"extension.json", "src", "assets", "config"})
MAX_RUNTIME_ENTRIES = 256


def bounded_children(directory: Path, limit: int) -> list[Path]:
    children: list[Path] = []
    with os.scandir(directory) as entries:
        for item in entries:
            if len(children) >= limit:
                raise ServiceError(413, "extension_inventory_too_large")
            children.append(Path(item.path))
    return sorted(children)


def extension_id(value: str) -> str:
    if not isinstance(value, str) or len(value) > 80 or not ID.fullmatch(value):
        raise ServiceError(422, "invalid_extension_id")
    return value


def private_directory(path: Path, *, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ServiceError(503, "extension_path_unavailable")


def read_file(path: Path, limit: int) -> bytes:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size > limit
        or before.st_uid != os.geteuid()
        or stat.S_IMODE(before.st_mode) & 0o077
        or before.st_nlink != 1
    ):
        raise ServiceError(422, "extension_file_invalid")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (
        len(raw) > limit
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        or path.lstat() != after
    ):
        raise ServiceError(409, "extension_files_changing")
    return raw


def read_json(path: Path, limit: int) -> JSON:
    return decode(read_file(path, limit), limit=limit)


@dataclass(frozen=True)
class FileInventory:
    files: dict[str, bytes]
    directories: tuple[str, ...]
    digest: str


def runtime_files(root: Path) -> FileInventory:
    private_directory(root)
    files: dict[str, bytes] = {}
    total = 0
    count = len(RUNTIME_PARTS)
    directories: list[str] = []

    def walk(directory: Path) -> None:
        nonlocal total, count
        private_directory(directory)
        directories.append(directory.relative_to(root).as_posix())
        children = bounded_children(directory, MAX_RUNTIME_ENTRIES - count)
        count += len(children)
        for path in children:
            relative = path.relative_to(root).as_posix()
            if len(relative) > 240 or any(ord(char) < 32 for char in relative):
                raise ServiceError(422, "extension_file_invalid")
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                if len(path.relative_to(root).parts) > 8:
                    raise ServiceError(413, "extension_inventory_too_large")
                walk(path)
            elif stat.S_ISREG(info.st_mode):
                if len(files) >= MAX_RUNTIME_FILES:
                    raise ServiceError(413, "extension_inventory_too_large")
                raw = read_file(path, MAX_RUNTIME_BYTES - total)
                total += len(raw)
                files[relative] = raw
            else:
                raise ServiceError(422, "extension_file_invalid")

    for part in sorted(RUNTIME_PARTS):
        path = root / part
        if path.is_dir() and not path.is_symlink():
            walk(path)
        elif part == "extension.json":
            raw = read_file(path, MAX_RUNTIME_BYTES - total)
            total += len(raw)
            files[part] = raw
        else:
            raise ServiceError(422, "extension_layout_invalid")
    return FileInventory(
        files,
        tuple(sorted(directories)),
        digest(
            {
                "files": {
                    name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()
                },
                "directories": sorted(directories),
            }
        ),
    )
