"""Codex setup artifacts and actual SDK workflow, never a simulated Codex model."""

import json
import os
import sys
import tomllib
from pathlib import Path

import pytest

from health_buddy.connect_agent import connect
from health_buddy.core.service_api import ServiceError
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
        config,
        skill,
        settings=settings,
        python=Path(sys.executable),
        source=ROOT,
        workspace=workspace,
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
    short_directory,
    tmp_path,
):
    config, skill, _ = targets(tmp_path)
    with actual_backend(short_directory, tmp_path) as (
        _,
        settings,
        runtime,
        _,
        _,
    ):
        launch = setup(config, skill, runtime.operations.config.root, settings)
        metadata = json.loads((skill / "WORKSPACE.json").read_bytes())
        assert metadata["workspace"] == str(runtime.operations.config.root)
        assert (Path(metadata["sourceRoot"]) / "docs/agent-guide.md").is_file()
        with client(settings, tmp_path, launch=launch) as wire:
            names = {
                item["name"] for item in wire.call("tools/list")["result"]["tools"]
            }
            assert {"discover_workspace", "get_context", "record_workout"} <= names
            discovered = wire.tool("discover_workspace", {})
            refs = discovered["result"]["data"]["documents"]
            assert any(item["reference"] == "docs/agent-guide.md" for item in refs)
            context = wire.tool(
                "get_context", {"scopes": ["training"], "days": 7, "limit": 20}
            )
            assert context["ok"] and context["result"]["data"]["scopes"] == ["training"]
            for scopes in (["labs"], ["training", "labs"]):
                literal = wire.tool(
                    "get_context", {"scopes": scopes, "days": 7, "limit": 20}
                )
                assert literal["ok"] and literal["result"]["data"]["scopes"] == scopes
            assert wire.tool("get_plan", {})["result"]["data"]["program"] is None
            meta = discovered["result"]["meta"]
            arguments = {
                "intentId": "synthetic-codex-workout",
                "identity": {
                    key: meta[key]
                    for key in ("installationId", "datasetId", "restoreEpoch")
                },
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
            assert fresh.tool("write_status", {"intentId": arguments["intentId"]})["ok"]
            conflict = fresh.tool(
                "record_workout",
                {**arguments, "workout": {**arguments["workout"], "notes": "changed"}},
            )
            assert not conflict["ok"]
            assert runtime.operations.journal.state().revision == revision


@pytest.mark.parametrize(
    "addition",
    [
        'EXTRA = "keep"\n',
        '[mcp_servers.health_buddy.owner_table]\nvalue = "keep"\n',
    ],
)
def test_unmarked_server_subtree_edits_refuse_update_and_removal(tmp_path, addition):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    setup(config, skill, workspace, settings)
    config.write_text(config.read_text() + addition)
    before = config.read_bytes()
    with pytest.raises(ServiceError, match="codex_integration_locally_changed"):
        setup(config, skill, workspace, settings)
    with pytest.raises(ServiceError, match="codex_integration_locally_changed"):
        connect(config, skill, remove=True)
    assert config.read_bytes() == before


def test_unrelated_later_tables_survive_update_and_removal(tmp_path):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    setup(config, skill, workspace, settings)
    later = '\n[plugins."later@directory"]\nenabled = true\n'
    config.write_text(config.read_text() + later)
    before = config.read_bytes()
    setup(config, skill, workspace, settings)
    assert config.read_bytes() == before
    connect(config, skill, remove=True)
    assert config.read_text() == UNRELATED + later


def uv_interpreter(tmp_path, version):
    """Mirror uv: venv python -> linked minor-version directory -> real file."""
    versioned = tmp_path / "uv-python" / f"cpython-{version}.99-linux-x86_64-gnu"
    (versioned / "bin").mkdir(parents=True)
    interpreter = versioned / "bin" / f"python{version}"
    interpreter.write_bytes(b"synthetic interpreter, never executed\n")
    interpreter.chmod(0o700)
    alias = versioned.parent / f"cpython-{version}-linux-x86_64-gnu"
    alias.symlink_to(versioned.name, target_is_directory=True)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(alias / "bin" / f"python{version}")
    return python


# 3.13 is the Raspberry Pi OS Python that venvs there resolve to.
@pytest.mark.parametrize("version", ["3.12", "3.13"])
def test_uv_interpreter_behind_linked_version_directory_connects(tmp_path, version):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    python = uv_interpreter(tmp_path, version)
    connect(
        config,
        skill,
        settings=settings,
        python=python,
        source=ROOT,
        workspace=workspace,
    )
    launch = tomllib.loads(config.read_text())["mcp_servers"]["health_buddy"]
    # Python finds its venv from the path it is started as, so keep the link.
    assert launch["command"] == str(python)


def test_interpreter_resolving_under_mnt_is_refused(tmp_path, monkeypatch):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    mounted = Path("/mnt") / "synthetic-drive" / "bin" / "python3.12"
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(mounted)
    is_file, access = Path.is_file, os.access
    # Report the mounted file as runnable so only the mount rule can refuse it.
    monkeypatch.setattr(Path, "is_file", lambda path: path == mounted or is_file(path))
    monkeypatch.setattr(
        os,
        "access",
        lambda path, mode, **kwargs: path == mounted or access(path, mode, **kwargs),
    )
    with pytest.raises(ServiceError, match="invalid_codex_python"):
        connect(
            config,
            skill,
            settings=settings,
            python=python,
            source=ROOT,
            workspace=workspace,
        )
    assert config.read_text() == UNRELATED and not skill.exists()


def test_interpreter_resolving_to_a_non_python_name_is_refused(tmp_path):
    config, skill, workspace = targets(tmp_path)
    settings, _ = settings_file(tmp_path)
    tool = tmp_path / "tools" / "python3.13-config"
    tool.parent.mkdir()
    tool.write_bytes(b"synthetic runnable tool, never executed\n")
    tool.chmod(0o700)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(tool)
    with pytest.raises(ServiceError, match="invalid_codex_python"):
        connect(
            config,
            skill,
            settings=settings,
            python=python,
            source=ROOT,
            workspace=workspace,
        )
    assert config.read_text() == UNRELATED and not skill.exists()
