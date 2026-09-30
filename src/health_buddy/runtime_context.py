"""Assemble a create-only Docker context from verified source and local inputs."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .runtime_bundle import _directory, _source_directory, _sync, _write
from .runtime_inputs import load_inputs, verify_inputs
from .runtime_manifest import (
    ManifestError,
    canonical,
    file_digest,
    inventory,
    native_directory,
    verify_source_identity,
)


def _copy(
    source: Path, target: Path, size: int, digest: str, mode: int = 0o644
) -> None:
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(size + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise ManifestError("build_context_input_changed")
        _write(target, raw, mode)
    finally:
        os.close(descriptor)


def create_context(
    bundle: Path, downloads: Path, architecture: str, output: Path
) -> Path:
    native_directory(bundle)
    native_directory(downloads)
    native_directory(output.parent)
    if any(
        output.is_relative_to(path) or path.is_relative_to(output)
        for path in (bundle, downloads)
    ):
        raise ManifestError("build_context_overlaps_input")
    source, release = bundle / "source", bundle / "release"
    identity = verify_source_identity(source, release / "source-manifest.json")
    lock = source / "packaging/runtime-inputs.json"
    selected = load_inputs(lock, architecture)
    verify_inputs(selected, downloads)
    lock_size, lock_digest = file_digest(lock, 2 * 1024 * 1024)
    _directory(output, 0o700)
    for name in ("source", "release", "inputs", "inputs/wheels", "inputs/debs"):
        _directory(output / name, 0o755)
    for item in inventory(source):
        relative, size, digest = (
            str(item["path"]),
            int(item["bytes"]),
            str(item["sha256"]),
        )
        original, target = source / relative, output / "source" / relative
        _source_directory(output / "source", target.parent)
        _copy(original, target, size, digest, original.lstat().st_mode & 0o777)
    for name in ("source.tar", "source-manifest.json"):
        size, digest = file_digest(release / name, 64 * 1024 * 1024)
        _copy(release / name, output / "release" / name, size, digest)
    _copy(lock, output / "inputs/runtime-inputs.json", lock_size, lock_digest)
    for input_file in selected.files:
        _copy(
            downloads / input_file.filename,
            output / "inputs" / input_file.kind / input_file.filename,
            input_file.size,
            input_file.sha256,
        )
    requirements = "".join(
        f"{item.name}=={item.version} --hash=sha256:{item.sha256}\n"
        for item in selected.files
        if item.kind == "wheels"
    )
    _write(output / "inputs/requirements.txt", requirements.encode("ascii"))
    substitutions = {
        "@BASE_IMAGE@": selected.base_image,
        "@ARCH@": architecture,
        "@COMMIT@": identity.source_commit,
        "@VERSION@": identity.package_version,
        "@SOURCE_ARCHIVE@": identity.source_archive_sha256,
        "@INPUT_LOCK@": lock_digest,
    }
    dockerfile = (source / "packaging/runtime.Dockerfile").read_text()
    for key, value in substitutions.items():
        if value is None:
            raise ManifestError("build_source_identity_missing")
        dockerfile = dockerfile.replace(key, value)
    if "@" in dockerfile.replace(selected.base_image, ""):
        raise ManifestError("unresolved_dockerfile_input")
    _write(output / "Dockerfile", dockerfile.encode("utf-8"))
    _write(
        output / ".dockerignore",
        b"**\n!Dockerfile\n!source/\n!source/**\n!release/\n!release/**\n!inputs/\n!inputs/**\n",
    )
    record = {
        "schemaVersion": 1,
        "architecture": architecture,
        "baseImage": selected.base_image,
        "baseConfig": selected.base_config,
        "sourceCommit": identity.source_commit,
        "packageVersion": identity.package_version,
        "sourceArchiveSha256": identity.source_archive_sha256,
        "inputLockSha256": lock_digest,
        "networkDuringAssembly": "none",
        "artifactStatus": "not-built",
    }
    _write(output / "context.json", canonical(record) + b"\n")
    for directory, _subdirs, _files in os.walk(
        output, topdown=False, followlinks=False
    ):
        _sync(Path(directory))
    _sync(output.parent)
    verify_source_identity(output / "source", output / "release/source-manifest.json")
    return output / "context.json"
