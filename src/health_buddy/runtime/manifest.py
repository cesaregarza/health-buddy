"""Bounded source-bundle verification, separate from external image identity."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

from health_buddy.core.release_identity import ReleaseIdentity

MAX_FILES = 4096
MAX_ENTRIES = 8192
MAX_BYTES = 64 * 1024 * 1024
MAX_METADATA = 2 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"[0-9][A-Za-z0-9.+-]{0,63}\Z")
INTERFACES = {"api": 1, "storage": 1, "extensions": 1, "pairing": 1, "phonePayload": 1}
# CPython caches bytecode beside the source it imports, and the documented
# installer commands import the extracted bundle in place (PYTHONPATH=$SOURCE/src).
# That cache is interpreter state, never release content.
BYTECODE_DIRECTORY = "__pycache__"
BYTECODE_SUFFIXES = (".pyc", ".pyo")


class ManifestError(ValueError):
    """A fixed safe diagnostic; the message never includes a path or file value."""


class SourceInventoryMismatch(ManifestError):
    """The source tree differs from its manifest, first at `path`.

    `path` is bundle-relative and already admitted by path_value, so an operator
    report may name it; the message stays the fixed code for existing handlers.
    """

    def __init__(self, path: str) -> None:
        super().__init__("source_inventory_mismatch")
        self.path = path


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def path_value(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise ManifestError("invalid_bundle_path")
    parts = value.split("/")
    if (
        len(parts) > 16
        or any(part in ("", ".", "..", ".git") for part in parts)
        or "\\" in value
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ManifestError("invalid_bundle_path")
    return value


def native_directory(path: Path) -> None:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or (len(path.parts) > 1 and path.parts[1] == "mnt")
    ):
        raise ManifestError("invalid_bundle_directory")
    # Reject each ancestor before any descendant lookup can follow that link.
    for parent in (*reversed(path.parents), path):
        details = parent.lstat()
        if not stat.S_ISDIR(details.st_mode):
            raise ManifestError("invalid_bundle_directory")


def file_digest(path: Path, limit: int) -> tuple[int, str]:
    """No following links or unbounded reads, even when metadata changes."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ManifestError("invalid_bundle_file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ManifestError("bundle_changed")
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(descriptor, min(65536, limit - total + 1)):
            total += len(chunk)
            if total > limit:
                raise ManifestError("bundle_size_exceeded")
            digest.update(chunk)
        after = os.fstat(descriptor)
        current = path.lstat()

        def fields(item: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                item.st_dev,
                item.st_ino,
                item.st_size,
                item.st_mtime_ns,
                item.st_ctime_ns,
            )

        if fields(before) != fields(after) or fields(after) != fields(current):
            raise ManifestError("bundle_changed")
        return total, digest.hexdigest()
    finally:
        os.close(descriptor)


def interpreter_bytecode(relative: str) -> bool:
    """Whether inventory() skips this bundle-relative file as interpreter bytecode."""
    *directories, name = relative.split("/")
    return BYTECODE_DIRECTORY in directories or name.endswith(BYTECODE_SUFFIXES)


def _bytecode_entry(entry: os.DirEntry[str]) -> bool:
    # Only a real cache directory or regular file is skipped; a link with such a
    # name still reaches the ordinary checks and fails like any other link.
    if entry.name == BYTECODE_DIRECTORY:
        return entry.is_dir(follow_symlinks=False)
    return entry.name.endswith(BYTECODE_SUFFIXES) and entry.is_file(
        follow_symlinks=False
    )


def inventory(root: Path) -> list[dict[str, str | int]]:
    """Every source file under root, except interpreter bytecode."""
    native_directory(root)
    result: list[dict[str, str | int]] = []
    total = 0
    entries = 0

    def visit(directory: Path, depth: int) -> None:
        nonlocal total, entries
        if depth > 16:
            raise ManifestError("bundle_depth_exceeded")
        before = directory.stat()
        with os.scandir(directory) as children:
            for entry in children:
                entries += 1
                if entries > MAX_ENTRIES:
                    raise ManifestError("bundle_entries_exceeded")
                # Counted before skipping, so the entry bound still bounds the scan.
                if _bytecode_entry(entry):
                    continue
                path = Path(entry.path)
                relative = path_value(path.relative_to(root).as_posix())
                if entry.is_dir(follow_symlinks=False):
                    visit(path, depth + 1)
                else:
                    if len(result) >= MAX_FILES:
                        raise ManifestError("bundle_files_exceeded")
                    size, digest = file_digest(path, MAX_BYTES - total)
                    total += size
                    result.append({"path": relative, "bytes": size, "sha256": digest})
        after = directory.stat()
        if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ManifestError("bundle_changed")

    visit(root, 0)
    return sorted(result, key=lambda item: str(item["path"]))


def _first_difference(
    recorded: Sequence[Mapping[str, object]], present: Sequence[Mapping[str, object]]
) -> str | None:
    """The first path, in sorted order, that was added, removed or changed."""
    expected = {str(item["path"]): item for item in recorded}
    actual = {str(item["path"]): item for item in present}
    for path in sorted(expected.keys() | actual.keys()):
        if expected.get(path) != actual.get(path):
            return path
    return None


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate_manifest_field")
        result[key] = value
    return result


def _text(value: object, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ManifestError("invalid_manifest_value")
    return value


def _json(path: Path) -> object:
    native_directory(path.parent)
    size, expected = file_digest(path, MAX_METADATA)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        raw = b""
        while len(raw) <= MAX_METADATA:
            chunk = os.read(descriptor, min(65536, MAX_METADATA - len(raw) + 1))
            if not chunk:
                break
            raw += chunk
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != expected:
            raise ManifestError("bundle_changed")
        value: object = json.loads(raw, object_pairs_hook=_pairs)
        return value
    finally:
        os.close(descriptor)


def verify_source_identity(source_root: Path, manifest_path: Path) -> ReleaseIdentity:
    """Verify a complete source tree plus the adjacent exact source.tar bytes.

    File integrity is established locally. The commit association still depends
    on the trusted controlled builder receipt, not a publisher signature.
    A tree that differs from the manifest raises SourceInventoryMismatch naming
    the first differing file; interpreter bytecode is not part of the tree.
    """
    value = _json(manifest_path)
    if not isinstance(value, dict) or set(value) != {
        "manifestVersion",
        "packageVersion",
        "source",
        "archiveBytes",
        "files",
        "docsSha256",
        "interfaces",
    }:
        raise ManifestError("invalid_source_manifest")
    if type(value["manifestVersion"]) is not int or value["manifestVersion"] != 1:
        raise ManifestError("unsupported_source_manifest")
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {
        "commit",
        "tree",
        "archiveSha256",
    }:
        raise ManifestError("invalid_source_manifest")
    commit = _text(source["commit"], GIT_SHA)
    tree = _text(source["tree"], GIT_SHA)
    archive_digest = _text(source["archiveSha256"], SHA256)
    version = _text(value["packageVersion"], VERSION)
    docs_digest = _text(value["docsSha256"], SHA256)
    if (
        type(value["archiveBytes"]) is not int
        or not 1 <= value["archiveBytes"] <= MAX_BYTES
        or value["interfaces"] != INTERFACES
        or not isinstance(value["interfaces"], dict)
        or any(type(item) is not int for item in value["interfaces"].values())
    ):
        raise ManifestError("invalid_source_manifest")
    files = value["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ManifestError("invalid_source_manifest")
    paths: list[str] = []
    total = 0
    for record in files:
        if not isinstance(record, dict) or set(record) != {"path", "bytes", "sha256"}:
            raise ManifestError("invalid_source_manifest")
        paths.append(path_value(record["path"]))
        _text(record["sha256"], SHA256)
        if type(record["bytes"]) is not int or record["bytes"] < 0:
            raise ManifestError("invalid_source_manifest")
        total += record["bytes"]
        if total > MAX_BYTES:
            raise ManifestError("bundle_size_exceeded")
    if paths != sorted(set(paths)):
        raise ManifestError("invalid_source_inventory")
    differing = _first_difference(files, inventory(source_root))
    if differing is not None:
        raise SourceInventoryMismatch(differing)
    docs = [item for item in files if item["path"].startswith("docs/")]
    if hashlib.sha256(canonical(docs)).hexdigest() != docs_digest:
        raise ManifestError("docs_inventory_mismatch")
    archive_size, actual_archive = file_digest(
        manifest_path.parent / "source.tar", MAX_BYTES
    )
    if archive_size != value["archiveBytes"] or actual_archive != archive_digest:
        raise ManifestError("source_archive_mismatch")
    return ReleaseIdentity(
        version, commit, tree, archive_digest, docs_digest, "packaged_manifest"
    )


def read_source_identity(source_root: Path, manifest_path: Path) -> ReleaseIdentity:
    """Discovery fails closed to unknown; explicit verification reports errors."""
    try:
        return verify_source_identity(source_root, manifest_path)
    except (ManifestError, OSError, ValueError, TypeError, RecursionError):
        return ReleaseIdentity()
