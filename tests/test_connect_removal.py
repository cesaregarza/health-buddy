"""Real client ownership files interrupted inside removal; no named client."""

import json
from pathlib import Path

import pytest

from health_buddy import connect_agent
from health_buddy.core.service_api import ServiceError
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


@pytest.mark.parametrize("client", ["codex", "claude"])
@pytest.mark.parametrize("content", ["empty", "owner-note", "empty-manifest"])
def test_unowned_skill_directory_refuses_without_writes(
    tmp_path, capsys, client, content
):
    fixture = codex if client == "codex" else claude
    config, skill, workspace = fixture.targets(tmp_path)
    settings, _unused = settings_file(tmp_path)
    skill.mkdir(mode=0o700)
    if content != "empty":
        name = (
            ".health-buddy-install.json"
            if content == "empty-manifest"
            else "owner-note.md"
        )
        (skill / name).write_text("" if content == "empty-manifest" else "Keep me")
        (skill / name).chmod(0o600)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ServiceError, match="agent_skill_directory_unowned"):
        fixture.setup(config, skill, workspace, settings)
    assert connect_agent.main(
        [client, "--config-file", str(config), "--skill-directory", str(skill)]
    ) == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["code"] == "agent_skill_directory_unowned"
    assert "owner inspection" in refusal["recovery"]
    assert "removal/reconnection" in refusal["recovery"]
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_owned_legacy_skill_stays_at_recorded_path_until_explicit_removal(
    tmp_path, client
):
    fixture = codex if client == "codex" else claude
    config, discovered, workspace = fixture.targets(tmp_path)
    settings, _unused = settings_file(tmp_path)
    legacy_parent = tmp_path / "private-client"
    legacy_parent.mkdir(mode=0o700)
    (legacy_parent / "skills").mkdir(mode=0o700)
    legacy = legacy_parent / "skills/health-buddy"
    original_config = config.read_bytes()
    fixture.setup(config, legacy, workspace, settings)
    (legacy / "owner-note.md").write_text("Retain this legacy note")
    before = {
        path: path.read_bytes()
        for path in (config, legacy / ".health-buddy-install.json", legacy / "SKILL.md")
    }
    fixture.setup(config, legacy, workspace, settings)
    with pytest.raises(ServiceError):
        fixture.setup(config, discovered, workspace, settings)
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not discovered.exists()
    connect_agent.connect(config, legacy, client=client, remove=True)
    assert not (legacy / ".health-buddy-install.json").exists()
    fixture.setup(config, discovered, workspace, settings)
    assert (discovered / "SKILL.md").is_file()
    assert (discovered / ".health-buddy-install.json").is_file()
    assert (legacy / "owner-note.md").read_text() == "Retain this legacy note"
    connect_agent.connect(config, discovered, client=client, remove=True)
    if client == "codex":
        assert config.read_bytes() == original_config
    else:
        assert json.loads(config.read_bytes()) == json.loads(original_config)
