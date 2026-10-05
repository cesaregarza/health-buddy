"""Prepare/load/start/reconcile using bounded synthetic CLI responses only."""

import json
import os
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from health_buddy.core.domain import identity_value
from health_buddy.core.service_api import ServiceError
from health_buddy.install import activation as install_activation
from health_buddy.install import owner as install_owner
from health_buddy.install import prepare as install_prepare
from health_buddy.runtime.release import selected_artifact
from health_buddy.security.runtime import open_runtime, setup_security
from tests.test_install_prepare import inputs


class OwnerStat:
    """Keep real inode/timestamp/mode evidence while simulating one OS owner."""

    def __init__(self, original):
        self.original = original
        self.st_uid = 1000 if original.st_uid == 0 else original.st_uid
        self.st_gid = 1000 if original.st_gid == 0 else original.st_gid

    def __getattr__(self, name):
        return getattr(self.original, name)

    def __eq__(self, other):
        return isinstance(other, OwnerStat) and self.original == other.original

    def __iter__(self):
        fields = list(self.original)
        fields[4:6] = [self.st_uid, self.st_gid]
        return iter(fields)

    def __getitem__(self, index):
        return tuple(self)[index]

    def __len__(self):
        return len(self.original)


def simulate_nonroot_owner(monkeypatch):
    # Real security readiness continues checking mode, ownership, bindings,
    # journal and Git refs. Only the OS identity/stat boundary is synthetic.
    for name in ("stat", "lstat", "fstat"):
        original = getattr(os, name)

        def owned(*args, _original=original, **kwargs):
            return OwnerStat(_original(*args, **kwargs))

        monkeypatch.setattr(os, name, owned)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(os, "getegid", lambda: 1000)


def _fixture_authority(selected, monkeypatch, guided_owner, local_only):
    workspace = selected["workspace"]
    if guided_owner:
        simulate_nonroot_owner(monkeypatch)
        install_owner.setup(
            journal=selected["journal"],
            owner_token=workspace / "secrets/synthetic-owner-token",
            origin="https://health-buddy.local"
            if local_only
            else "https://synthetic.example.test",
            owner_subject="owner" if local_only else "synthetic-owner",
            confirm_owner_setup=True,
        )
    else:
        configuration = workspace / "config.json"
        values = json.loads(configuration.read_bytes())
        values["security"].update(
            ingress="tailscale-uds",
            externalOrigin="https://synthetic.example.test",
            ownerSubject="synthetic-owner",
            socketPath="security/runtime/http.sock",
        )
        configuration.write_text(json.dumps(values))
        setup_security(workspace, workspace / "secrets/synthetic-owner-proof")
        simulate_nonroot_owner(monkeypatch)


def fixture(tmp_path, monkeypatch, *, guided_owner=False, local_only=False):
    selected = inputs(tmp_path, monkeypatch)
    install_prepare.prepare(**selected)
    workspace = selected["workspace"]
    owner_note = workspace / "personal/OWNER.md"
    owner_note.write_text("synthetic personal work retained")
    _fixture_authority(selected, monkeypatch, guided_owner, local_only)
    runtime = open_runtime(workspace)
    before = identity_value(runtime.operations.journal.verify().identity)
    artifact = selected_artifact(selected["manifest"], "amd64")
    state = {
        "active": False,
        "created": False,
        "other": False,
        "lost": None,
        "health": "healthy",
        "calls": [],
    }
    original_run = subprocess.run

    def response(command, **kwargs):
        if command[0] != str(selected["docker"]):
            return original_run(command, **kwargs)
        assert command[1:3] == ["--host", "unix:///run/docker.sock"]
        assert kwargs["timeout"] <= 180
        retained = json.loads(selected["journal"].read_bytes())
        assert retained["activation"]["binding"]["identity"] == before
        state["calls"].append(command)
        if command[3:5] == ["image", "load"]:
            if state["lost"] == "load":
                state["lost"] = None
                raise subprocess.TimeoutExpired(command, 180)
            return SimpleNamespace(returncode=0, stdout=b"")
        if command[3:5] == ["image", "inspect"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    artifact.loader_ids[0]
                    + "\nlinux\namd64\n"
                    + json.dumps(["sha256:" + item for item in artifact.diff_ids])
                    + "\n"
                ).encode(),
            )
        if command[3] == "compose":
            arguments = command[10:]
            if arguments[0] == "ps":
                return SimpleNamespace(
                    returncode=0,
                    stdout=b"a" * 64 + b"\n"
                    if state["active"]
                    or state["other"]
                    or ("--all" in arguments and state["created"])
                    else b"",
                )
            if arguments == ["stop", "--timeout", "30", "api"]:
                state["active"] = False
                return SimpleNamespace(returncode=0, stdout=b"")
            assert arguments == ["up", "--detach", "--wait", "--no-deps", "api"]
            state["active"] = True
            state["created"] = True
            if state["lost"] == "start":
                state["lost"] = None
                raise subprocess.TimeoutExpired(command, 180)
            return SimpleNamespace(returncode=0, stdout=b"")
        assert command[3] == "inspect"
        mount = workspace if not state["other"] else tmp_path / "other-owner"
        return SimpleNamespace(
            returncode=0,
            stdout=(
                artifact.loader_ids[0]
                + "\ntrue\n1000:1000\n"
                + json.dumps(str(mount))
                + "\nbind\ntrue\n"
                + (state["health"] + "\n" if ".State.Health" in command[5] else "")
            ).encode(),
        )

    monkeypatch.setattr(subprocess, "run", response)
    arguments = dict(
        journal=selected["journal"],
        environment=selected["journal"].parent / "runtime.env",
        project="health-buddy-synthetic",
        uid=1000,
        gid=1000,
        confirm_local_daemon=True,
        confirm_quiesced=True,
    )
    return arguments, state, selected, before, owner_note


@pytest.mark.parametrize("lost", [None, "load", "start"])
def test_prepare_activation_and_lost_ack_resume_preserve_workspace(
    tmp_path, monkeypatch, lost
):
    arguments, state, selected, before, note = fixture(tmp_path, monkeypatch)
    state["lost"] = lost
    config = (selected["workspace"] / "config.json").read_bytes()
    if lost:
        with pytest.raises(
            ServiceError, match="install_activation_" + lost + "_interrupted"
        ):
            install_activation.activate(**arguments)
        phase = json.loads(selected["journal"].read_bytes())["activation"]["phase"]
        assert phase == ("loading" if lost == "load" else "starting")
    value = install_activation.activate(**arguments)
    assert value["runtimeActivated"] and not value["connected"]
    counts = (
        sum("up" in item for item in state["calls"]),
        sum("load" in item for item in state["calls"]),
    )
    assert counts == (1, 2 if lost == "load" else 1)
    assert install_activation.activate(**arguments) == value
    assert counts == (
        sum("up" in item for item in state["calls"]),
        sum("load" in item for item in state["calls"]),
    )
    runtime = open_runtime(selected["workspace"])
    assert identity_value(runtime.operations.journal.verify().identity) == before
    assert runtime.operations.journal.verify().revision == 0
    assert note.read_text() == "synthetic personal work retained"
    assert (selected["workspace"] / "config.json").read_bytes() == config


def test_unrelated_project_and_changed_binding_refuse_without_host_writes(
    tmp_path, monkeypatch
):
    arguments, state, selected, _before, note = fixture(tmp_path, monkeypatch)
    state["other"] = True
    with pytest.raises(ServiceError, match="project_not_empty"):
        install_activation.activate(**arguments)
    assert not any("up" in item or "load" in item for item in state["calls"])
    state["other"] = False
    install_activation.activate(**arguments)
    retained = selected["journal"].read_bytes()
    state["calls"].clear()
    with pytest.raises(ServiceError, match="original_binding"):
        install_activation.activate(**{**arguments, "project": "health-buddy-another"})
    assert state["calls"] == [] and selected["journal"].read_bytes() == retained
    state["other"] = True
    with pytest.raises(ServiceError, match="running_installation_mismatch"):
        install_activation.activate(**arguments)
    assert not any("up" in item or "load" in item for item in state["calls"])
    assert note.read_text() == "synthetic personal work retained"


def test_owner_admission_and_environment_edit_refuse(tmp_path, monkeypatch, capsys):
    arguments, state, _selected, _before, _note = fixture(tmp_path, monkeypatch)
    with pytest.raises(ServiceError, match="owner_admission"):
        install_activation.activate(**{**arguments, "confirm_local_daemon": False})
    with pytest.raises(ServiceError, match="nonroot_identity"):
        install_activation.activate(**{**arguments, "uid": 0})
    assert state["calls"] == []
    activation_args = [
        "--journal",
        str(arguments["journal"]),
        "--environment",
        str(arguments["environment"]),
        "--project",
        arguments["project"],
        "--uid",
        "1000",
        "--gid",
        "1000",
        "--confirm-local-daemon",
        "--confirm-quiesced",
    ]
    journal_before = arguments["journal"].read_bytes()
    arguments["environment"].write_text("synthetic unrelated file")
    assert install_activation.main(activation_args) == 2
    conflict = json.loads(capsys.readouterr().out)
    assert conflict["code"] == "install_activation_environment_unowned"
    assert conflict["conflictingPath"] == str(arguments["environment"])
    assert "If you created this file" in conflict["recovery"]
    assert "remove only this file" in conflict["recovery"]
    assert arguments["environment"].read_text() == "synthetic unrelated file"
    assert arguments["journal"].read_bytes() == journal_before
    assert state["calls"] == []
    arguments["environment"].unlink()
    assert install_activation.main(activation_args) == 0
    assert json.loads(capsys.readouterr().out)["runtimeActivated"]
    arguments["environment"].write_text("synthetic owner edit retained")
    state["calls"].clear()
    assert install_activation.main(activation_args) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["code"] == "install_activation_environment_changed"
    assert str(arguments["environment"]) not in json.dumps(value)
    assert (
        state["calls"] == []
        and arguments["environment"].read_text() == "synthetic owner edit retained"
    )


def test_default_prepared_workspace_refuses_before_any_daemon_contact(
    tmp_path, monkeypatch, capsys
):
    selected = inputs(tmp_path, monkeypatch)
    install_prepare.prepare(**selected)
    simulate_nonroot_owner(monkeypatch)
    original_run = subprocess.run

    def no_daemon(command, **kwargs):
        assert command[0] != str(selected["docker"])
        return original_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", no_daemon)
    before = selected["journal"].read_bytes()
    arguments = [
        "--journal",
        str(selected["journal"]),
        "--environment",
        str(selected["journal"].parent / "runtime.env"),
        "--project",
        "health-buddy-synthetic",
        "--uid",
        "1000",
        "--gid",
        "1000",
        "--confirm-local-daemon",
        "--confirm-quiesced",
    ]
    assert install_activation.main(arguments) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["code"] == "install_activation_requires_managed_owner_setup"
    assert "explicit security setup" in value["recovery"]
    configuration = selected["workspace"] / "config.json"
    values = json.loads(configuration.read_bytes())
    values["security"].update(
        ingress="tailscale-uds",
        externalOrigin="https://synthetic.example.test",
        ownerSubject="synthetic-owner",
        socketPath="security/runtime/http.sock",
    )
    configuration.write_text(json.dumps(values))
    assert install_activation.main(arguments) == 2
    assert (
        json.loads(capsys.readouterr().out)["code"]
        == "install_activation_requires_ready_owner_authority"
    )
    assert selected["journal"].read_bytes() == before


def test_lost_start_ack_waits_for_healthy_without_restarting(tmp_path, monkeypatch):
    arguments, state, selected, _identity, _note = fixture(tmp_path, monkeypatch)
    state["lost"] = "start"
    with pytest.raises(ServiceError, match="start_interrupted"):
        install_activation.activate(**arguments)
    for health in ("starting", "unhealthy"):
        state["health"] = health
        with pytest.raises(
            ServiceError, match="install_activation_runtime_not_healthy"
        ) as refused:
            install_activation.activate(**arguments)
        assert refused.value.retryable
        assert (
            json.loads(selected["journal"].read_bytes())["activation"]["phase"]
            == "starting"
        )
    state["health"] = "healthy"
    assert install_activation.activate(**arguments)["runtimeActivated"]
    assert sum("up" in item for item in state["calls"]) == 1
    assert sum("load" in item for item in state["calls"]) == 1


def activation_cli(arguments):
    return [
        "--journal",
        str(arguments["journal"]),
        "--environment",
        str(arguments["environment"]),
        "--project",
        arguments["project"],
        "--uid",
        str(arguments["uid"]),
        "--gid",
        str(arguments["gid"]),
        "--confirm-local-daemon",
        "--confirm-quiesced",
    ]


def test_active_repeat_observes_binding_without_opening_stores(tmp_path, monkeypatch):
    arguments, state, selected, _identity, note = fixture(tmp_path, monkeypatch)
    activated = install_activation.activate(**arguments)
    paths = [
        arguments["journal"],
        arguments["environment"],
        note,
        selected["workspace"] / "config.json",
    ]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    state["calls"].clear()

    def no_store(*args, **kwargs):
        pytest.fail("an active rerun must not open or probe a store")

    monkeypatch.setattr(install_activation, "open_runtime", no_store)
    monkeypatch.setattr(sqlite3, "connect", no_store)
    assert install_activation.activate(**arguments) == activated
    assert all("up" not in call and "load" not in call for call in state["calls"])
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths
    } == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("project", "health-buddy-changed"),
        ("uid", 1001),
        ("gid", 1001),
        ("uid", 0),
        ("confirm_local_daemon", False),
        ("confirm_quiesced", False),
    ],
)
def test_active_repeat_rejects_changed_or_invalid_admission(
    tmp_path, monkeypatch, field, value
):
    arguments, state, _selected, _identity, _note = fixture(tmp_path, monkeypatch)
    install_activation.activate(**arguments)
    before = arguments["journal"].read_bytes()
    state["calls"].clear()
    with pytest.raises(ServiceError):
        install_activation.activate(**{**arguments, field: value})
    assert state["calls"] == []
    assert arguments["journal"].read_bytes() == before


@pytest.mark.parametrize(
    "changed",
    ["identity", "authority", "permissions", "environment", "compose", "manifest"],
)
def test_active_repeat_rejects_changed_metadata_before_daemon_contact(
    tmp_path, monkeypatch, capsys, changed
):
    arguments, state, selected, _identity, _note = fixture(tmp_path, monkeypatch)
    install_activation.activate(**arguments)
    workspace = selected["workspace"]
    if changed == "identity":
        path = workspace / "identity.json"
        value = json.loads(path.read_bytes())
        value["restoreEpoch"] = "synthetic-changed-epoch"
        path.write_text(json.dumps(value))
    elif changed == "authority":
        (workspace / "security/epoch.json").unlink()
    elif changed == "permissions":
        (workspace / "security/authority.sqlite").chmod(0o644)
    elif changed == "environment":
        arguments["environment"].write_text("synthetic changed environment")
    elif changed == "compose":
        (selected["bundle"] / "source/packaging/compose.yaml").write_text(
            "synthetic changed compose"
        )
    else:
        selected["manifest"].write_text("{}")
    before = arguments["journal"].read_bytes()
    state["calls"].clear()
    assert install_activation.main(activation_cli(arguments)) == 2
    assert json.loads(capsys.readouterr().out)["runtimeActivated"] is False
    assert state["calls"] == []
    assert arguments["journal"].read_bytes() == before


@pytest.mark.parametrize("health", ["starting", "unhealthy", "missing", "unrelated"])
def test_active_repeat_never_claims_success_for_lost_or_unhealthy_runtime(
    tmp_path, monkeypatch, capsys, health
):
    arguments, state, _selected, _identity, _note = fixture(tmp_path, monkeypatch)
    install_activation.activate(**arguments)
    if health == "missing":
        state["active"] = state["created"] = False
    elif health == "unrelated":
        state["other"] = True
    else:
        state["health"] = health
    before = arguments["journal"].read_bytes()
    state["calls"].clear()
    assert install_activation.main(activation_cli(arguments)) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["runtimeActivated"] is False
    assert value["code"] != "install_runtime_store_not_ready"
    assert all("up" not in call and "load" not in call for call in state["calls"])
    assert arguments["journal"].read_bytes() == before


def test_transient_activation_store_refusal_keeps_same_selection_retryable(
    tmp_path, monkeypatch, capsys
):
    arguments, state, selected, _identity, _note = fixture(tmp_path, monkeypatch)
    before = selected["journal"].read_bytes()
    original = install_activation.open_runtime

    def starting_store(*args, **kwargs):
        raise ServiceError(503, "source_unavailable", retryable=True)

    monkeypatch.setattr(install_activation, "open_runtime", starting_store)
    assert install_activation.main(activation_cli(arguments)) == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["code"] == "install_runtime_store_not_ready"
    assert refusal["retryable"] is True
    assert refusal["runtimeActivated"] is False
    assert "starting or temporarily locked" in refusal["recovery"]
    assert "wait ten seconds" in refusal["recovery"]
    assert "same command again unchanged" in refusal["recovery"]
    assert selected["journal"].read_bytes() == before
    assert state["calls"] == []
    monkeypatch.setattr(install_activation, "open_runtime", original)
    assert install_activation.main(activation_cli(arguments)) == 0
    assert json.loads(capsys.readouterr().out)["runtimeActivated"] is True


def test_claimed_active_phase_still_requires_a_bound_running_runtime(
    tmp_path, monkeypatch, capsys
):
    arguments, state, selected, _identity, _note = fixture(tmp_path, monkeypatch)
    state["lost"] = "load"
    with pytest.raises(ServiceError, match="load_interrupted"):
        install_activation.activate(**arguments)
    retained = json.loads(selected["journal"].read_bytes())
    retained["activation"]["phase"] = "active"
    selected["journal"].write_text(json.dumps(retained))
    arguments["environment"].write_bytes(
        install_activation.runtime_environments(
            retained["activation"]["binding"], selected["manifest"]
        )[0]
    )
    arguments["environment"].chmod(0o600)
    before = selected["journal"].read_bytes()
    state["calls"].clear()
    assert install_activation.main(activation_cli(arguments)) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["code"] == "install_activation_recorded_runtime_missing"
    assert refused["runtimeActivated"] is False
    assert selected["journal"].read_bytes() == before
    assert all("up" not in call and "load" not in call for call in state["calls"])
