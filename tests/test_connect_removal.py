"""Real client ownership files interrupted inside removal; no named client."""

import json
from pathlib import Path

import pytest

from health_buddy import connect_agent
from health_buddy.service_api import ServiceError
from tests import test_claude_integration as claude
from tests import test_codex_integration as codex
from tests.test_mcp_settings import settings_file


@pytest.mark.parametrize("client", ["codex", "claude"])
@pytest.mark.parametrize(
    "point", ["config", "SKILL.md", "WORKSPACE.json", ".health-buddy-install.json"]
)
def test_internal_removal_interruptions_resume_exact_owned_files(
    tmp_path, monkeypatch, client, point
):
    fixture = codex if client == "codex" else claude
    config, skill, workspace = fixture.targets(tmp_path)
    original_config = config.read_bytes()
    settings, _unused = settings_file(tmp_path)
    fixture.setup(config, skill, workspace, settings)
    note = skill / "owner-note.md"
    note.write_text("Synthetic retained owner file")
    original_atomic = connect_agent.atomic_bytes
    original_unlink = Path.unlink

    def publish(path, payload):
        original_atomic(path, payload)
        if point == "config" and path == config:
            raise OSError("synthetic interruption after config replacement")

    def unlink(path, *args, **kwargs):
        original_unlink(path, *args, **kwargs)
        if point == path.name and path.parent == skill:
            raise OSError("synthetic interruption after owned unlink")

    monkeypatch.setattr(connect_agent, "atomic_bytes", publish)
    monkeypatch.setattr(Path, "unlink", unlink)
    with pytest.raises(OSError):
        connect_agent.connect(config, skill, client=client, remove=True)
    intent = (skill / ".health-buddy-remove.json").read_bytes()
    assert b"Synthetic retained owner file" not in intent
    assert b"credentialFile" not in intent
    # Read-only installer admission must accept that exact interrupted state.
    connect_agent.connect(config, skill, client=client, remove=True, check_only=True)
    assert (skill / ".health-buddy-remove.json").read_bytes() == intent
    monkeypatch.setattr(connect_agent, "atomic_bytes", original_atomic)
    monkeypatch.setattr(Path, "unlink", original_unlink)
    connect_agent.connect(config, skill, client=client, remove=True)
    connect_agent.connect(config, skill, client=client, remove=True)
    if client == "codex":
        assert config.read_bytes() == original_config
    else:
        assert json.loads(config.read_bytes()) == json.loads(original_config)
    assert note.read_text() == "Synthetic retained owner file"
    assert workspace.exists() and settings.exists()
    assert not any(
        (skill / name).exists()
        for name in (
            *connect_agent.MANAGED,
            ".health-buddy-install.json",
            ".health-buddy-remove.json",
        )
    )


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_present_owned_edit_during_removal_intent_refuses_without_overwrite(
    tmp_path, monkeypatch, client
):
    fixture = codex if client == "codex" else claude
    config, skill, workspace = fixture.targets(tmp_path)
    settings, _unused = settings_file(tmp_path)
    fixture.setup(config, skill, workspace, settings)
    original = connect_agent.atomic_bytes

    def publish(path, payload):
        original(path, payload)
        if path == config:
            raise OSError("synthetic lost config acknowledgement")

    monkeypatch.setattr(connect_agent, "atomic_bytes", publish)
    with pytest.raises(OSError):
        connect_agent.connect(config, skill, client=client, remove=True)
    monkeypatch.setattr(connect_agent, "atomic_bytes", original)
    path = skill / "WORKSPACE.json"
    path.write_text("Synthetic foreign edited file")
    before = {
        target: target.read_bytes()
        for target in (config, path, skill / ".health-buddy-remove.json")
    }
    for check_only in (True, False):
        with pytest.raises(ServiceError, match="integration_locally_changed"):
            connect_agent.connect(
                config, skill, client=client, remove=True, check_only=check_only
            )
    assert all(target.read_bytes() == value for target, value in before.items())
