"""Synthetic exact-source and immutable metadata regressions; no image claims."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from health_buddy import runtime_manifest
from health_buddy.core.release_identity import ReleaseIdentity
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
    # Fixed synthetic Git builtins; no external input or inherited hooks.
    result = subprocess.run(  # noqa: S603
        [
            "/usr/bin/git",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repository),
            *arguments,
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def source(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    git(repository, "init", "--quiet")
    (repository / "docs").mkdir()
    (repository / "src").mkdir()
    (repository / "pyproject.toml").write_text(
        '[project]\nname="health-buddy"\nversion="0.1.0.dev0"\n'
    )
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


def test_bundle_selects_exact_commit_not_working_tree_and_excludes_history(
    tmp_path: Path,
) -> None:
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
    assert (
        identity.source_archive_sha256
        == hashlib.sha256((output / "release/source.tar").read_bytes()).hexdigest()
    )
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
        create_bundle(
            repository, git(repository, "rev-parse", "HEAD"), tmp_path / "output"
        )
    assert outside.read_text() == "synthetic outside input"


@pytest.mark.parametrize(
    "mutation",
    ["changed", "extra", "missing", "linked-file", "linked-directory", "archive"],
)
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


@pytest.mark.parametrize(
    "mutation",
    [
        "boolean-version",
        "boolean-size",
        "duplicate-path",
        "traversal",
        "bad-docs",
        "unknown-key",
        "bad-interface",
    ],
)
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


def test_manifest_duplicate_json_key_and_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected, manifest, _revision = prepared(tmp_path)
    before = manifest.read_bytes()
    manifest.write_bytes(
        before.replace(
            b'"manifestVersion":1', b'"manifestVersion":1,"manifestVersion":1'
        )
    )
    assert read_source_identity(selected, manifest) == ReleaseIdentity()
    manifest.write_bytes(before)
    monkeypatch.setattr(runtime_manifest, "MAX_METADATA", 10)
    assert read_source_identity(selected, manifest) == ReleaseIdentity()


def test_inventory_bounds_empty_directories_before_unbounded_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected, _manifest, _revision = prepared(tmp_path)
    monkeypatch.setattr(runtime_manifest, "MAX_ENTRIES", 6)
    for index in range(8):
        (selected / f"empty-{index}").mkdir()
    with pytest.raises(ManifestError, match="bundle_entries_exceeded"):
        runtime_manifest.inventory(selected)


def test_immutable_identity_is_startup_snapshot_not_continuous_attestation(
    tmp_path: Path,
) -> None:
    selected, manifest, revision = prepared(tmp_path)
    startup = verify_source_identity(selected, manifest)
    (selected / "docs/guide.md").write_text("Synthetic later mutation.\n")
    assert startup.source_commit == revision
    assert startup.source_evidence == "packaged_manifest"
    assert read_source_identity(selected, manifest) == ReleaseIdentity()


def test_native_bundle_rejects_symlink_parent_before_target_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "unread-target"
    target.mkdir()
    (target / "child").mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    original = Path.lstat

    def guarded(path, *args, **kwargs):
        if path != link and path.is_relative_to(link):
            pytest.fail("bundle traversal accessed a descendant through a symlink")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", guarded)
    with pytest.raises(ManifestError, match="invalid_bundle_directory"):
        runtime_manifest.native_directory(link / "child")


@pytest.mark.parametrize("mount", ["/mnt", "//mnt", "///mnt"])
def test_native_bundle_rejects_forbidden_mount_lexically(
    monkeypatch: pytest.MonkeyPatch, mount: str
) -> None:
    def forbidden(*_args, **_kwargs):
        pytest.fail("a forbidden lexical mount was probed")

    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(ManifestError, match="invalid_bundle_directory"):
        runtime_manifest.native_directory(Path(mount + "/synthetic/not-accessed"))


def test_exact_bundle_ignores_retained_repository_replacement_objects(tmp_path):
    repository, original = source(tmp_path)
    original_tree = git(repository, "rev-parse", original + "^{tree}")
    (repository / "src/module.py").write_text("VALUE = 2\n")
    git(repository, "add", "src/module.py")
    git(repository, "commit", "--quiet", "-m", "Synthetic replacement tree")
    replacement = git(repository, "rev-parse", "HEAD")
    git(repository, "replace", original, replacement)
    assert git(repository, "show", original + ":src/module.py") == "VALUE = 2"
    output = tmp_path / "output"
    manifest = create_bundle(repository, original, output)
    identity = verify_source_identity(output / "source", manifest)
    assert identity.source_commit == original
    assert identity.source_tree == original_tree
    assert (output / "source/src/module.py").read_bytes() == b"VALUE = 1\n"
    assert git(repository, "rev-parse", "refs/replace/" + original) == replacement
    assert (repository / "src/module.py").read_bytes() == b"VALUE = 2\n"


def private_umask_bundle(tmp_path):
    repository, _revision = source(tmp_path)
    executable = repository / "src/runner.sh"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    git(repository, "add", "src/runner.sh")
    git(repository, "commit", "--quiet", "-m", "Synthetic executable source")
    revision = git(repository, "rev-parse", "HEAD")
    before = {
        path: (path.stat().st_mode, path.read_bytes())
        for path in (executable, repository / "src/module.py")
    }
    previous = os.umask(0o077)
    try:
        output = tmp_path / "output"
        manifest = create_bundle(repository, revision, output)
    finally:
        os.umask(previous)
    for path, expected in before.items():
        assert (path.stat().st_mode, path.read_bytes()) == expected
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for path in (output / "source", output / "release"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o755
        for item in path.rglob("*"):
            expected = 0o755 if item.is_dir() or item.name == "runner.sh" else 0o644
            assert stat.S_IMODE(item.stat().st_mode) == expected
    identity = verify_source_identity(output / "source", manifest)
    assert identity.source_commit == revision
    return output


def test_new_bundle_modes_are_exact_under_private_maintenance_umask(tmp_path):
    private_umask_bundle(tmp_path)


def test_source_and_release_contents_are_readable_by_distinct_uid(tmp_path):
    if os.geteuid() != 0:
        pytest.skip("queue root launches a distinct synthetic unprivileged UID")
    output = private_umask_bundle(tmp_path)
    source_fd = os.open(output / "source", os.O_RDONLY | os.O_DIRECTORY)
    release_fd = os.open(output / "release", os.O_RDONLY | os.O_DIRECTORY)
    try:
        script = (
            "import os,sys\n"
            "assert os.geteuid()==65534 and os.getegid()==65534\n"
            "os.fchdir(int(sys.argv[1]))\n"
            "assert open('src/module.py','rb').read()==b'VALUE = 1\\n'\n"
            "assert os.access('src/runner.sh',os.X_OK)\n"
            "os.fchdir(int(sys.argv[2]))\n"
            "assert open('source-manifest.json','rb').read()\n"
            "assert open('source.tar','rb').read(1)\n"
        )
        # Fixed interpreter/fixture code and already-opened immutable directories.
        result = subprocess.run(  # noqa: S603
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                "-c",
                script,
                str(source_fd),
                str(release_fd),
            ],
            pass_fds=(source_fd, release_fd),
            cwd="/",
            user=65534,
            group=65534,
            extra_groups=[],
            env={"PATH": os.defpath},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 0, "synthetic non-owner bundle read failed"
    finally:
        os.close(source_fd)
        os.close(release_fd)
