"""Actual private candidate artifacts, distinct from registry/external publication."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from .runtime_artifact import ArtifactInspection, verify_docker_archive
from .runtime_bundle import _write
from .runtime_manifest import (
    GIT_SHA,
    INTERFACES,
    SHA256,
    VERSION,
    ManifestError,
    _json,
    canonical,
    file_digest,
    native_directory,
    verify_source_identity,
)


def inspect_image(bundle: Path, archive: Path, architecture: str) -> ArtifactInspection:
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    _, lock = file_digest(
        bundle / "source/packaging/runtime-inputs.json", 2 * 1024 * 1024
    )
    if (
        identity.source_commit is None
        or identity.source_archive_sha256 is None
        or identity.package_version is None
    ):
        raise ManifestError("source_identity_missing")
    return verify_docker_archive(
        archive,
        architecture=architecture,
        source_commit=identity.source_commit,
        source_archive_sha256=identity.source_archive_sha256,
        input_lock_sha256=lock,
        package_version=identity.package_version,
    )


def create_release(bundle: Path, directory: Path) -> Path:
    """Require both actual architecture archives. Never fabricate a missing URI."""
    native_directory(directory)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    artifacts = []
    for architecture in ("amd64", "arm64"):
        name = f"health-buddy-linux-{architecture}.docker.tar"
        result = inspect_image(bundle, directory / name, architecture)
        artifacts.append({"file": name, **asdict(result)})
    archive = bundle / "release/source.tar"
    size, source_hash = file_digest(archive, 64 * 1024 * 1024)
    _write(directory / "health-buddy-source.tar", archive.read_bytes())
    lock = bundle / "source/packaging/runtime-inputs.json"
    _, lock_hash = file_digest(lock, 2 * 1024 * 1024)
    value = {
        "manifestVersion": 1,
        "status": "private-verification-candidate",
        "format": "docker-image-save",
        "packageVersion": identity.package_version,
        "sourceCommit": identity.source_commit,
        "sourceTree": identity.source_tree,
        "sourceArchive": {
            "file": "health-buddy-source.tar",
            "bytes": size,
            "sha256": source_hash,
        },
        "inputLockSha256": lock_hash,
        "interfaces": INTERFACES,
        "artifacts": artifacts,
        "qualification": (
            "Archive verification only; per-platform build/runtime receipts "
            "remain separate. No actual host reboot, physical device or "
            "external publication claim."
        ),
    }
    target = directory / "runtime-manifest.json"
    _write(target, canonical(value) + b"\n")
    checksums = [
        (name, file_digest(directory / name, 1024 * 1024 * 1024)[1])
        for name in [
            "runtime-manifest.json",
            "health-buddy-source.tar",
            *[str(item["file"]) for item in artifacts],
        ]
    ]
    _write(
        directory / "SHA256SUMS",
        "".join(f"{digest}  {name}\n" for name, digest in checksums).encode("ascii"),
    )
    return target


def selected_artifact(manifest: Path, architecture: str) -> ArtifactInspection:
    native_directory(manifest.parent)
    if architecture not in {"amd64", "arm64"}:
        raise ManifestError("unsupported_runtime_architecture")
    value = _json(manifest)
    keys = {
        "manifestVersion",
        "status",
        "format",
        "packageVersion",
        "sourceCommit",
        "sourceTree",
        "sourceArchive",
        "inputLockSha256",
        "interfaces",
        "artifacts",
        "qualification",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ManifestError("invalid_runtime_manifest")
    if (
        type(value["manifestVersion"]) is not int
        or value["manifestVersion"] != 1
        or value["status"] != "private-verification-candidate"
        or value["format"] != "docker-image-save"
        or value["interfaces"] != INTERFACES
        or not isinstance(value["interfaces"], dict)
        or any(type(item) is not int for item in value["interfaces"].values())
    ):
        raise ManifestError("unsupported_runtime_manifest")
    for name, pattern in (
        ("packageVersion", VERSION),
        ("sourceCommit", GIT_SHA),
        ("sourceTree", GIT_SHA),
        ("inputLockSha256", SHA256),
    ):
        if not isinstance(value[name], str) or not pattern.fullmatch(value[name]):
            raise ManifestError("invalid_runtime_manifest")
    source = value["sourceArchive"]
    if (
        not isinstance(source, dict)
        or set(source) != {"file", "bytes", "sha256"}
        or source["file"] != "health-buddy-source.tar"
    ):
        raise ManifestError("invalid_runtime_manifest")
    if (
        type(source["bytes"]) is not int
        or not 0 < source["bytes"] <= 64 * 1024**2
        or not isinstance(source["sha256"], str)
        or not SHA256.fullmatch(source["sha256"])
    ):
        raise ManifestError("invalid_runtime_manifest")
    if file_digest(manifest.parent / source["file"], 64 * 1024 * 1024) != (
        source["bytes"],
        source["sha256"],
    ):
        raise ManifestError("release_source_mismatch")
    items = value["artifacts"]
    if not isinstance(items, list) or len(items) != 2:
        raise ManifestError("release_architecture_missing")
    selected = None
    seen = set()
    for record in items:
        if not isinstance(record, dict) or set(record) != {
            "file",
            "architecture",
            "config_digest",
            "loader_ids",
            "archive_sha256",
            "archive_bytes",
            "layer_digests",
            "diff_ids",
        }:
            raise ManifestError("invalid_runtime_artifact")
        arch = record["architecture"]
        if not isinstance(arch, str) or arch not in {"amd64", "arm64"} or arch in seen:
            raise ManifestError("invalid_runtime_artifact")
        seen.add(arch)
        if record["file"] != f"health-buddy-linux-{arch}.docker.tar":
            raise ManifestError("invalid_runtime_artifact")
        if arch == architecture:
            result = verify_docker_archive(
                manifest.parent / record["file"],
                architecture=architecture,
                source_commit=value["sourceCommit"],
                source_archive_sha256=source["sha256"],
                input_lock_sha256=value["inputLockSha256"],
                package_version=value["packageVersion"],
            )
            expected = {"file": record["file"], **asdict(result)}
            for field in ("layer_digests", "diff_ids", "loader_ids"):
                expected[field] = list(getattr(result, field))
            if record != expected:
                raise ManifestError("runtime_artifact_receipt_mismatch")
            selected = result
    if selected is None:
        raise ManifestError("release_architecture_missing")
    return selected


def docker_command(docker: Path) -> list[str]:
    native_directory(docker.parent)
    executable = docker.lstat()
    if not stat.S_ISREG(executable.st_mode) or not executable.st_mode & 0o111:
        raise ManifestError("runtime_docker_requires_native_regular_executable")
    return [str(docker), "--host", "unix:///run/docker.sock"]


def load_verified_archive(
    archive: Path, result: ArtifactInspection, docker: Path
) -> str:
    """Load no tags, then bind the actual engine ID to a validated descriptor."""
    command = docker_command(docker)
    if file_digest(archive, 1024 * 1024 * 1024) != (
        result.archive_bytes,
        result.archive_sha256,
    ):
        raise ManifestError("runtime_artifact_changed_before_load")
    with tempfile.TemporaryDirectory(prefix="hb-docker-") as folder:
        environment = {"PATH": os.defpath, "DOCKER_CONFIG": folder}
        subprocess.run(  # noqa: S603 - Explicit admitted local CLI, verified tag-free archive.
            [
                *command,
                "image",
                "load",
                "--platform",
                f"linux/{result.architecture}",
                "--input",
                str(archive),
            ],
            env=environment,
            check=True,
            timeout=180,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for selected in result.loader_ids:
            inspected = subprocess.run(  # noqa: S603 - Only archive-validated immutable IDs.
                [
                    *command,
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}\n{{.Os}}\n{{.Architecture}}\n{{json .RootFS.Layers}}",
                    selected,
                ],
                env=environment,
                check=False,
                timeout=15,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if inspected.returncode != 0:
                continue
            if len(inspected.stdout) > 16384:
                raise ManifestError("loaded_runtime_metadata_limit")
            rows = inspected.stdout.decode("ascii").splitlines()
            if (
                len(rows) != 4
                or rows[0] not in result.loader_ids
                or rows[1:3] != ["linux", result.architecture]
            ):
                raise ManifestError("loaded_runtime_identity_mismatch")
            if json.loads(rows[3]) != [
                "sha256:" + digest for digest in result.diff_ids
            ]:
                raise ManifestError("loaded_runtime_rootfs_mismatch")
            if file_digest(archive, 1024 * 1024 * 1024) != (
                result.archive_bytes,
                result.archive_sha256,
            ):
                raise ManifestError("runtime_artifact_changed_during_load")
            return rows[0]
    raise ManifestError("loaded_runtime_identity_missing")


def load_release(
    manifest: Path,
    architecture: str,
    workspace: Path,
    output: Path,
    *,
    docker: Path,
    uid: int,
    gid: int,
) -> None:
    """Explicit local daemon load; Compose receives the verified image ID only.

    The caller must independently trust/admit this local daemon and CLI/plugin
    environment. The project queue uses its reviewed guard; no remote endpoint.
    """
    docker_command(docker)
    native_directory(workspace)
    native_directory(output.parent)
    if not 0 < uid < 2**31 or not 0 < gid < 2**31:
        raise ManifestError("runtime_nonroot_identity_required")
    details = workspace.lstat()
    if (
        details.st_uid != uid
        or details.st_gid != gid
        or details.st_mode & 0o777 != 0o700
    ):
        raise ManifestError("runtime_workspace_ownership_mismatch")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{1,240}", str(workspace)):
        raise ManifestError("runtime_compose_path_unsupported")
    result = selected_artifact(manifest, architecture)
    archive = manifest.parent / f"health-buddy-linux-{architecture}.docker.tar"
    image_id = load_verified_archive(archive, result, docker)
    _write(
        output,
        f"HB_IMAGE={image_id}\nHB_UID={uid}\nHB_GID={gid}\nHB_WORKSPACE={workspace}\n".encode(
            "ascii"
        ),
        0o600,
    )
