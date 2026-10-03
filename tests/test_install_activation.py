"""Prepare/load/start/reconcile using bounded synthetic CLI responses only."""

import json
import os
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


def fixture(tmp_path, monkeypatch, *, guided_owner=False):
    selected = inputs(tmp_path, monkeypatch)
    install_prepare.prepare(**selected)
    workspace = selected["workspace"]
    owner_note = workspace / "personal/OWNER.md"
    owner_note.write_text("synthetic personal work retained")
    if guided_owner:
        simulate_nonroot_owner(monkeypatch)
        install_owner.setup(
            journal=selected["journal"],
            owner_token=workspace / "secrets/synthetic-owner-token",
            origin="https://synthetic.example.test",
            owner_subject="synthetic-owner",
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
    runtime = open_runtime(workspace)
    before = identity_value(runtime.operations.journal.verify().identity)
    artifact = selected_artifact(selected["manifest"], "amd64")
    state = {
        "active": False,
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
                    if state["active"] or state["other"]
                    else b"",
                )
            assert arguments == ["up", "--detach", "--wait", "--no-deps", "api"]
            state["active"] = True
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
