"""Codex setup artifacts and actual SDK workflow, never a simulated Codex model."""

import json
import sys
import tomllib
from pathlib import Path

import pytest

from health_buddy.connect_agent import connect
from health_buddy.service_api import ServiceError
from tests import test_transport_auth_wire as uds_fixtures
from tests.mcp_wire_fixtures import actual_backend, client
from tests.test_mcp_settings import settings_file
from tests.test_portable_workspace import workout

short_directory = uds_fixtures.short_directory
ROOT = Path(__file__).resolve().parents[1]
UNRELATED = (
    '# Keep comments and unrelated clients/plugins verbatim.\nmodel = "example"\n'
    '[mcp_servers.other]\ncommand = "other"\n'
    '[plugins."example@directory"]\nenabled = false\n'
)


def targets(tmp_path):
    home = tmp_path / "codex-config"
    home.mkdir(mode=0o700)
    skills = tmp_path / "skills"
    skills.mkdir(mode=0o700)
    config = home / "config.toml"
    config.write_text(UNRELATED)
    config.chmod(0o600)
    workspace = tmp_path / "persistent-workspace"
    workspace.mkdir(mode=0o700)
    return config, skills / "health-buddy", workspace


def setup(config, skill, workspace, settings):
    connect(
        config, skill, settings=settings, python=Path(sys.executable),
        source=ROOT, workspace=workspace,
    )
    return tomllib.loads(config.read_text())["mcp_servers"]["health_buddy"]


def test_repeat_update_remove_preserves_unrelated_settings_and_owner_files(tmp_path):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    launch = setup(config, skill, workspace, settings)
    before = config.read_bytes()
    assert setup(config, skill, workspace, settings) == launch
    assert config.read_bytes() == before and config.read_text().startswith(UNRELATED)
    assert "credentialFile" not in config.read_text()
    assert not (tmp_path / "client-state").exists()
    (skill / "owner-note.md").write_text("preserve this unrelated file")
    settings2 = tmp_path / "adapter2.json"
    settings2.write_bytes(settings.read_bytes())
    settings2.chmod(0o600)
    updated = setup(config, skill, workspace, settings2)
    assert updated["args"][-1] == str(settings2)
    connect(config, skill, remove=True)
    assert config.read_text() == UNRELATED
    assert (skill / "owner-note.md").read_text() == "preserve this unrelated file"
    assert not (skill / "SKILL.md").exists() and settings2.exists()
    connect(config, skill, remove=True)
    assert config.read_text() == UNRELATED


@pytest.mark.parametrize("edited", ["skill", "config"])
def test_local_edits_require_deliberate_reconciliation(tmp_path, edited):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    setup(config, skill, workspace, settings)
    path = skill / "SKILL.md" if edited == "skill" else config
    path.write_text(path.read_text().replace("health-buddy", "owner-change", 1))
    before = path.read_bytes()
    with pytest.raises(ServiceError):
        setup(config, skill, workspace, settings)
    with pytest.raises(ServiceError):
        connect(config, skill, remove=True)
    assert path.read_bytes() == before


def test_unmanaged_server_and_insecure_config_are_preserved(tmp_path):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    config.write_text('[mcp_servers.health_buddy]\ncommand = "owner-choice"\n')
    before = config.read_bytes()
    with pytest.raises(ServiceError, match="codex_unmanaged_server_exists"):
        setup(config, skill, workspace, settings)
    assert config.read_bytes() == before and not skill.exists()
    config.chmod(0o644)
    with pytest.raises(ServiceError):
        setup(config, skill, workspace, settings)
    assert config.read_bytes() == before


def test_configured_stdio_discovers_context_and_records_workout_once(
    short_directory, tmp_path,
):
    config, skill, _ = targets(tmp_path)
    with actual_backend(short_directory, tmp_path) as (
        _, settings, runtime, _, _,
    ):
        launch = setup(config, skill, runtime.operations.config.root, settings)
        metadata = json.loads((skill / "WORKSPACE.json").read_bytes())
        assert metadata["workspace"] == str(runtime.operations.config.root)
        assert (Path(metadata["sourceRoot"]) / "docs/agent-guide.md").is_file()
        with client(settings, tmp_path, launch=launch) as wire:
            names = {item["name"] for item in wire.call("tools/list")["result"]["tools"]}
            assert {"discover_workspace", "get_context", "record_workout"} <= names
            discovered = wire.tool("discover_workspace", {})
            refs = discovered["result"]["data"]["documents"]
            assert any(item["reference"] == "docs/agent-guide.md" for item in refs)
            context = wire.tool(
                "get_context", {"scopes": ["training"], "days": 7, "limit": 20}
            )
            assert context["ok"] and context["result"]["data"]["scopes"] == ["training"]
            assert wire.tool("get_plan", {})["result"]["data"]["program"] is None
            meta = discovered["result"]["meta"]
            arguments = {
                "intentId": "synthetic-codex-workout",
                "identity": {key: meta[key] for key in (
                    "installationId", "datasetId", "restoreEpoch"
                )},
                "expectedRevision": meta["dataRevision"],
                "workout": workout(day="2026-01-03"),
            }
            receipt = wire.tool("record_workout", arguments)
            assert receipt["ok"] and receipt["result"]["data"]["saved"]
            revision = runtime.operations.journal.state().revision
            assert revision == meta["dataRevision"] + 1
        with client(settings, tmp_path, launch=launch) as fresh:
            assert fresh.tool("record_workout", arguments) == receipt
            assert runtime.operations.journal.state().revision == revision
            assert fresh.tool("write_status", {
                "intentId": arguments["intentId"]
            })["ok"]
            conflict = fresh.tool("record_workout", {
                **arguments, "workout": {**arguments["workout"], "notes": "changed"}
            })
            assert not conflict["ok"]
            assert runtime.operations.journal.state().revision == revision
