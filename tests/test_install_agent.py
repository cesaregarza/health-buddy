"""Real authority/client files with bounded fake host responses; no model client."""

import json
import sys
from pathlib import Path

import pytest

from health_buddy import install_agent, install_https, install_status
from health_buddy.security_api import PairingReservation, SecurityRequest
from health_buddy.service_api import ServiceError
from tests.test_install_https import serve_fixture


def connection_fixture(tmp_path, monkeypatch):
    https, _state, selected, identity, note, _engine = serve_fixture(
        tmp_path, monkeypatch
    )
    install_https.route(**https)
    private = tmp_path / "private-client"
    private.mkdir(mode=0o700)
    skills = private / "skills"
    skills.mkdir(mode=0o700)
    config = private / "config.toml"
    config.write_text('model = "synthetic-kept"\n# owner comment\n')
    config.chmod(0o600)
    policy = private / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "name": "Synthetic Health Buddy agent",
                "grants": ["records:read", "records:write"],
                "sourceIds": ["manual"],
                "readSources": ["manual"],
                "readKinds": ["workout", "weight", "hydration"],
                "readFields": None,
            }
        )
    )
    policy.chmod(0o600)
    arguments = dict(
        journal=selected["journal"],
        policy=policy,
        agent_token=private / "agent-token",
        settings=private / "adapter.json",
        retry_root=private / "retries",
        client="codex",
        client_config=config,
        skill_directory=skills / "health-buddy",
        python=Path(sys.executable),
        confirm_grant=True,
        acknowledge_ai_egress=True,
    )
    return arguments, selected, identity, note


@pytest.mark.parametrize("lost", [None, "credential_written"])
def test_default_install_connection_repeats_with_same_authority_and_redacted_status(
    tmp_path, monkeypatch, lost
):
    arguments, selected, identity, note = connection_fixture(tmp_path, monkeypatch)
    original_note = note.read_bytes()
    if lost:

        def interrupt(point):
            if point == lost:
                raise OSError("synthetic lost private handoff acknowledgement")

        with pytest.raises(OSError):
            install_agent.setup(**arguments, fault=interrupt)
    result = install_agent.setup(**arguments)
    token = arguments["agent_token"].read_bytes()
    retained = selected["journal"].read_bytes()
    assert result["clientConfigurationPrepared"] and not result["connected"]
    assert install_agent.setup(**arguments) == result
    assert arguments["agent_token"].read_bytes() == token
    assert selected["journal"].read_bytes() == retained
    assert token.strip() not in retained
    assert (
        arguments["client_config"]
        .read_text()
        .startswith('model = "synthetic-kept"\n# owner comment\n')
    )
    assert not arguments["retry_root"].exists()
    runtime, admitted = install_agent.owner(json.loads(retained))
    assert len(install_agent.actors(runtime, admitted)) == 1
    assert runtime.operations.journal.state().revision == 0
    assert note.read_bytes() == original_note
    pairing = runtime.security.execute(
        admitted.principal,
        SecurityRequest(
            "pairing.create",
            payload=PairingReservation("Synthetic private phone"),
            identity=admitted.client.identity,
        ),
    ).data["id"]
    summary = install_status.status(journal=selected["journal"], pairing_id=pairing)
    assert (
        summary["agentGrantRetained"] and summary["pairingStatus"] == "awaiting_owner"
    )
    assert summary["activeDeviceCount"] == 0 and not summary["connected"]
    redacted = json.dumps(summary).encode()
    for private_value in (
        token.strip(),
        pairing.encode(),
        str(selected["workspace"]).encode(),
        b"Synthetic private phone",
    ):
        assert private_value not in redacted
    assert json.loads(retained)["agentSetup"]["binding"]["identity"] == identity


@pytest.mark.parametrize("edited", ["settings", "client_config"])
def test_connection_refuses_owned_local_edits_without_overwriting(
    tmp_path, monkeypatch, edited
):
    arguments, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    install_agent.setup(**arguments)
    path = arguments[edited]
    original = path.read_bytes()
    changed = (
        original.replace(b'"health-buddy-installer"', b'"owner-edited"')
        if edited == "settings"
        else original.replace(b"startup_timeout_sec = 15", b"startup_timeout_sec = 16")
    )
    assert changed != original
    path.write_bytes(changed)
    with pytest.raises(
        ServiceError, match="settings_locally_changed|integration_locally_changed"
    ):
        install_agent.setup(**arguments)
    assert path.read_bytes() == changed
    runtime, admitted = install_agent.owner(
        json.loads(selected["journal"].read_bytes())
    )
    assert len(install_agent.actors(runtime, admitted)) == 1


def test_lost_one_time_secret_refuses_duplicate_grant(tmp_path, monkeypatch):
    arguments, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)

    def interrupt(point):
        if point == "grant_committed":
            raise OSError("synthetic lost one-time grant response")

    with pytest.raises(OSError):
        install_agent.setup(**arguments, fault=interrupt)
    for _attempt in range(2):
        with pytest.raises(
            ServiceError, match="private_handoff_requires_owner_rotation"
        ):
            install_agent.setup(**arguments)
    assert not arguments["agent_token"].exists()
    runtime, admitted = install_agent.owner(
        json.loads(selected["journal"].read_bytes())
    )
    assert len(install_agent.actors(runtime, admitted)) == 1


def test_settings_create_race_refuses_unowned_valid_profile(tmp_path, monkeypatch):
    arguments, _selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    original = install_agent.create_file

    def create(path, value):
        if path == arguments["settings"]:
            original(path, value)
            return False
        return original(path, value)

    monkeypatch.setattr(install_agent, "create_file", create)
    with pytest.raises(ServiceError, match="settings_locally_changed"):
        install_agent.setup(**arguments)
    assert not arguments["skill_directory"].exists()
