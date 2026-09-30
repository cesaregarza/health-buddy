"""Explicit removed-installation review with real authority and fake host CLIs."""

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from health_buddy import install_activation, install_agent, install_https, install_rearm, install_remove
from health_buddy.domain import encode
from health_buddy.durability import atomic_bytes
from health_buddy.security_api import BearerProof
from health_buddy.service_api import ServiceError
from tests.test_install_remove import removal_fixture


def fixture(tmp_path, monkeypatch):
    removal, connection, selected, note, host = removal_fixture(tmp_path, monkeypatch)
    workspace = selected["workspace"]
    personal = workspace / "personal/synthetic-owner"
    personal.mkdir(mode=0o700)
    atomic_bytes(personal / "metric.py", b"# Synthetic retained custom metric\n")
    atomic_bytes(personal / "state.json", b'{"synthetic":true}\n')
    atomic_bytes(personal / "test.sh", b"#!/bin/sh\n# Never executed by reinstall.\n")
    (personal / "test.sh").chmod(0o700)
    runtime, owner = install_agent.owner(json.loads(selected["journal"].read_bytes()))
    original_state = runtime.operations.journal.verify()
    original_files = runtime.operations.manual.snapshot()
    original_token = connection["agent_token"].read_text().strip()
    install_remove.remove(**removal)
    record = json.loads(selected["journal"].read_bytes())
    fresh_policy = connection["policy"].parent / "fresh-policy.json"
    policy = json.loads(connection["policy"].read_bytes())
    policy["name"] = "Synthetic reviewed replacement agent"
    atomic_bytes(fresh_policy, encode(policy))
    request = dict(
        journal=selected["journal"], original_policy=connection["policy"],
        expected_removed_sha256=hashlib.sha256(selected["journal"].read_bytes()).hexdigest(),
        policy=fresh_policy, agent_token=fresh_policy.parent / "fresh-token",
        settings=fresh_policy.parent / "fresh-settings.json", retry_root=fresh_policy.parent / "fresh-retries",
        confirm_reinstall=True, confirm_local_daemon=True, confirm_serve=True,
        confirm_quiesced=True, confirm_fresh_grant=True, acknowledge_ai_egress=True,
    )
    return removal, connection, selected, note, host, record, request, original_state, original_files, original_token


def test_reviewed_reinstall_preserves_personal_owner_revocation_and_inert_repeat(tmp_path, monkeypatch):
    _, connection, selected, note, host, record, request, original_state, original_files, original_token = fixture(tmp_path, monkeypatch)
    workspace = selected["workspace"]
    preserved = {path: (path.read_bytes(), path.stat().st_mode & 0o777)
                 for path in (note, workspace / "identity.json", workspace / "config.json",
                              *list((workspace / "personal/synthetic-owner").iterdir()),
                              connection["agent_token"], connection["settings"])}
    active = record["activation"]["binding"]
    activation = dict(journal=selected["journal"], environment=active["environment"],
                      project=active["project"], uid=active["uid"], gid=active["gid"],
                      confirm_local_daemon=True, confirm_quiesced=True)
    activation["environment"] = Path(activation["environment"])
    with pytest.raises(ServiceError, match="removal_requires_owner_lifecycle_review"):
        install_activation.activate(**activation)
    with pytest.raises(ServiceError, match="removal_requires_owner_lifecycle_review"):
        install_agent.setup(**connection)
    first = install_rearm.rearm(**request)
    assert first["rearmed"] and not first["duplicate"] and first["priorGrantRevoked"]
    rearmed = selected["journal"].read_bytes()
    assert install_rearm.rearm(**request)["duplicate"]
    assert selected["journal"].read_bytes() == rearmed
    assert host["calls"] == [["stop", "--time", "30", "a" * 64], ["rm", "a" * 64]]

    # Only the existing supported activation flow can create a fresh container.
    original_run = subprocess.run
    state = {"started": False}
    def response(command, **kwargs):
        if command[0] == active["docker"] and command[3] == "compose":
            if command[10] == "ps":
                return SimpleNamespace(returncode=0, stdout=b"b" * 64 + b"\n" if state["started"] else b"")
            if command[10] == "up":
                state["started"] = True
        return original_run(command, **kwargs)
    monkeypatch.setattr(subprocess, "run", response)
    assert install_activation.activate(**activation)["runtimeActivated"]
    https = record["privateHttps"]["binding"]
    install_https.route(
        journal=selected["journal"], tailscale=Path(https["tailscale"]),
        daemon_socket=Path(https["daemonSocket"]), action="setup",
        confirm_local_tailscale=True, confirm_serve=True, confirm_quiesced=True,
    )
    fresh = dict(connection, policy=request["policy"], agent_token=request["agent_token"],
                 settings=request["settings"], retry_root=request["retry_root"])
    install_agent.setup(**fresh)
    current = selected["journal"].read_bytes()
    runtime, owner = install_agent.owner(json.loads(current))
    state_after = runtime.operations.journal.verify()
    assert state_after == original_state and runtime.operations.manual.snapshot() == original_files
    assert preserved == {path: (path.read_bytes(), path.stat().st_mode & 0o777) for path in preserved}
    with pytest.raises(ServiceError) as old:
        runtime.security.authenticate(BearerProof(original_token))
    assert old.value.status == 401
    token = runtime.security.authenticate(BearerProof(request["agent_token"].read_text().strip()))
    assert token.client.actor_binding != record["agentSetup"]["actorId"]
    assert token.client.identity == owner.client.identity
    assert token.client.security_epoch == owner.client.security_epoch
    assert install_rearm.rearm(**request)["duplicate"]
    install_activation.activate(**activation)
    install_agent.setup(**fresh)
    assert selected["journal"].read_bytes() == current
    assert runtime.operations.journal.verify() == original_state


@pytest.mark.parametrize("boundary", ["review", "environment", "owner", "container", "policy", "consent"])
def test_reviewed_rearm_refuses_modified_foreign_or_unconsented_state(tmp_path, monkeypatch, boundary):
    _, _, selected, _, host, record, request, _, _, _ = fixture(tmp_path, monkeypatch)
    if boundary == "review":
        request["expected_removed_sha256"] = "0" * 64
    elif boundary == "environment":
        atomic_bytes(Path(record["activation"]["binding"]["environment"]), b"HB_WORKSPACE=/native/foreign\n")
    elif boundary == "owner":
        value = json.loads((selected["workspace"] / "config.json").read_bytes())
        value["identity"]["displayName"] = "Changed owner config"
        atomic_bytes(selected["workspace"] / "config.json", encode(value))
    elif boundary == "container":
        host["present"] = True
        host["drift"] = "replacement"
    elif boundary == "policy":
        atomic_bytes(request["policy"], request["original_policy"].read_bytes())
    else:
        request["confirm_fresh_grant"] = False
    before = selected["journal"].read_bytes()
    calls = deepcopy(host["calls"])
    with pytest.raises(ServiceError):
        install_rearm.rearm(**request)
    assert selected["journal"].read_bytes() == before and host["calls"] == calls
    assert not request["agent_token"].exists() and not request["settings"].exists()


def test_rearm_repeat_and_fresh_agent_setup_reject_changed_selection(tmp_path, monkeypatch):
    _, connection, selected, _, _, _, request, _, _, _ = fixture(tmp_path, monkeypatch)
    install_rearm.rearm(**request)
    before = selected["journal"].read_bytes()
    changed = dict(request, settings=request["settings"].with_name("foreign-settings.json"))
    with pytest.raises(ServiceError, match="original_review"):
        install_rearm.rearm(**changed)
    assert selected["journal"].read_bytes() == before
    # Selection is pinned before any fresh credential can be created; the
    # existing stage ordering also prevents setup while activation is pending.
    with pytest.raises(ServiceError):
        install_agent.setup(**connection)
    assert not request["agent_token"].exists()
