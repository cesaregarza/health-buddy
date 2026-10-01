"""Verified immutable context assembly includes every runtime source input."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from health_buddy.runtime.bundle import create_bundle
from health_buddy.runtime.context import create_context
from health_buddy.runtime.manifest import ManifestError, verify_source_identity
from tests.test_runtime_bundle import git, source
from tests.test_runtime_inputs import lock


def context_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repository, _ = source(tmp_path)
    package = repository / "packaging"
    package.mkdir()
    lock(package).rename(package / "runtime-inputs.json")
    (package / "runtime.Dockerfile").write_text(
        "FROM @BASE_IMAGE@\nLABEL source=@COMMIT@\nCOPY source /source\n"
    )
    script = repository / "src/tool"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    git(repository, "add", ".")
    git(repository, "commit", "--quiet", "-m", "Synthetic packaging contract")
    bundle = tmp_path / "bundle"
    create_bundle(repository, git(repository, "rev-parse", "HEAD"), bundle)
    downloads = tmp_path / "downloads"
    downloads.mkdir(mode=0o700)
    (downloads / "synthetic.whl").write_bytes(b"abc")
    (downloads / "synthetic.deb").write_bytes(b"def")
    return bundle, downloads


def test_context_exact_source_inputs_permissions_and_no_repository_history(
    tmp_path: Path,
) -> None:
    bundle, downloads = context_fixture(tmp_path)
    output = tmp_path / "context"
    previous = os.umask(0o077)
    try:
        create_context(bundle, downloads, "amd64", output)
    finally:
        os.umask(previous)
    identity = verify_source_identity(
        output / "source", output / "release/source-manifest.json"
    )
    assert identity.source_evidence == "packaged_manifest"
    assert (output / "source/src/tool").stat().st_mode & 0o777 == 0o755
    assert (output / "inputs/wheels/synthetic.whl").stat().st_mode & 0o777 == 0o644
    assert output.stat().st_mode & 0o777 == 0o700
    assert not (output / "source/.git").exists()
    assert "--hash=sha256:" in (output / "inputs/requirements.txt").read_text()
    assert "@COMMIT@" not in (output / "Dockerfile").read_text()
    with pytest.raises((FileExistsError, ManifestError)):
        create_context(bundle, downloads, "amd64", output)


def test_context_refuses_changed_input_before_allocating_output(tmp_path: Path) -> None:
    bundle, downloads = context_fixture(tmp_path)
    (downloads / "synthetic.whl").write_bytes(b"abd")
    with pytest.raises(ManifestError, match="runtime_input_hash_mismatch"):
        create_context(bundle, downloads, "amd64", tmp_path / "context")
    assert not (tmp_path / "context").exists()
