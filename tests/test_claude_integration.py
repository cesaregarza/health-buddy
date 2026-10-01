"""Claude config and real shared adapter; no named Claude/model session."""

import json
import sys
from pathlib import Path

import pytest

from health_buddy.connect_agent import connect
from health_buddy.core.service_api import ServiceError
from health_buddy.extension_registry import Registry
from health_buddy.personal_workspace import describe
from tests import test_transport_auth_wire as uds_fixtures
from tests.extension_fixtures import example
from tests.mcp_wire_fixtures import actual_backend, client
from tests.test_codex_integration import setup as codex_setup
from tests.test_codex_integration import targets as codex_targets
from tests.test_mcp_settings import settings_file
from tests.test_portable_workspace import workout

short_directory = uds_fixtures.short_directory
ROOT = Path(__file__).resolve().parents[1]
UNRELATED = {"mcpServers": {"other": {"command": "owner-choice"}}}


def targets(tmp_path):
    project = tmp_path / "claude-launcher"
    project.mkdir(mode=0o700)
    config = project / ".mcp.json"
    config.write_text(json.dumps(UNRELATED))
    config.chmod(0o600)
    skills = tmp_path / "claude-skills"
    skills.mkdir(mode=0o700)
    workspace = tmp_path / "claude-workspace"
    workspace.mkdir(mode=0o700)
    return config, skills / "health-buddy", workspace


def setup(config, skill, workspace, settings):
    connect(
        config,
        skill,
        settings=settings,
        python=Path(sys.executable),
        source=ROOT,
        workspace=workspace,
        client="claude",
    )
    return json.loads(config.read_bytes())["mcpServers"]["health_buddy"]


def test_repeat_update_remove_keeps_unrelated_config_and_workspace(tmp_path):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    launch = setup(config, skill, workspace, settings)
    before = config.read_bytes()
    assert setup(config, skill, workspace, settings) == launch
    assert config.read_bytes() == before
    assert set(launch) == {"type", "command", "args", "env"}
    assert launch["type"] == "stdio"
    owner_settings = config.parent / "owner-settings.json"
    owner_settings.write_text('{"enabledPlugins":{"owner@example":true}}')
    note = workspace / "CHANGE.md"
    note.write_text("keep persistent customization note")
    unknown = skill / "owner-note.md"
    unknown.write_text("keep unknown skill file")
    parsed = json.loads(config.read_bytes())
    parsed["mcpServers"]["later"] = {"command": "later-owner-choice"}
    config.write_text(json.dumps(parsed))
    settings2 = tmp_path / "adapter2.json"
    settings2.write_bytes(settings.read_bytes())
    settings2.chmod(0o600)
    assert setup(config, skill, workspace, settings2)["args"][-1] == str(settings2)
    connect(config, skill, client="claude", remove=True)
    assert json.loads(config.read_bytes())["mcpServers"] == {
        **UNRELATED["mcpServers"],
        "later": {"command": "later-owner-choice"},
    }
    assert owner_settings.read_text() == '{"enabledPlugins":{"owner@example":true}}'
    assert note.exists() and unknown.exists() and settings2.exists()
    connect(config, skill, client="claude", remove=True)


@pytest.mark.parametrize("change", ["unowned", "duplicate", "insecure", "edited"])
def test_ambiguous_or_unowned_config_refuses_without_mutation(tmp_path, change):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    if change == "edited":
        setup(config, skill, workspace, settings)
        parsed = json.loads(config.read_bytes())
        parsed["mcpServers"]["health_buddy"]["ownerExtra"] = "keep"
        config.write_text(json.dumps(parsed))
    elif change == "unowned":
        config.write_text('{"mcpServers":{"health_buddy":{"command":"owner"}}}')
    elif change == "duplicate":
        config.write_text('{"mcpServers":{},"mcpServers":{"other":{}}}')
    else:
        config.chmod(0o644)
    before = config.read_bytes()
    with pytest.raises(ServiceError):
        setup(config, skill, workspace, settings)
    with pytest.raises(ServiceError):
        connect(config, skill, client="claude", remove=True)
    assert config.read_bytes() == before


def test_claude_configured_sdk_reads_codex_record_replays_and_diagnoses(
    short_directory, tmp_path
):
    config, skill, _ = targets(tmp_path)
    codex_config, codex_skill, _ = codex_targets(tmp_path)
    with actual_backend(short_directory, tmp_path) as (
        bridge,
        settings,
        runtime,
        _,
        _,
    ):
        workspace = runtime.operations.config.root
        codex_launch = codex_setup(codex_config, codex_skill, workspace, settings)
        with client(settings, tmp_path, launch=codex_launch) as first:
            discovered = first.tool("discover_workspace", {})
            meta = discovered["result"]["meta"]
            arguments = {
                "intentId": "synthetic-cross-config-workout",
                "identity": {
                    key: meta[key]
                    for key in ("installationId", "datasetId", "restoreEpoch")
                },
                "expectedRevision": meta["dataRevision"],
                "workout": workout(day="2026-01-03"),
            }
            receipt = first.tool("record_workout", arguments)
            assert receipt["ok"] and receipt["result"]["data"]["saved"]
        name = "local.weekly-mass"
        extension = example(runtime.operations.config, name)
        note = extension / "notes/DESIGN.md"
        note.write_text(note.read_text() + "\nSynthetic shared maintenance note.\n")
        selected = Registry(runtime.operations.config).enable(
            name, source_ids=("manual",)
        )
        claude_settings = tmp_path / "claude-adapter.json"
        selected_settings = json.loads(settings.read_bytes())
        selected_settings["clientId"] = "synthetic-claude-config"
        selected_settings["retryRoot"] = str(tmp_path / "claude-retry")
        claude_settings.write_text(json.dumps(selected_settings))
        claude_settings.chmod(0o600)
        launch = setup(config, skill, workspace, claude_settings)
        shared_skill = (codex_skill / "SKILL.md").read_bytes()
        assert (skill / "SKILL.md").read_bytes() == shared_skill
        metadata = json.loads((skill / "WORKSPACE.json").read_bytes())
        assert metadata["workspace"] == str(workspace)
        revision = runtime.operations.journal.state().revision
        # Claude launches stdio from its project; cwd is not a .mcp.json field.
        with client(
            claude_settings, tmp_path, launch={**launch, "cwd": config.parent}
        ) as second:
            current = second.tool("discover_workspace", {})
            assert current["ok"]
            refs = current["result"]["data"]["documents"]
            assert any(item["reference"] == "docs/agent-guide.md" for item in refs)
            admitted = current["result"]["data"]["extensions"]
            assert admitted[0]["reviewedDigest"] == selected.reviewed_digest
            assert admitted[0]["designNotesRef"] == f"extension:{name}:design-notes"
            context = second.tool("get_context", {"scopes": ["training"], "days": 7})
            assert context["ok"]
            assert context["result"]["data"]["scopes"] == ["training"]
            records = second.tool(
                "list_records",
                {
                    "sourceIds": ["manual"],
                    "kinds": ["workout-set"],
                    "from": "2026-01-02T00:00:00Z",
                    "to": "2026-01-04T23:59:59Z",
                    "limit": 20,
                },
            )
            assert records["ok"]
            rows = records["result"]["data"]["records"]
            assert len(rows) == 1 and rows[0]["sourceId"] == "manual"
            values = rows[0]["value"]
            assert values["exercise"]["value"] == "example_press"
            assert values["load_lb"] == {
                "value": 20,
                "unit": "lb",
                "missingness": None,
            }
            bridge.redirect_next = True
            failed = second.tool("sync_status", {})
            assert not failed["ok"] and failed["error"]["code"] == "redirect_refused"
            assert second.tool("sync_status", {})["ok"]
            current_meta = current["result"]["meta"]
            next_workout = workout(day="2026-01-04")
            next_workout["session_id"] = (
                "dashboard-00000000-0000-4000-8000-000000000002"
            )
            next_arguments = {
                **arguments,
                "intentId": "synthetic-claude-workout",
                "expectedRevision": current_meta["dataRevision"],
                "workout": next_workout,
            }
            saved = second.tool("record_workout", next_arguments)
            assert saved["ok"], saved.get("error")
            assert saved["result"]["data"]["saved"]
            assert second.tool("record_workout", next_arguments) == saved
            assert second.tool(
                "write_status", {"intentId": next_arguments["intentId"]}
            )["ok"]
        assert runtime.operations.journal.state().revision == revision + 1
        inventory = describe(runtime.operations.config)["personalInventory"]["entries"]
        assert any(item["path"].endswith("notes/DESIGN.md") for item in inventory)
        assert "Synthetic shared maintenance note." in note.read_text()
