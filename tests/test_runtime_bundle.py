"""Synthetic exact-source and immutable metadata regressions; no image claims."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from health_buddy import runtime_manifest
from health_buddy.release_identity import ReleaseIdentity
from health_buddy.runtime_bundle import create_bundle
from health_buddy.runtime_manifest import (
    ManifestError,
    canonical,
    read_source_identity,
    verify_source_identity,
)


def git(repository: Path, *arguments: str) -> str:
    environment = {
        "PATH": os.defpath,
        "HOME": str(repository),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "Synthetic Builder",
        "GIT_AUTHOR_EMAIL": "builder@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic Builder",
        "GIT_COMMITTER_EMAIL": "builder@example.invalid",
    }
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false", "-C", str(repository), *arguments],
        env=environment, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def source(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    git(repository, "init", "--quiet")
    (repository / "docs").mkdir()
    (repository / "src").mkdir()
    (repository / "pyproject.toml").write_text('[project]\nname="health-buddy"\nversion="0.1.0.dev0"\n')
    (repository / "docs" / "guide.md").write_text("Synthetic source guide.\n")
    (repository / "src" / "module.py").write_text("VALUE = 1\n")
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "Synthetic source fixture")
    return repository, git(repository, "rev-parse", "HEAD")


def prepared(tmp_path: Path) -> tuple[Path, Path, str]:
    repository, revision = source(tmp_path)
    output = tmp_path / "output"
    manifest = create_bundle(repository, revision, output)
    return output / "source", manifest, revision


def test_bundle_selects_exact_commit_not_working_tree_and_excludes_history(tmp_path: Path) -> None:
    repository, revision = source(tmp_path)
    (repository / "src" / "module.py").write_text("VALUE = 999\n")
    (repository / "private-owner-file.txt").write_text("synthetic unpublished note")
    output = tmp_path / "output"
    manifest = create_bundle(repository, revision, output)
    selected = output / "source"
    identity = verify_source_identity(selected, manifest)
    assert identity.source_commit == revision
    assert identity.source_tree == git(repository, "rev-parse", revision + "^{tree}")
    assert identity.package_version == "0.1.0.dev0"
    assert identity.source_evidence == "packaged_manifest"
    assert identity.artifact is None
    assert identity.source_archive_sha256 == hashlib.sha256((output / "release/source.tar").read_bytes()).hexdigest()
    assert (selected / "src/module.py").read_text() == "VALUE = 1\n"
    assert not (selected / ".git").exists()
    assert not (selected / "private-owner-file.txt").exists()
    assert (repository / "src/module.py").read_text() == "VALUE = 999\n"
    before = manifest.read_bytes()
    with pytest.raises(ManifestError, match="explicit_commit_and_new_output_required"):
        create_bundle(repository, revision, output)
    assert manifest.read_bytes() == before


def test_bundle_rejects_git_export_substitution_or_exclusion(tmp_path: Path) -> None:
    repository, _revision = source(tmp_path)
    (repository / ".gitattributes").write_text("src/module.py export-ignore\n")
    git(repository, "add", ".gitattributes")
    git(repository, "commit", "--quiet", "-m", "Synthetic unsupported export")
    revision = git(repository, "rev-parse", "HEAD")
    with pytest.raises(ManifestError, match="archive_inventory_mismatch"):
        create_bundle(repository, revision, tmp_path / "output")
    assert not (tmp_path / "output/release/source-manifest.json").exists()


def test_bundle_rejects_tracked_symlink_without_reading_target(tmp_path: Path) -> None:
    repository, _revision = source(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("synthetic outside input")
    (repository / "shortcut").symlink_to(outside)
    git(repository, "add", "shortcut")
    git(repository, "commit", "--quiet", "-m", "Synthetic prohibited link")
    with pytest.raises(ManifestError, match="unsupported_source_entry"):
        create_bundle(repository, git(repository, "rev-parse", "HEAD"), tmp_path / "output")
    assert outside.read_text() == "synthetic outside input"


@pytest.mark.parametrize("mutation", ["changed", "extra", "missing", "linked-file", "linked-directory", "archive"])
def test_source_integrity_changes_fail_closed(tmp_path: Path, mutation: str) -> None:
    selected, manifest, _revision = prepared(tmp_path)
    module = selected / "src/module.py"
    if mutation == "changed":
        module.write_text("VALUE = 2\n")
    elif mutation == "extra":
        (selected / "extra.txt").write_text("synthetic extra")
    elif mutation == "missing":
        module.unlink()
    elif mutation == "linked-file":
        outside = tmp_path / "outside"
        outside.write_text("VALUE = 1\n")
        module.unlink()
        module.symlink_to(outside)
    elif mutation == "linked-directory":
        original = selected / "src"
        original.rename(tmp_path / "moved")
        original.symlink_to(tmp_path / "moved", target_is_directory=True)
    else:
        with (manifest.parent / "source.tar").open("ab") as target:
            target.write(b"synthetic corruption")
    assert read_source_identity(selected, manifest) == ReleaseIdentity()
    with pytest.raises((ManifestError, OSError)):
        verify_source_identity(selected, manifest)


@pytest.mark.parametrize("mutation", ["boolean-version", "boolean-size", "duplicate-path", "traversal", "bad-docs", "unknown-key", "bad-interface"])
def test_manifest_shapes_are_strict_and_bounded(tmp_path: Path, mutation: str) -> None:
    selected, manifest, _revision = prepared(tmp_path)
    value = json.loads(manifest.read_bytes())
    if mutation == "boolean-version":
        value["manifestVersion"] = True
    elif mutation == "boolean-size":
        value["files"][0]["bytes"] = True
    elif mutation == "duplicate-path":
        value["files"].append(value["files"][0])
    elif mutation == "traversal":
        value["files"][0]["path"] = "../outside"
    elif mutation == "bad-docs":
        value["docsSha256"] = "0" * 64
    elif mutation == "unknown-key":
        value["arbitraryAuthority"] = "synthetic"
    else:
        value["interfaces"]["api"] = True
    manifest.write_bytes(canonical(value))
    assert read_source_identity(selected, manifest) == ReleaseIdentity()


def test_manifest_duplicate_json_key_and_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected, manifest, _revision = prepared(tmp_path)
    before = manifest.read_bytes()
    manifest.write_bytes(before.replace(b'"manifestVersion":1', b'"manifestVersion":1,"manifestVersion":1'))
    assert read_source_identity(selected, manifest) == ReleaseIdentity()
    manifest.write_bytes(before)
    monkeypatch.setattr(runtime_manifest, "MAX_METADATA", 10)
    assert read_source_identity(selected, manifest) == ReleaseIdentity()


def test_inventory_bounds_empty_directories_before_unbounded_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    selected, _manifest, _revision = prepared(tmp_path)
    monkeypatch.setattr(runtime_manifest, "MAX_ENTRIES", 6)
    for index in range(8):
        (selected / f"empty-{index}").mkdir()
    with pytest.raises(ManifestError, match="bundle_entries_exceeded"):
        runtime_manifest.inventory(selected)


def test_immutable_identity_is_startup_snapshot_not_continuous_attestation(tmp_path: Path) -> None:
    selected, manifest, revision = prepared(tmp_path)
    startup = verify_source_identity(selected, manifest)
    (selected / "docs/guide.md").write_text("Synthetic later mutation.\n")
    assert startup.source_commit == revision
    assert startup.source_evidence == "packaged_manifest"
    assert read_source_identity(selected, manifest) == ReleaseIdentity()
