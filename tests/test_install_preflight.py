"""Synthetic pinned release dry-run; no Docker/host mutation or health reads.

prepared(maintenance=True) adds a real bundle of this checkout's packages and
its pinned release: each test process builds them once, keeps that original
read-only and copies it for every test. It also memoizes the MCP readiness
probe, which starts an interpreter that imports the SDK: once the real probe
has passed for this interpreter and these source bytes, the same probe is not
run again in this process. Every other probe, and every failure, is real.
"""

import contextlib
import functools
import hashlib
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from health_buddy import connect_agent
from health_buddy.connect_agent import check_mcp_readiness
from health_buddy.install import agent as install_agent
from health_buddy.install import preflight as install_preflight
from health_buddy.install.prepare import MAINTENANCE_REFERENCES
from health_buddy.runtime.bundle import create_bundle
from health_buddy.runtime.manifest import (
    ManifestError,
    canonical,
    inventory,
    verify_source_identity,
)
from health_buddy.runtime.release import create_release
from tests.test_runtime_artifact import make_archive
from tests.test_runtime_bundle import git, plant_bytecode
from tests.test_runtime_context import context_fixture

ROOT = Path(__file__).resolve().parents[1]
# Canonical source inventories this interpreter's real probe passed against.
READY_SOURCES: set[bytes] = set()


def pinned_release(bundle: Path, artifacts: Path) -> Path:
    """Both architecture archives, labelled for the bundle's source, pinned."""
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    artifacts.mkdir()
    labels = {
        "org.opencontainers.image.revision": identity.source_commit,
        "org.opencontainers.image.version": identity.package_version,
        "io.health-buddy.source-archive-sha256": identity.source_archive_sha256,
        "io.health-buddy.input-lock-sha256": hashlib.sha256(
            (bundle / "source/packaging/runtime-inputs.json").read_bytes()
        ).hexdigest(),
    }
    for architecture in ("amd64", "arm64"):
        make_archive(
            artifacts / f"health-buddy-linux-{architecture}.docker.tar",
            architecture=architecture,
            config_override={"config": {"Labels": labels}},
        )
    return create_release(bundle, artifacts)


def entries(root: Path) -> list[str]:
    """root and every entry under it, without pathlib's cost per entry."""
    paths = [str(root)]
    for folder, folders, files in os.walk(root):
        paths += [os.path.join(folder, name) for name in (*folders, *files)]
    return paths


def change_times(root: Path) -> dict[str, int]:
    """Any write, rename, chmod or new entry under root moves one of these."""
    return {path: os.lstat(path).st_ctime_ns for path in entries(root)}


def chmod_tree(root: Path, change: Callable[[int], int]) -> None:
    for path in entries(root):
        os.chmod(path, change(stat.S_IMODE(os.lstat(path).st_mode)))


@functools.cache
def maintenance_original(base: Path) -> tuple[Path, dict[str, int]]:
    """The read-only maintenance bundle and release, and their change times.

    Built once per test process under base, the parent of every tmp_path.
    """
    # A fresh directory per attempt, so a failed build repeats its own error.
    work = Path(tempfile.mkdtemp(prefix="maintenance-original-", dir=base))
    previous = os.umask(0o022)  # The same modes whichever test builds it.
    try:
        # context_fixture's repository holds the synthetic packaging contract;
        # add the files prepare and the bootstrap read, and the packages the
        # stages import.
        context_fixture(work)
        repository = work / "repository"
        for package in ("health_buddy", "health_ingest"):
            shutil.copytree(
                ROOT / "src" / package,
                repository / "src" / package,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
        for name in (
            *MAINTENANCE_REFERENCES,
            "packaging/compose.yaml",
            "scripts/package_runtime.py",
        ):
            destination = repository / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / name).read_bytes())
        git(repository, "add", ".")
        git(repository, "commit", "--quiet", "-m", "Synthetic maintenance references")
        original = work / "original"
        original.mkdir()
        bundle = original / "maintenance-bundle"
        create_bundle(repository, git(repository, "rev-parse", "HEAD"), bundle)
        pinned_release(bundle, original / "artifacts")
    finally:
        os.umask(previous)
    chmod_tree(original, lambda mode: mode & ~0o222)
    return original, change_times(original)


def maintenance_copy(tmp_path: Path, name: str) -> Path:
    """This test's own writable copy of one directory of the original."""
    # pytest makes every tmp_path in the session's (or xdist worker's) base.
    original, built = maintenance_original(tmp_path.parent)
    # Root ignores the read-only modes, so a change must still be caught here.
    assert change_times(original) == built, "a test changed the shared original"
    copy = tmp_path / name
    shutil.copytree(original / name, copy)
    # Under umask 022 the builders granted write to the owner only.
    chmod_tree(copy, lambda mode: mode | 0o200)
    return copy


def maintenance_bundle(tmp_path: Path) -> Path:
    return maintenance_copy(tmp_path, "maintenance-bundle")


def readiness_once(python: Path, source: Path) -> None:
    """Probe for real unless sys.executable already passed against these bytes.

    Only that exact path is remembered: a venv's python resolves to the same
    binary but has its own packages.
    """
    key = None
    if python == Path(sys.executable):
        with contextlib.suppress(ManifestError, OSError):
            key = canonical(inventory(source))
    if key is None or key not in READY_SOURCES:
        check_mcp_readiness(python, source)
        if key is not None:
            READY_SOURCES.add(key)


def prepared(tmp_path, monkeypatch, *, maintenance=False):
    if maintenance:
        bundle = maintenance_bundle(tmp_path)
        manifest = maintenance_copy(tmp_path, "artifacts") / "runtime-manifest.json"
        # Agent setup probes before mutations; connect receives that result.
        monkeypatch.setattr(install_agent, "check_mcp_readiness", readiness_once)
        monkeypatch.setattr(connect_agent, "check_mcp_readiness", readiness_once)
    else:
        bundle, _ = context_fixture(tmp_path)
        manifest = pinned_release(bundle, tmp_path / "artifacts")
    workspace = tmp_path / "persistent-owner"
    workspace.mkdir(mode=0o700)
    docker = tmp_path / "docker"
    docker.write_bytes(b"synthetic executable never invoked")
    docker.chmod(0o700)
    monkeypatch.setattr(
        install_preflight,
        "host_facts",
        lambda: {
            "system": "Linux",
            "architecture": "amd64",
            "physicalMemoryBytes": 4 * 1024**3,
            "logicalCpus": 2,
        },
    )
    monkeypatch.setattr(
        install_preflight, "docker_socket_state", lambda: "socket_present_not_connected"
    )
    monkeypatch.setattr(
        install_preflight.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(free=10 * 1024**3),
    )
    return dict(
        bundle=bundle,
        manifest=manifest,
        trusted_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        workspace=workspace,
        docker=docker,
    )


def codes(value):
    return {item["code"] for item in value["diagnostics"]}


def test_pinned_real_archive_inspection_reports_plan_without_creating_install(
    tmp_path, monkeypatch
):
    inputs = prepared(tmp_path, monkeypatch)
    before = {
        str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    result = install_preflight.preflight(**inputs)
    assert result["preflightPassed"]
    assert result["release"]["state"] == "pinned_archives_and_matching_source_verified"
    assert not result["installed"] and not result["readyForActivation"]
    assert result["docker"]["compose"] == "not_executed_or_qualified"
    assert result["workspace"]["state"] == "empty_not_initialized"
    assert before == {
        str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
    }
    assert list(inputs["workspace"].iterdir()) == []
    assert "checkpointed_install" in result["pending"]


def test_wrong_trust_pin_refuses_before_archive_validation(tmp_path, monkeypatch):
    inputs = prepared(tmp_path, monkeypatch)
    inputs["trusted_manifest_sha256"] = "0" * 64
    monkeypatch.setattr(
        install_preflight,
        "selected_artifact",
        lambda *_: pytest.fail("untrusted release inspected"),
    )
    result = install_preflight.preflight(**inputs)
    assert "release_untrusted" in codes(result)
    assert not result["preflightPassed"]


def test_changed_archive_is_refused_without_loading(tmp_path, monkeypatch):
    inputs = prepared(tmp_path, monkeypatch)
    archive = inputs["manifest"].parent / "health-buddy-linux-amd64.docker.tar"
    with archive.open("ab") as output:
        output.write(b"synthetic tamper")
    assert "release_invalid" in codes(install_preflight.preflight(**inputs))


def test_changed_source_names_the_file_to_re_extract(tmp_path, monkeypatch):
    inputs = prepared(tmp_path, monkeypatch)
    (inputs["bundle"] / "source/src/module.py").write_text("VALUE = 2\n")
    result = install_preflight.preflight(**inputs)
    assert not result["preflightPassed"]
    assert {"release_invalid", "source_inventory_mismatch"} <= codes(result)
    (mismatch,) = [
        item
        for item in result["diagnostics"]
        if item["code"] == "source_inventory_mismatch"
    ]
    assert mismatch["recovery"].startswith("Re-extract the source bundle")
    assert "src/module.py" in mismatch["recovery"]
    assert str(inputs["bundle"]) not in json.dumps(result)


def test_bytecode_from_running_the_bundle_in_place_still_passes(tmp_path, monkeypatch):
    inputs = prepared(tmp_path, monkeypatch)
    plant_bytecode(inputs["bundle"] / "source")
    result = install_preflight.preflight(**inputs)
    assert result["preflightPassed"], result["diagnostics"]


def test_existing_state_and_conflicting_port_are_preserved(tmp_path, monkeypatch):
    inputs = prepared(tmp_path, monkeypatch)
    personal = inputs["workspace"] / "owner-note.txt"
    personal.write_text("synthetic personal note retained")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        result = install_preflight.preflight(**inputs, port=listener.getsockname()[1])
    assert {"existing_state_requires_review", "port_conflict"} <= codes(result)
    assert personal.read_text() == "synthetic personal note retained"
    assert "synthetic personal" not in json.dumps(result)
    assert str(inputs["workspace"]) not in json.dumps(result)


def test_unsupported_resources_and_missing_docker_have_stable_refusals(
    tmp_path, monkeypatch
):
    inputs = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(
        install_preflight,
        "host_facts",
        lambda: {
            "system": "ExampleOS",
            "architecture": None,
            "physicalMemoryBytes": 1,
            "logicalCpus": 1,
        },
    )
    inputs["docker"].unlink()
    monkeypatch.setattr(install_preflight, "docker_socket_state", lambda: "unavailable")
    result = install_preflight.preflight(**inputs)
    assert {
        "unsupported_host",
        "resources_low",
        "docker_cli_unavailable",
        "docker_socket_unavailable",
    } <= codes(result)
    assert not result["preflightPassed"]


# A bare --docker parses to None like an omitted one; an empty value parses to
# the relative path ".", which the native-executable check refuses.
@pytest.mark.parametrize(
    "docker_args", [[], ["--docker", ""]], ids=["omitted_none", "empty_relative"]
)
def test_missing_or_empty_docker_cli_names_the_required_executable(
    tmp_path, monkeypatch, capsys, docker_args
):
    inputs = prepared(tmp_path, monkeypatch)
    arguments = [
        f"--{key.replace('_', '-')}={value}"
        for key, value in inputs.items()
        if key != "docker"
    ]
    assert install_preflight.main([*arguments, *docker_args]) == 2
    result = json.loads(capsys.readouterr().out)
    (diagnostic,) = result["diagnostics"]
    assert diagnostic["code"] == "docker_cli_unavailable"
    assert "--docker" in diagnostic["recovery"]
    assert "INSPECTED_NATIVE_DOCKER" in diagnostic["recovery"]
    assert "/usr/bin/docker" in diagnostic["recovery"]
    assert "socket" in diagnostic["recovery"]
    assert not result["preflightPassed"]
    assert not any(inputs["workspace"].iterdir())


def test_docker_socket_is_refused_as_an_executable(tmp_path, monkeypatch):
    inputs = prepared(tmp_path, monkeypatch)
    with socket.socket(socket.AF_UNIX) as daemon:
        path = tmp_path / "docker.sock"
        daemon.bind(str(path))
        path.chmod(0o700)
        result = install_preflight.preflight(**{**inputs, "docker": path})
    assert "docker_cli_unavailable" in codes(result)
    assert not result["preflightPassed"]
    assert str(path) not in json.dumps(result)


def test_linked_target_refuses_before_personal_inventory(tmp_path, monkeypatch):
    inputs = prepared(tmp_path, monkeypatch)
    target = tmp_path / "linked-owner"
    target.symlink_to(inputs["workspace"], target_is_directory=True)
    inputs["workspace"] = target
    monkeypatch.setattr(
        install_preflight.os,
        "scandir",
        lambda *_: pytest.fail("linked workspace inspected"),
    )
    assert "path_unavailable" in codes(install_preflight.preflight(**inputs))
