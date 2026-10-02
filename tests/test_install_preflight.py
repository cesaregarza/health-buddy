"""Synthetic pinned release dry-run; no Docker/host mutation or health reads."""

import hashlib
import json
import shutil
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from health_buddy.install import preflight as install_preflight
from health_buddy.runtime.bundle import create_bundle
from health_buddy.runtime.manifest import verify_source_identity
from health_buddy.runtime.release import create_release
from tests.test_runtime_artifact import make_archive
from tests.test_runtime_bundle import git, plant_bytecode
from tests.test_runtime_context import context_fixture


def prepared(tmp_path, monkeypatch, *, maintenance=False):
    bundle, _ = context_fixture(tmp_path)
    if maintenance:
        from health_buddy.install.prepare import MAINTENANCE_REFERENCES

        repository = tmp_path / "repository"
        root = Path(__file__).resolve().parents[1]
        for name in (*MAINTENANCE_REFERENCES, "packaging/compose.yaml"):
            destination = repository / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((root / name).read_bytes())
        # Client readiness executes the adapter from this verified source tree.
        shutil.copytree(
            root / "src/health_buddy",
            repository / "src/health_buddy",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
        git(repository, "add", ".")
        git(repository, "commit", "--quiet", "-m", "Synthetic maintenance references")
        bundle = tmp_path / "maintenance-bundle"
        create_bundle(repository, git(repository, "rev-parse", "HEAD"), bundle)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    artifacts = tmp_path / "artifacts"
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
    manifest = create_release(bundle, artifacts)
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


@pytest.mark.parametrize("docker_args", [[], ["--docker"], ["--docker", ""]])
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
