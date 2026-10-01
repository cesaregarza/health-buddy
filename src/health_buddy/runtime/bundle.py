"""Create an exact, history-free source artifact from one explicit Git commit.

This is native build tooling, not a startup path or a health-authorized action.
All execution belongs to the project's serialized validation/build queue.
"""

from __future__ import annotations

import hashlib
import io
import os
import selectors
import subprocess
import tarfile
import time
import tomllib
from pathlib import Path

from health_buddy.runtime.manifest import (
    GIT_SHA,
    INTERFACES,
    MAX_BYTES,
    MAX_ENTRIES,
    MAX_FILES,
    MAX_METADATA,
    VERSION,
    ManifestError,
    canonical,
    inventory,
    native_directory,
    path_value,
    verify_source_identity,
)


def _git(repository: Path, arguments: list[str], limit: int) -> bytes:
    environment = {
        "PATH": os.defpath,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    command = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=" + os.devnull,
        "-c",
        "credential.helper=",
        "-c",
        "tar.umask=0022",
        "-C",
        str(repository),
        *arguments,
    ]
    # Fixed native Git builtins; no shell, external diff/filter or remote operation.
    child = subprocess.Popen(  # noqa: S603
        command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment
    )
    assert child.stdout is not None
    deadline = time.monotonic() + 20
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ManifestError("source_command_timeout")
                chunk = os.read(
                    child.stdout.fileno(), min(65536, limit - len(output) + 1)
                )
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ManifestError("source_command_output_limit")
        if child.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
            raise ManifestError("source_command_failed")
        return bytes(output)
    except subprocess.TimeoutExpired:
        raise ManifestError("source_command_timeout") from None
    finally:
        child.stdout.close()
        if child.poll() is None:
            child.kill()
        child.wait()


def _sync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path: Path, data: bytes, mode: int = 0o644) -> None:
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode
    )
    with os.fdopen(descriptor, "wb") as target:
        # Newly created output only; never repair input/owner-file modes.
        os.fchmod(target.fileno(), mode)
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def _directory(path: Path, mode: int) -> None:
    path.mkdir(mode=mode)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _source_directory(source: Path, path: Path) -> None:
    current = source
    for part in path.relative_to(source).parts:
        current /= part
        try:
            current.lstat()
        except FileNotFoundError:
            _directory(current, 0o755)
        else:
            # Earlier archive entries may have created this output directory.
            # Any unexpected replacement fails rather than receiving chmod.
            native_directory(current)


def create_bundle(repository: Path, revision: str, output: Path) -> Path:
    """Create only; a partial failed destination is preserved for inspection.

    The full tracked tree is the source allowlist, never a working-tree copy.
    Privacy/source audits must have accepted this exact commit before building.
    A separate image context copies only this bundle and verified dependencies.
    """
    native_directory(repository)
    native_directory(output.parent)
    try:
        output.lstat()
    except FileNotFoundError:
        exists = False
    else:
        exists = True
    if not GIT_SHA.fullmatch(revision) or exists:
        raise ManifestError("explicit_commit_and_new_output_required")
    if output.is_relative_to(repository) or repository.is_relative_to(output):
        raise ManifestError("source_output_overlap")
    commit = _rev_parse(repository, revision + "^{commit}")
    tree = _rev_parse(repository, revision + "^{tree}")
    if commit != revision or not GIT_SHA.fullmatch(tree):
        raise ManifestError("source_revision_mismatch")
    selected, all_paths = _tree_inventory(repository, revision)
    archive = _git(repository, ["archive", "--format=tar", revision], MAX_BYTES)
    _directory(output, 0o700)
    source = output / "source"
    release = output / "release"
    _directory(source, 0o755)
    _directory(release, 0o755)
    _sync(output.parent)
    _write(release / "source.tar", archive)
    _extract_archive(archive, source, selected, all_paths)
    version = _package_version(source)
    files = inventory(source)
    docs = [item for item in files if str(item["path"]).startswith("docs/")]
    manifest = {
        "manifestVersion": 1,
        "packageVersion": version,
        "source": {
            "commit": commit,
            "tree": tree,
            "archiveSha256": hashlib.sha256(archive).hexdigest(),
        },
        "archiveBytes": len(archive),
        "files": files,
        "docsSha256": hashlib.sha256(canonical(docs)).hexdigest(),
        "interfaces": INTERFACES,
    }
    raw_manifest = canonical(manifest)
    if len(raw_manifest) > MAX_METADATA:
        raise ManifestError("manifest_size_exceeded")
    target = release / "source-manifest.json"
    _write(target, raw_manifest + b"\n")
    # Every published source link is durable before the complete output returns.
    for directory, _subdirs, _files in os.walk(
        source, topdown=False, followlinks=False
    ):
        _sync(Path(directory))
    _sync(release)
    _sync(output)
    verify_source_identity(source, target)
    return target


def _rev_parse(repository: Path, name: str) -> str:
    return (
        _git(repository, ["rev-parse", "--verify", name], 128).decode("ascii").strip()
    )


def _tree_inventory(
    repository: Path, revision: str
) -> tuple[dict[str, tuple[int, str, int]], set[str]]:
    """Each tracked regular file's (size, blob, mode), and every path it implies."""
    raw_entries = _git(
        repository, ["ls-tree", "-r", "-l", "-z", revision], MAX_METADATA
    )
    selected: dict[str, tuple[int, str, int]] = {}
    all_paths: set[str] = set()
    total = 0
    for entry in raw_entries.split(b"\x00"):
        if not entry:
            continue
        fields, name = entry.split(b"\t", 1)
        mode, kind, object_id, length = fields.split()
        if mode not in (b"100644", b"100755") or kind != b"blob":
            raise ManifestError("unsupported_source_entry")
        relative = path_value(name.decode("ascii"))
        size = int(length)
        if relative in selected or len(selected) >= MAX_FILES or size < 0:
            raise ManifestError("invalid_source_inventory")
        blob = object_id.decode("ascii")
        if not GIT_SHA.fullmatch(blob):
            raise ManifestError("invalid_source_object")
        selected[relative] = (size, blob, int(mode, 8) & 0o777)
        parts = relative.split("/")
        all_paths.update("/".join(parts[:count]) for count in range(1, len(parts) + 1))
        if len(all_paths) > MAX_ENTRIES:
            raise ManifestError("bundle_entries_exceeded")
        total += size
        if total > MAX_BYTES:
            raise ManifestError("bundle_size_exceeded")
    if not selected:
        raise ManifestError("empty_source_inventory")
    return selected, all_paths


def _extract_archive(
    archive: bytes,
    source: Path,
    selected: dict[str, tuple[int, str, int]],
    all_paths: set[str],
) -> None:
    """Write exactly the inventoried files, each checked against its Git blob."""
    seen: set[str] = set()
    archive_entries = 0
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as content:
        for member in content:
            archive_entries += 1
            if archive_entries > MAX_ENTRIES:
                raise ManifestError("bundle_entries_exceeded")
            relative = path_value(
                member.name.rstrip("/") if member.isdir() else member.name
            )
            path = source / relative
            if member.isdir():
                if relative not in all_paths or relative in selected:
                    raise ManifestError("archive_inventory_mismatch")
                _source_directory(source, path)
                continue
            expected = selected.get(relative)
            if (
                not member.isfile()
                or relative in seen
                or expected is None
                or member.size != expected[0]
            ):
                raise ManifestError("archive_inventory_mismatch")
            seen.add(relative)
            _source_directory(source, path.parent)
            stream = content.extractfile(member)
            if stream is None:
                raise ManifestError("archive_inventory_mismatch")
            with stream:
                raw = stream.read(member.size + 1)
            if len(raw) != member.size:
                raise ManifestError("archive_inventory_mismatch")
            # Git blob SHA-1 is object identity, not a password/security digest.
            actual_blob = hashlib.sha1(
                b"blob " + str(len(raw)).encode("ascii") + b"\x00" + raw,
                usedforsecurity=False,
            ).hexdigest()
            if actual_blob != expected[1] or member.mode & 0o777 != expected[2]:
                raise ManifestError("archive_object_mismatch")
            _write(path, raw, expected[2])
    if seen != selected.keys():
        raise ManifestError("archive_inventory_mismatch")


def _package_version(source: Path) -> str:
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    version = project["version"]
    if (
        project["name"] != "health-buddy"
        or not isinstance(version, str)
        or not VERSION.fullmatch(version)
    ):
        raise ManifestError("invalid_package_metadata")
    return version
