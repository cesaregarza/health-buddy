"""Owned exact-container lifecycle and explicit same-actor credential recovery."""

import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest

from health_buddy.install import agent as install_agent
from health_buddy.install import remove as install_remove
from health_buddy.install import status as install_status
from health_buddy.security.authority import SecurityAuthority
from health_buddy.core.security_api import AgentGrant, SecurityRequest
from health_buddy.core.service_api import ServiceError
from tests.test_install_agent import connection_fixture


def removal_fixture(tmp_path, monkeypatch):
    connection, selected, _identity, note = connection_fixture(tmp_path, monkeypatch)
    install_agent.setup(**connection)
    record = json.loads(selected["journal"].read_bytes())
    active = record["activation"]["binding"]
    runtime, admitted = install_agent.owner(record)
    runtime.security.execute(
        admitted.principal,
        SecurityRequest(
            "grants.create",
            payload=AgentGrant(
                "Synthetic unrelated retained agent",
                ("records:read",),
                (),
                ("manual",),
                ("weight",),
                None,
            ),
            identity=admitted.client.identity,
        ),
    )
    state = {"present": True, "running": True, "lost": None, "drift": None, "calls": []}
    original_run = subprocess.run

    def response(command, **kwargs):
        if command[0] != active["docker"]:
            return original_run(command, **kwargs)
        if command[3] == "compose" and command[10:] == [
            "ps",
            "--all",
            "--quiet",
            "api",
        ]:
            return SimpleNamespace(
                stdout=(
                    (b"b" if state["drift"] == "replacement" else b"a") * 64 + b"\n"
                )
                if state["present"]
                else b"",
                returncode=0,
            )
        if command[3:5] == ["container", "ls"]:
            assert command[-1] == "id=" + "a" * 64
            return SimpleNamespace(
                stdout=(b"a" * 64 + b"\n") if state["present"] else b"", returncode=0
            )
        if command[3] == "inspect" and command[5] == install_remove.FORMAT:
            rows = [
                "a" * 64,
                record["activation"]["runningImageId"],
                "true" if state["running"] else "false",
                "1000:1000",
                json.dumps(str(selected["workspace"])),
                "bind",
                "true",
                active["project"],
                "api",
            ]
            if state["drift"] == "mount":
                rows[4] = json.dumps(str(tmp_path / "foreign"))
            if state["drift"] == "service":
                rows[8] = "foreign"
            return SimpleNamespace(
                stdout=("\n".join(rows) + "\n").encode(), returncode=0
            )
        if command[3] in ("stop", "rm"):
            assert command[-1] == "a" * 64
            assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["timeout"] == 45
            assert (
                json.loads(selected["journal"].read_bytes())["removal"]["phase"]
                == "container_pending"
            )
            state["calls"].append(command[3:])
            if command[3] == "stop":
                state["running"] = False
            else:
                assert not state["running"]
                state["present"] = False
            if state["lost"] == command[3]:
                state["lost"] = None
                raise subprocess.TimeoutExpired("synthetic owned container", 45)
            return SimpleNamespace(stdout=b"", returncode=0)
        return original_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", response)
    arguments = dict(
        journal=selected["journal"],
        policy=connection["policy"],
        confirm_remove=True,
        confirm_local_daemon=True,
        confirm_serve=True,
        confirm_quiesced=True,
    )
    return arguments, connection, selected, note, state


@pytest.mark.parametrize("lost", [None, "stop", "rm", "grant", "client"])
def test_owned_removal_resumes_retaining_data_and_private_recovery(
    tmp_path, monkeypatch, lost
):
    arguments, connection, selected, note, state = removal_fixture(
        tmp_path, monkeypatch
    )
    retained = {
        path: path.read_bytes()
        for path in (
            connection["agent_token"],
            connection["settings"],
            connection["policy"],
            note,
            selected["workspace"] / "config.json",
            selected["workspace"] / "identity.json",
        )
    }
    if lost in ("stop", "rm"):
        state["lost"] = lost
    if lost == "grant":
        original = SecurityAuthority.execute

        def revoke(authority, principal, request):
            reply = original(authority, principal, request)
            if request.action == "grants.revoke":
                raise OSError("synthetic lost revoke acknowledgement")
            return reply

        monkeypatch.setattr(SecurityAuthority, "execute", revoke)
    if lost == "client":
        original_connect = install_remove.connect

        def disconnect(*args, **kwargs):
            original_connect(*args, **kwargs)
            if not kwargs.get("check_only"):
                raise OSError("synthetic lost client removal acknowledgement")

        monkeypatch.setattr(install_remove, "connect", disconnect)
    if lost:
        with pytest.raises((ServiceError, OSError)):
            install_remove.remove(**arguments)
    if lost == "grant":
        monkeypatch.setattr(SecurityAuthority, "execute", original)
        assert not install_status.status(journal=selected["journal"])[
            "agentGrantRetained"
        ]
    if lost == "client":
        monkeypatch.setattr(install_remove, "connect", original_connect)
    assert install_remove.remove(**arguments)["removed"]
    calls = deepcopy(state["calls"])
    assert install_remove.remove(**arguments)["removed"]
    assert (
        state["calls"]
        == calls
        == [["stop", "--time", "30", "a" * 64], ["rm", "a" * 64]]
    )
    assert all(path.read_bytes() == value for path, value in retained.items())
    assert (selected["bundle"] / "source/docs/agent-guide.md").exists()
    assert (
        connection["client_config"].read_text()
        == 'model = "synthetic-kept"\n# owner comment\n'
    )
    assert not (connection["skill_directory"] / "SKILL.md").exists()
    summary = install_status.status(journal=selected["journal"])
    assert summary["removalLastCompleted"] and not summary["agentGrantRetained"]
    assert (
        not summary["runtimeLastActive"]
        and not summary["clientConfigurationLastPrepared"]
    )
    runtime, admitted = install_agent.owner(
        json.loads(selected["journal"].read_bytes())
    )
    assert runtime.operations.journal.verify().revision == 0
    inventory = install_agent.actors(runtime, admitted)
    assert len(inventory) == 2
    assert [
        actor["active"]
        for actor in inventory
        if actor["name"] == "Synthetic unrelated retained agent"
    ] == [True]
    assert [
        actor["active"]
        for actor in inventory
        if actor["name"] == "Synthetic Health Buddy agent"
    ] == [False]
    with pytest.raises(ServiceError, match="removal_requires_owner_lifecycle_review"):
        install_agent.setup(**connection)


@pytest.mark.parametrize("drift", ["mount", "service", "client"])
def test_removal_refuses_drift_before_any_mutation(tmp_path, monkeypatch, drift):
    arguments, connection, selected, _note, state = removal_fixture(
        tmp_path, monkeypatch
    )
    if drift == "client":
        path = connection["client_config"]
        path.write_bytes(
            path.read_bytes().replace(
                b"startup_timeout_sec = 15", b"startup_timeout_sec = 16"
            )
        )
    else:
        state["drift"] = drift
    journal = selected["journal"].read_bytes()
    config = connection["client_config"].read_bytes()
    with pytest.raises(
        ServiceError, match=r"ownership_changed|integration_locally_changed"
    ):
        install_remove.remove(**arguments)
    assert not state["calls"]
    assert selected["journal"].read_bytes() == journal
    assert connection["client_config"].read_bytes() == config


@pytest.mark.parametrize("drift", ["mount", "replacement"])
def test_stopped_container_drift_after_lost_stop_refuses_removal(
    tmp_path, monkeypatch, drift
):
    arguments, _connection, _selected, _note, state = removal_fixture(
        tmp_path, monkeypatch
    )
    state["lost"] = "stop"
    with pytest.raises(ServiceError, match="container_action_interrupted"):
        install_remove.remove(**arguments)
    state["drift"] = drift
    with pytest.raises(
        ServiceError, match=r"container_ownership_changed|container_replaced"
    ):
        install_remove.remove(**arguments)
    assert [command[0] for command in state["calls"]] == ["stop"]


@pytest.mark.parametrize("lost_rotation", [False, True])
def test_explicit_missing_secret_rotation_retains_actor_and_epoch(
    tmp_path, monkeypatch, lost_rotation
):
    connection, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)

    def interrupt(point):
        if point == "grant_committed":
            raise OSError("synthetic lost one-time secret")

    with pytest.raises(OSError):
        install_agent.setup(**connection, fault=interrupt)
    with pytest.raises(ServiceError, match="requires_owner_rotation"):
        install_agent.setup(**connection)
    bound = json.loads(selected["journal"].read_bytes())["agentSetup"]
    if lost_rotation:

        def lost(point):
            if point == "rotation_committed":
                raise OSError("synthetic lost rotation delivery")

        with pytest.raises(OSError):
            install_agent.setup(
                **connection, rotate_pending_missing_secret=True, fault=lost
            )
        with pytest.raises(ServiceError, match="requires_owner_rotation"):
            install_agent.setup(**connection)
    assert install_agent.setup(**connection, rotate_pending_missing_secret=True)[
        "clientConfigurationPrepared"
    ]
    token = connection["agent_token"].read_bytes()
    assert install_agent.setup(**connection, rotate_pending_missing_secret=True)[
        "clientConfigurationPrepared"
    ]
    assert connection["agent_token"].read_bytes() == token
    actual = json.loads(selected["journal"].read_bytes())["agentSetup"]
    assert actual["actorId"] == bound["actorId"]
    assert actual["binding"] == bound["binding"]
    runtime, admitted = install_agent.owner(
        json.loads(selected["journal"].read_bytes())
    )
    assert len(install_agent.actors(runtime, admitted)) == 1


def test_explicit_rotation_refuses_existing_empty_file(tmp_path, monkeypatch):
    connection, _selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    install_agent.setup(**connection)
    connection["agent_token"].write_bytes(b"")
    with pytest.raises(ServiceError, match="requires_owner_rotation"):
        install_agent.setup(**connection, rotate_pending_missing_secret=True)
    assert connection["agent_token"].read_bytes() == b""
