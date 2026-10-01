"""Single real synthetic immutable-release to compatible workspace tracer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from health_buddy import upgrade_activation
from health_buddy.app import App
from health_buddy.backup_crypto import keygen
from health_buddy.core.durability import atomic_bytes
from health_buddy.extension_jobs import run_event
from health_buddy.extension_registry import Registry
from health_buddy.runtime_bundle import create_bundle
from health_buddy.runtime_manifest import verify_source_identity
from health_buddy.runtime_release import create_release
from health_buddy.core.security_api import BearerProof
from health_buddy.security_runtime import open_runtime
from health_buddy.core.service_api import Request, ServiceError
from health_buddy.upgrade import stage
from health_buddy.upgrade_activation import activate
from tests.extension_fixtures import example, prepared
from tests.test_runtime_artifact import make_archive
from tests.test_runtime_bundle import git
from tests.test_runtime_context import context_fixture


def test_stage_verified_release_preserves_custom_metric_connector_and_authority(
    tmp_path: Path,
) -> None:
    runtime, owner, token, grant, _setup = prepared(tmp_path / "original")
    config = runtime.operations.config
    metric = example(config, "local.weekly-mass")
    source = metric / "src/metric.py"
    atomic_bytes(source, source.read_bytes().replace(b"fmean(values)", b"max(values)"))
    atomic_bytes(metric / "notes/OWNER.md", b"Synthetic retained owner notes\n")
    Registry(config).enable("local.weekly-mass", source_ids=("manual",))
    app = App.authenticated(config.root, proof=BearerProof(token), runtime=runtime)
    for day, weight in ((3, 150), (4, 160)):
        app.log_record(
            "measurement",
            [
                "--measured-at-local",
                f"2030-01-0{day}T09:00:00Z",
                "--weight-lb",
                str(weight),
            ],
        )
    event = {
        "eventId": "upgrade-before",
        "sourceId": "fabricated-water",
        "observedAt": "2030-01-03T09:00:00Z",
        "value": 250,
        "unit": "mL",
    }
    original_event = run_event(config, runtime, grant, "local.water-import", event)
    request = Request(
        "extensions.read",
        resource_id="local.weekly-mass",
        query={"from": "2030-01-01T00:00:00Z", "to": "2030-01-07T23:59:59Z"},
    )
    original_metric = runtime.operations.execute(owner.principal, request)
    assert original_metric.status == 200
    before = runtime.operations.journal.state()
    original_config = (config.root / "config.json").read_bytes()
    release_root = tmp_path / "synthetic-release"
    release_root.mkdir(mode=0o700)
    bundle, _downloads = context_fixture(release_root)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    labels = {
        "org.opencontainers.image.revision": identity.source_commit,
        "org.opencontainers.image.version": identity.package_version,
        "io.health-buddy.source-archive-sha256": identity.source_archive_sha256,
        "io.health-buddy.input-lock-sha256": hashlib.sha256(
            (bundle / "source/packaging/runtime-inputs.json").read_bytes()
        ).hexdigest(),
    }
    artifacts = release_root / "artifacts"
    artifacts.mkdir(mode=0o700)
    for architecture in ("amd64", "arm64"):
        make_archive(
            artifacts / f"health-buddy-linux-{architecture}.docker.tar",
            architecture=architecture,
            config_override={"config": {"Labels": labels}},
        )
    manifest = create_release(bundle, artifacts)
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    key = tmp_path / "upgrade.key"
    keygen(key)
    archive = tmp_path / "pre-upgrade.hbb"
    candidate = tmp_path / "candidate"
    receipt = stage(
        runtime,
        owner.principal,
        manifest,
        manifest_hash,
        "amd64",
        archive,
        key,
        candidate,
        confirm_quiesced=True,
    )
    assert receipt["target"]["manifestSha256"] == manifest_hash
    assert receipt["state"] == "staged_requires_explicit_activation"
    assert token.encode() not in archive.read_bytes()
    assert b"Synthetic retained owner notes" not in archive.read_bytes()
    copied = open_runtime(candidate)
    admitted = copied.security.authenticate(BearerProof(token))
    after = copied.operations.journal.state()
    assert after.identity == before.identity
    assert after.revision == before.revision
    assert (candidate / "config.json").read_bytes() == original_config
    assert (
        candidate / "personal/extensions/local.weekly-mass/src/metric.py"
    ).read_bytes() == source.read_bytes()
    assert (
        candidate / "personal/extensions/local.weekly-mass/notes/OWNER.md"
    ).read_bytes() == (metric / "notes/OWNER.md").read_bytes()
    assert (
        copied.operations.execute(admitted.principal, request).body
        == original_metric.body
    )
    replay = run_event(
        copied.operations.config, copied, grant, "local.water-import", event
    )
    assert replay["data"]["recordId"] == original_event["data"]["recordId"]
    next_event = {**event, "eventId": "upgrade-after", "value": 300}
    assert run_event(
        copied.operations.config, copied, grant, "local.water-import", next_event
    )["data"]["recordId"]
    assert runtime.operations.journal.state().revision == before.revision
    assert (config.root / "config.json").read_bytes() == original_config


def release_fixture(
    root: Path, variant: str, package_version: str | None = None
) -> Path:
    root.mkdir(mode=0o700)
    bundle, _downloads = context_fixture(root)
    if package_version is not None:
        repository = root / "repository"
        pyproject = repository / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text().replace("0.1.0.dev0", package_version)
        )
        git(repository, "add", "pyproject.toml")
        git(repository, "commit", "--quiet", "-m", "Synthetic version")
        bundle = root / "versioned-bundle"
        create_bundle(repository, git(repository, "rev-parse", "HEAD"), bundle)
    identity = verify_source_identity(
        bundle / "source", bundle / "release/source-manifest.json"
    )
    labels = {
        "org.opencontainers.image.revision": identity.source_commit,
        "org.opencontainers.image.version": identity.package_version,
        "io.health-buddy.source-archive-sha256": identity.source_archive_sha256,
        "io.health-buddy.input-lock-sha256": hashlib.sha256(
            (bundle / "source/packaging/runtime-inputs.json").read_bytes()
        ).hexdigest(),
    }
    artifacts = root / "artifacts"
    artifacts.mkdir(mode=0o700)
    for architecture in ("amd64", "arm64"):
        make_archive(
            artifacts / f"health-buddy-linux-{architecture}.docker.tar",
            architecture=architecture,
            config_override={
                "config": {"Labels": labels, "Env": ["SYNTHETIC=" + variant]}
            },
        )
    return create_release(bundle, artifacts)


_real_compose = upgrade_activation.compose


def activation_fixture(tmp_path, monkeypatch):
    runtime, owner, token, grant, _setup = prepared(tmp_path / "original")
    metric = example(runtime.operations.config, "local.weekly-mass")
    source = metric / "src/metric.py"
    atomic_bytes(source, source.read_bytes().replace(b"fmean(values)", b"max(values)"))
    Registry(runtime.operations.config).enable(
        "local.weekly-mass", source_ids=("manual",)
    )
    notes = metric / "notes/OWNER.md"
    atomic_bytes(notes, b"Synthetic private unchanged notes\n")
    previous = release_fixture(tmp_path / "previous-release", "previous")
    target = release_fixture(tmp_path / "target-release", "target")
    target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    previous_hash = hashlib.sha256(previous.read_bytes()).hexdigest()
    key = tmp_path / "key"
    keygen(key)
    candidate = tmp_path / "candidate"
    stage(
        runtime,
        owner.principal,
        target,
        target_hash,
        "amd64",
        tmp_path / "pre-upgrade.hbb",
        key,
        candidate,
        confirm_quiesced=True,
    )
    environment = tmp_path / "runtime.env"
    atomic_bytes(environment, b"synthetic previous runtime")
    calls = []

    def load(manifest, architecture, workspace, output, **kwargs):
        assert workspace == runtime.operations.config.root
        assert architecture == "amd64"
        assert kwargs["uid"] == kwargs["gid"] == 1000
        calls.append(("load", manifest))
        atomic_bytes(output, hashlib.sha256(manifest.read_bytes()).hexdigest().encode())

    def compose(docker, env, project, *arguments):
        assert project == "health-buddy-synthetic"
        assert env == environment
        assert arguments[-1] == "api"
        calls.append(arguments)
        return b""

    def running(
        docker, env, project, manifest, architecture, workspace, uid, gid, **kwargs
    ):
        assert workspace == runtime.operations.config.root
        assert uid == gid == 1000
        calls.append(("observed-running", manifest))
        if kwargs.get("allow_inactive"):
            return ""
        if manifest == target:
            assert env.read_bytes() == target_hash.encode()
        return "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest()

    # No daemon/container execution: these are the two reviewed host seams.
    monkeypatch.setattr(upgrade_activation, "load_release", load)
    monkeypatch.setattr(upgrade_activation, "compose", compose)
    monkeypatch.setattr(upgrade_activation, "running", running)
    arguments = (
        runtime,
        owner.principal,
        candidate,
        target,
        target_hash,
        "amd64",
        previous,
        previous_hash,
        environment,
        tmp_path / "synthetic-docker",
        "health-buddy-synthetic",
        1000,
        1000,
    )
    return arguments, calls, token, grant, notes


def test_activation_resume_repeat_and_binary_rollback_preserve_later_writes(
    tmp_path, monkeypatch
):
    arguments, calls, token, grant, notes = activation_fixture(tmp_path, monkeypatch)
    runtime = arguments[0]
    root = runtime.operations.config.root
    before = runtime.operations.journal.state()
    original_compose = upgrade_activation.compose
    failed = False

    def interrupted(docker, environment, project, *args):
        nonlocal failed
        if args[0] == "up" and not failed:
            failed = True
            raise OSError("synthetic failure payload must not persist")
        return original_compose(docker, environment, project, *args)

    monkeypatch.setattr(upgrade_activation, "compose", interrupted)
    with pytest.raises(ServiceError, match="upgrade_interrupted_resume_same_command"):
        activate(*arguments, confirm_quiesced=True)
    receipt_path = root / "operations/upgrade-activation.json"
    assert json.loads(receipt_path.read_bytes())["phase"] == "starting"
    assert b"synthetic failure payload" not in receipt_path.read_bytes()
    assert token.encode() not in receipt_path.read_bytes()
    resumed = activate(*arguments, confirm_quiesced=True)
    assert resumed["phase"] == "active"
    assert calls.count(("stop", "api")) == 1
    assert sum(item[0] == "load" for item in calls) == 1
    assert activate(*arguments, confirm_quiesced=True) == resumed
    event = {
        "eventId": "after-activation",
        "sourceId": "fabricated-water",
        "observedAt": "2030-01-04T09:00:00Z",
        "value": 350,
        "unit": "mL",
    }
    written = run_event(
        runtime.operations.config, runtime, grant, "local.water-import", event
    )
    after_write = runtime.operations.journal.state()
    assert after_write.revision > before.revision
    notes_bytes = notes.read_bytes()
    reverse = (
        *arguments[:3],
        arguments[6],
        arguments[7],
        arguments[5],
        arguments[3],
        arguments[4],
        *arguments[8:],
    )
    rolled_back = activate(*reverse, confirm_quiesced=True, rollback=True)
    assert rolled_back["phase"] == "rolled_back"
    assert runtime.operations.journal.state().revision == after_write.revision
    assert runtime.operations.journal.state().identity == before.identity
    assert notes.read_bytes() == notes_bytes
    assert runtime.security.authenticate(BearerProof(token))
    replay = run_event(
        runtime.operations.config, runtime, grant, "local.water-import", event
    )
    assert replay["data"]["recordId"] == written["data"]["recordId"]


@pytest.mark.parametrize("change", ["record", "personal", "config"])
def test_stale_snapshot_refuses_activation_before_any_host_action(
    tmp_path, monkeypatch, change
):
    arguments, calls, token, _grant, notes = activation_fixture(tmp_path, monkeypatch)
    runtime = arguments[0]
    if change == "record":
        app = App.authenticated(
            runtime.operations.config.root, proof=BearerProof(token), runtime=runtime
        )
        app.log_record(
            "measurement",
            ["--measured-at-local", "2030-01-04T09:00:00Z", "--weight-lb", "170"],
        )
    elif change == "personal":
        atomic_bytes(notes, b"Synthetic newer owner notes\n")
    else:
        path = runtime.operations.config.root / "config.json"
        value = json.loads(path.read_bytes())
        value["identity"]["displayName"] = "Synthetic changed owner"
        atomic_bytes(path, json.dumps(value).encode())
    with pytest.raises(ServiceError, match="upgrade_snapshot_stale_requires_restage"):
        activate(*arguments, confirm_quiesced=True)
    assert calls == []


def test_incompatible_extension_blocks_before_host_actions_preserving_source(
    tmp_path, monkeypatch
):
    arguments, calls, _token, _grant, notes = activation_fixture(tmp_path, monkeypatch)
    source = notes.parents[1] / "src/metric.py"
    raw = source.read_bytes() + b"\n# Synthetic unreviewed edit\n"
    atomic_bytes(source, raw)
    with pytest.raises(
        ServiceError, match="upgrade_extension_requires_review_or_disable"
    ):
        activate(*arguments, confirm_quiesced=True)
    assert calls == []
    assert source.read_bytes() == raw


@pytest.mark.parametrize(
    "interface", ["storage", "api", "phonePayload", "pairing", "extensions"]
)
def test_unsupported_interfaces_refuse_before_host_actions(
    tmp_path, monkeypatch, interface
):
    arguments, calls, _token, _grant, _notes = activation_fixture(tmp_path, monkeypatch)
    manifest = arguments[3]
    value = json.loads(manifest.read_bytes())
    value["interfaces"][interface] = 2
    manifest.write_text(json.dumps(value))
    changed = (
        *arguments[:4],
        hashlib.sha256(manifest.read_bytes()).hexdigest(),
        *arguments[5:],
    )
    with pytest.raises(
        ServiceError, match="upgrade_release_invalid_or_unsupported_interfaces"
    ):
        activate(*changed, confirm_quiesced=True)
    assert calls == []


def test_unsafe_binary_downgrade_rejected_before_host_actions(tmp_path, monkeypatch):
    arguments, calls, _token, _grant, _notes = activation_fixture(tmp_path, monkeypatch)
    newer = release_fixture(tmp_path / "newer-release", "newer", "2.0.0")
    changed = (
        *arguments[:6],
        newer,
        hashlib.sha256(newer.read_bytes()).hexdigest(),
        *arguments[8:],
    )
    with pytest.raises(
        ServiceError, match="upgrade_downgrade_requires_explicit_recorded_rollback"
    ):
        activate(*changed, confirm_quiesced=True)
    assert calls == []


def test_recorded_core_conflict_requires_review_and_retains_patch(
    tmp_path, monkeypatch
):
    arguments, calls, _token, _grant, _notes = activation_fixture(tmp_path, monkeypatch)
    runtime = arguments[0]
    root = runtime.operations.config.path("personal/forks/synthetic")
    root.mkdir(mode=0o700, parents=True)
    atomic_bytes(root / "change.patch", b"Synthetic owner patch never executed\n")
    metadata = {
        "schemaVersion": 1,
        "upstreamBase": json.loads(arguments[3].read_bytes())["sourceCommit"],
        "sourceCommit": "a" * 40,
        "sourceTree": "b" * 40,
        "patchFiles": ["change.patch"],
        "dirty": False,
        "conflicted": True,
        "buildRecipe": "owner recorded synthetic recipe",
        "tests": [],
    }
    atomic_bytes(root / "fork.json", json.dumps(metadata).encode())
    with pytest.raises(
        ServiceError, match="upgrade_core_fork_requires_rebase_and_review"
    ):
        activate(*arguments, confirm_quiesced=True)
    assert calls == []
    assert (
        root / "change.patch"
    ).read_bytes() == b"Synthetic owner patch never executed\n"


def test_interrupted_activation_can_explicitly_recover_recorded_previous_binary(
    tmp_path, monkeypatch
):
    arguments, _calls, _token, _grant, _notes = activation_fixture(
        tmp_path, monkeypatch
    )
    original_loader = upgrade_activation.load_release

    def interrupted(*args, **kwargs):
        raise OSError("synthetic load interruption")

    monkeypatch.setattr(upgrade_activation, "load_release", interrupted)
    with pytest.raises(ServiceError, match="upgrade_interrupted_resume_same_command"):
        activate(*arguments, confirm_quiesced=True)
    monkeypatch.setattr(upgrade_activation, "load_release", original_loader)
    reverse = (
        *arguments[:3],
        arguments[6],
        arguments[7],
        arguments[5],
        arguments[3],
        arguments[4],
        *arguments[8:],
    )
    receipt = activate(*reverse, confirm_quiesced=True, rollback=True, recover=True)
    assert receipt["phase"] == "rolled_back"
    assert (
        arguments[0].operations.journal.state().revision
        == receipt["activationRevision"]
    )


@pytest.mark.parametrize("mismatch", ["workspace", "user"])
def test_real_running_guard_refuses_other_installation_before_stop_or_load(
    tmp_path, monkeypatch, mismatch
):
    real_running = upgrade_activation.running
    arguments, calls, _token, _grant, _notes = activation_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(upgrade_activation, "running", real_running)
    artifact = upgrade_activation.selected_artifact(arguments[6], "amd64")
    workspace = arguments[0].operations.config.root
    source = (
        str(tmp_path / "another-owner") if mismatch == "workspace" else str(workspace)
    )
    user = "1001:1001" if mismatch == "user" else "1000:1000"
    responses = iter(
        [
            b"a" * 64 + b"\n",
            (
                artifact.loader_ids[0]
                + "\ntrue\n"
                + user
                + "\n"
                + json.dumps(source)
                + "\nbind\ntrue\n"
            ).encode(),
        ]
    )
    commands = []
    monkeypatch.setattr(upgrade_activation, "compose", _real_compose)
    monkeypatch.setattr(
        upgrade_activation, "docker_command", lambda _docker: ["synthetic-docker"]
    )

    original_run = upgrade_activation.subprocess.run

    def response(command, **kwargs):
        if command[0] != "synthetic-docker":
            return original_run(command, **kwargs)
        commands.append(command)
        assert kwargs["stdout"] == upgrade_activation.subprocess.PIPE
        assert "stop" not in command and "load" not in command and "up" not in command
        return SimpleNamespace(stdout=next(responses))

    monkeypatch.setattr(upgrade_activation.subprocess, "run", response)
    with pytest.raises(ServiceError, match="upgrade_running_installation_mismatch"):
        activate(*arguments, confirm_quiesced=True)
    assert len(commands) == 2
    assert commands[0][-3:] == ["ps", "--quiet", "api"]
    assert ".Config.User" in commands[1][-2]
    assert ".Mounts" in commands[1][-2]
    assert ".Config.Env" not in commands[1][-2]
    assert calls == []


@pytest.mark.parametrize("field", ["project", "environment", "uid", "gid"])
def test_terminal_repeat_binds_saved_installation_before_any_host_action(
    tmp_path, monkeypatch, field
):
    real_running = upgrade_activation.running
    arguments, calls, _token, _grant, _notes = activation_fixture(tmp_path, monkeypatch)
    activate(*arguments, confirm_quiesced=True)
    calls.clear()
    monkeypatch.setattr(upgrade_activation, "running", real_running)
    changed = list(arguments)
    index, value = {
        "project": (10, "health-buddy-another"),
        "environment": (8, tmp_path / "other-runtime.env"),
        "uid": (11, 1001),
        "gid": (12, 1001),
    }[field]
    changed[index] = value

    original_run = upgrade_activation.subprocess.run

    def unexpected(command, **kwargs):
        if command[0] != "synthetic-docker":
            return original_run(command, **kwargs)
        raise AssertionError("receipt mismatch must not invoke a host subprocess")

    monkeypatch.setattr(upgrade_activation.subprocess, "run", unexpected)
    with pytest.raises(ServiceError, match="upgrade_installation_binding_mismatch"):
        activate(*changed, confirm_quiesced=True)
    assert calls == []


def test_real_running_accepts_expected_installation_with_terminal_newline(
    tmp_path, monkeypatch
):
    real_running = upgrade_activation.running
    arguments, _calls, _token, _grant, _notes = activation_fixture(
        tmp_path, monkeypatch
    )
    artifact = upgrade_activation.selected_artifact(arguments[6], "amd64")
    workspace = arguments[0].operations.config.root
    responses = iter(
        [
            b"a" * 64 + b"\n",
            (
                artifact.loader_ids[0]
                + "\ntrue\n1000:1000\n"
                + json.dumps(str(workspace))
                + "\nbind\ntrue\n\n"
            ).encode(),
        ]
    )
    monkeypatch.setattr(upgrade_activation, "compose", _real_compose)
    monkeypatch.setattr(
        upgrade_activation, "docker_command", lambda _docker: ["synthetic-docker"]
    )
    original_run = upgrade_activation.subprocess.run

    def response(command, **kwargs):
        if command[0] != "synthetic-docker":
            return original_run(command, **kwargs)
        return SimpleNamespace(stdout=next(responses))

    monkeypatch.setattr(upgrade_activation.subprocess, "run", response)
    assert (
        real_running(
            arguments[9],
            arguments[8],
            arguments[10],
            arguments[6],
            "amd64",
            workspace,
            1000,
            1000,
        )
        == artifact.loader_ids[0]
    )


def test_resumed_stopping_refuses_other_active_workspace_and_allows_absent_api(
    tmp_path, monkeypatch
):
    real_running = upgrade_activation.running
    arguments, calls, _token, _grant, _notes = activation_fixture(tmp_path, monkeypatch)
    workspace = arguments[0].operations.config.root

    def interrupted(docker, environment, project, *args):
        assert args == ("stop", "api")
        raise OSError("synthetic interruption before stop")

    monkeypatch.setattr(upgrade_activation, "compose", interrupted)
    with pytest.raises(ServiceError, match="upgrade_interrupted_resume_same_command"):
        activate(*arguments, confirm_quiesced=True)
    receipt_path = workspace / "operations/upgrade-activation.json"
    assert json.loads(receipt_path.read_bytes())["phase"] == "stopping"
    calls.clear()
    previous = upgrade_activation.selected_artifact(arguments[6], "amd64")
    target = upgrade_activation.selected_artifact(arguments[3], "amd64")
    monkeypatch.setattr(upgrade_activation, "running", real_running)
    monkeypatch.setattr(upgrade_activation, "compose", _real_compose)
    monkeypatch.setattr(
        upgrade_activation, "docker_command", lambda _docker: ["synthetic-docker"]
    )
    responses = iter(
        [
            b"a" * 64 + b"\n",
            (
                previous.loader_ids[0]
                + "\ntrue\n1000:1000\n"
                + json.dumps(str(tmp_path / "another-owner"))
                + "\nbind\ntrue\n\n"
            ).encode(),
        ]
    )
    commands = []
    original_run = upgrade_activation.subprocess.run

    def response(command, **kwargs):
        if command[0] != "synthetic-docker":
            return original_run(command, **kwargs)
        commands.append(command)
        return SimpleNamespace(stdout=next(responses))

    monkeypatch.setattr(upgrade_activation.subprocess, "run", response)
    with pytest.raises(ServiceError, match="upgrade_running_installation_mismatch"):
        activate(*arguments, confirm_quiesced=True)
    assert calls == []
    assert len(commands) == 2
    assert all("stop" not in command and "load" not in command for command in commands)
    assert json.loads(receipt_path.read_bytes())["phase"] == "stopping"
    # The same durable phase can continue when the original API is absent.
    responses = iter(
        [
            b"",
            b"",
            b"",
            b"a" * 64 + b"\n",
            (
                target.loader_ids[0]
                + "\ntrue\n1000:1000\n"
                + json.dumps(str(workspace))
                + "\nbind\ntrue\n\n"
            ).encode(),
        ]
    )
    assert activate(*arguments, confirm_quiesced=True)["phase"] == "active"
    assert sum(item[0] == "load" for item in calls) == 1
