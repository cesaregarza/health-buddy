"""Artifact manifests and explicit local load selection, no daemon required."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from health_buddy.runtime import release as runtime_release
from health_buddy.runtime.manifest import (
    INTERFACES,
    ManifestError,
    verify_source_identity,
)
from health_buddy.runtime.release import (
    create_release,
    docker_command,
    load_verified_archive,
    selected_artifact,
)
from tests.test_runtime_artifact import (
    COMMIT,
    FIXTURES,
    INPUT_LOCK,
    VERSION,
    inspect,
    make_archive,
)
from tests.test_runtime_context import context_fixture


def test_manifest_binds_both_actual_archives_and_source(tmp_path: Path) -> None:
    bundle, _ = context_fixture(tmp_path)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    lock_hash = hashlib.sha256(
        (bundle / "source/packaging/runtime-inputs.json").read_bytes()
    ).hexdigest()
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    labels = {
        "org.opencontainers.image.revision": identity.source_commit,
        "org.opencontainers.image.version": identity.package_version,
        "io.health-buddy.source-archive-sha256": identity.source_archive_sha256,
        "io.health-buddy.input-lock-sha256": lock_hash,
    }
    for architecture in ("amd64", "arm64"):
        make_archive(
            artifacts / f"health-buddy-linux-{architecture}.docker.tar",
            architecture=architecture,
            config_override={"config": {"Labels": labels}},
        )
    manifest = create_release(bundle, artifacts)
    value = json.loads(manifest.read_bytes())
    assert value["status"] == "private-verification-candidate"
    assert value["sourceCommit"] == identity.source_commit
    assert len(value["artifacts"]) == 2
    result = selected_artifact(manifest, "amd64")
    assert result.config_digest in result.loader_ids
    assert result.archive_sha256 in (artifacts / "SHA256SUMS").read_text()
    with (artifacts / "health-buddy-linux-amd64.docker.tar").open("ab") as output:
        output.write(b"untrusted trailing data")
    with pytest.raises(ManifestError):
        selected_artifact(manifest, "amd64")


def test_absent_second_architecture_never_creates_placeholder_manifest(
    tmp_path: Path,
) -> None:
    bundle, _ = context_fixture(tmp_path)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with pytest.raises((ManifestError, FileNotFoundError)):
        create_release(bundle, artifacts)
    assert not (artifacts / "runtime-manifest.json").exists()


def test_docker_leaf_symlink_is_rejected_without_following_or_invoking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"synthetic executable")
    target.chmod(0o700)
    link = tmp_path / "docker"
    link.symlink_to(target)
    original = Path.stat

    def guard(path, *args, **kwargs):
        if path in (link, target) and kwargs.get("follow_symlinks", True):
            raise AssertionError("following executable probe")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guard)
    with pytest.raises(ManifestError, match="native_regular_executable"):
        docker_command(link)
    assert target.read_bytes() == b"synthetic executable"


def test_loader_uses_actual_manifest_identity_not_universal_config_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "image.tar"
    make_archive(archive, modern_blobs=True, compressed=True)
    result = inspect(archive)
    executable = tmp_path / "docker"
    executable.write_bytes(b"synthetic not executed")
    executable.chmod(0o700)
    calls = []

    def run(arguments, **_kwargs):
        calls.append(arguments)
        if "load" in arguments:
            return subprocess.CompletedProcess(arguments, 0)
        body = (
            "\n".join(
                [
                    result.loader_ids[0],
                    "linux",
                    "amd64",
                    json.dumps(["sha256:" + item for item in result.diff_ids]),
                ]
            )
            + "\n"
        )
        return subprocess.CompletedProcess(arguments, 0, body.encode())

    monkeypatch.setattr(runtime_release.subprocess, "run", run)
    loaded = load_verified_archive(archive, result, executable)
    assert loaded == result.loader_ids[0] != result.config_digest
    assert all(
        arguments[:3] == [str(executable), "--host", "unix:///run/docker.sock"]
        for arguments in calls
    )
    assert "--platform" in calls[0]
    assert calls[1][-1] in result.loader_ids


def test_loader_rejects_changed_archive_before_daemon_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "image.tar"
    make_archive(archive)
    result = inspect(archive)
    executable = tmp_path / "docker"
    executable.write_bytes(b"synthetic")
    executable.chmod(0o700)
    archive.write_bytes(b"changed")
    monkeypatch.setattr(
        runtime_release.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "daemon reached before artifact admission"
        ),
    )
    with pytest.raises(ManifestError, match="changed_before_load"):
        load_verified_archive(archive, result, executable)


def test_manifest_parent_is_checked_before_reading_or_following(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "actual"
    target.mkdir()
    (target / "runtime-manifest.json").write_bytes(b"synthetic unchanged bytes")
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        runtime_release,
        "_json",
        lambda *_args: pytest.fail("manifest read before native parent validation"),
    )
    with pytest.raises(ManifestError, match="invalid_bundle_directory"):
        selected_artifact(link / "runtime-manifest.json", "amd64")
    assert (
        target / "runtime-manifest.json"
    ).read_bytes() == b"synthetic unchanged bytes"


def pinned_release(directory: Path, amd64_archive: bytes) -> Path:
    """A runtime manifest whose amd64 record pins these bytes by digest and size.

    Selection verifies the pinned archive before comparing any receipt field, so
    the receipt fields and the unselected arm64 record are placeholders.
    """
    source = b"synthetic source archive"
    (directory / "health-buddy-source.tar").write_bytes(source)
    (directory / "health-buddy-linux-amd64.docker.tar").write_bytes(amd64_archive)
    pins = {
        "amd64": (hashlib.sha256(amd64_archive).hexdigest(), len(amd64_archive)),
        "arm64": ("0" * 64, 1),
    }
    value = {
        "manifestVersion": 1,
        "status": "private-verification-candidate",
        "format": "docker-image-save",
        "packageVersion": VERSION,
        "sourceCommit": COMMIT,
        "sourceTree": COMMIT,
        "sourceArchive": {
            "file": "health-buddy-source.tar",
            "bytes": len(source),
            "sha256": hashlib.sha256(source).hexdigest(),
        },
        "inputLockSha256": INPUT_LOCK,
        "interfaces": INTERFACES,
        "artifacts": [
            {
                "file": f"health-buddy-linux-{architecture}.docker.tar",
                "architecture": architecture,
                "config_digest": "sha256:" + "0" * 64,
                "loader_ids": [],
                "archive_sha256": digest,
                "archive_bytes": size,
                "layer_digests": [],
                "diff_ids": [],
            }
            for architecture, (digest, size) in pins.items()
        ],
        "qualification": "synthetic",
    }
    manifest = directory / "runtime-manifest.json"
    manifest.write_text(json.dumps(value))
    return manifest


def test_selecting_a_pinned_containerd_store_save_refuses_naming_the_store(
    tmp_path: Path,
) -> None:
    archive = (FIXTURES / "docker29-containerd-store.tar").read_bytes()
    manifest = pinned_release(tmp_path, archive)
    with pytest.raises(
        ManifestError, match=r"^artifact_from_containerd_image_store: "
    ) as refused:
        selected_artifact(manifest, "amd64")
    assert "features.containerd-snapshotter" in str(refused.value)
