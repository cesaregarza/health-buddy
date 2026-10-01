"""Real workspace/guide/client preparation with synthetic checkpoints, no daemon."""

import json
import sys
from pathlib import Path

import pytest

from health_buddy.install import prepare as install_prepare
from health_buddy.core.domain import identity_value
from health_buddy.security.runtime import open_runtime
from health_buddy.core.service_api import ServiceError
from tests.test_install_preflight import prepared


def inputs(tmp_path, monkeypatch):
    selected = prepared(tmp_path, monkeypatch, maintenance=True)
    private = tmp_path / "install-state"
    private.mkdir(mode=0o700)
    return {**selected, "journal": private / "install.json"}


def test_interrupted_initializer_reopens_same_workspace_without_duplicate_data(
    tmp_path, monkeypatch
):
    selected = inputs(tmp_path, monkeypatch)
    original = install_prepare.initialize

    def interrupt(root):
        original(root)
        raise OSError("synthetic lost local checkpoint")

    monkeypatch.setattr(install_prepare, "initialize", interrupt)
    with pytest.raises(OSError):
        install_prepare.prepare(**selected)
    journal = json.loads(selected["journal"].read_bytes())
    assert journal["phase"] == "initializing"
    note = selected["workspace"] / "personal/OWNER.md"
    note.write_text("synthetic personal note added during interruption")
    config_before = (selected["workspace"] / "config.json").read_bytes()
    monkeypatch.setattr(install_prepare, "initialize", original)
    result = install_prepare.prepare(**selected)
    assert result["phase"] == "prepared"
    assert not result["installed"] and not result["connected"]
    runtime = open_runtime(selected["workspace"])
    identity = runtime.operations.journal.state().identity
    revision = runtime.operations.journal.state().revision
    assert revision == 0
    assert install_prepare.prepare(**selected) == result
    repeated = open_runtime(selected["workspace"])
    assert repeated.operations.journal.state().identity == identity
    assert repeated.operations.journal.state().revision == revision
    assert (selected["workspace"] / "config.json").read_bytes() == config_before
    assert note.read_text() == "synthetic personal note added during interruption"
    profile = json.loads(
        (selected["workspace"] / "personal/INSTALLATION.json").read_bytes()
    )
    assert profile["sourceRoot"] == str(selected["bundle"] / "source")
    assert profile["sourceCommit"] == result["sourceCommit"]
    for key in (
        "guide",
        "developmentLock",
        "extensionCatalog",
        "tests",
        "previewAndReview",
    ):
        assert (Path(profile["sourceRoot"]) / profile[key]).exists()


def test_profile_publication_interruption_resumes_without_overwriting_owner_files(
    tmp_path, monkeypatch
):
    selected = inputs(tmp_path, monkeypatch)
    original = install_prepare.atomic_bytes

    def interrupt(path, payload):
        original(path, payload)
        if path.name == "INSTALLATION.json":
            raise OSError("synthetic lost profile publication acknowledgement")

    monkeypatch.setattr(install_prepare, "atomic_bytes", interrupt)
    with pytest.raises(OSError):
        install_prepare.prepare(**selected)
    assert json.loads(selected["journal"].read_bytes())["phase"] == "source_preparing"
    monkeypatch.setattr(install_prepare, "atomic_bytes", original)
    assert install_prepare.prepare(**selected)["phase"] == "prepared"
    profile = selected["workspace"] / "personal/INSTALLATION.json"
    profile.write_text('{"synthetic_owner_edit":"keep"}')
    before = profile.read_bytes()
    with pytest.raises(ServiceError, match="source_profile_locally_changed"):
        install_prepare.prepare(**selected)
    assert profile.read_bytes() == before


def test_changed_binding_and_unowned_existing_workspace_refuse(tmp_path, monkeypatch):
    selected = inputs(tmp_path, monkeypatch)
    sentinel = selected["workspace"] / "owner-note"
    sentinel.write_text("keep unrelated existing installation")
    with pytest.raises(ServiceError, match="install_preflight_refused"):
        install_prepare.prepare(**selected)
    assert not selected["journal"].exists() and sentinel.exists()
    sentinel.unlink()
    install_prepare.prepare(**selected)
    before = selected["journal"].read_bytes()
    with pytest.raises(ServiceError, match="original_binding"):
        install_prepare.prepare(**{**selected, "trusted_manifest_sha256": "0" * 64})
    assert selected["journal"].read_bytes() == before


def command_line(selected):
    return [f"--{key.replace('_', '-')}={value}" for key, value in selected.items()]


def test_changed_bundle_source_is_named_before_the_first_write(
    tmp_path, monkeypatch, capsys
):
    selected = inputs(tmp_path, monkeypatch)
    (selected["bundle"] / "source/AGENTS.md").write_text("Synthetic later edit.\n")
    with pytest.raises(
        ServiceError, match="install_preparation_source_identity_mismatch"
    ):
        install_prepare.prepare(**selected)
    assert not selected["journal"].exists()
    assert not any(selected["workspace"].iterdir())
    assert install_prepare.main(command_line(selected)) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "install_preparation_source_identity_mismatch"
    assert output["recovery"].startswith("Re-extract the source bundle")


def test_other_refusals_keep_the_generic_preparation_code(
    tmp_path, monkeypatch, capsys
):
    selected = inputs(tmp_path, monkeypatch)
    (selected["workspace"] / "owner-note").write_text("keep unrelated installation")
    assert install_prepare.main(command_line(selected)) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "install_preparation_refused"


@pytest.mark.parametrize("name", ["codex", "claude"])
def test_client_config_repeats_without_live_connection(tmp_path, monkeypatch, name):
    selected = inputs(tmp_path, monkeypatch)
    install_prepare.prepare(**selected)
    root = selected["workspace"]
    config = root / "config.json"
    values = json.loads(config.read_bytes())
    values["security"]["externalOrigin"] = "https://synthetic.example.test"
    config.write_text(json.dumps(values))
    config.chmod(0o600)
    runtime = open_runtime(root)
    token = root / "secrets/synthetic-agent-token"
    token.write_text("q" * 43)
    token.chmod(0o600)
    settings = selected["journal"].parent / "adapter.json"
    settings.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "origin": "https://synthetic.example.test",
                "identity": identity_value(runtime.operations.journal.state().identity),
                "credentialFile": str(token),
                "retryRoot": str(selected["journal"].parent / "retry"),
                "clientId": "synthetic-installer",
                "writeSources": ["manual"],
                "acknowledgeAiEgress": True,
            }
        )
    )
    settings.chmod(0o600)
    client_root = tmp_path / "synthetic-client"
    client_root.mkdir(mode=0o700)
    skills = tmp_path / "synthetic-skills"
    skills.mkdir(mode=0o700)
    client_config = client_root / ("config.toml" if name == "codex" else ".mcp.json")
    unrelated = (
        '# owner comment\nmodel = "owner-choice"\n'
        if name == "codex"
        else '{"mcpServers":{"owner":{"command":"owner-choice"}}}'
    )
    client_config.write_text(unrelated)
    client_config.chmod(0o600)
    selected.update(
        client=name,
        client_config=client_config,
        skill_directory=skills / "health-buddy",
        settings=settings,
        python=Path(sys.executable),
    )
    result = install_prepare.prepare(**selected)
    assert result["clientConfigurationPrepared"] and not result["connected"]
    assert not result["installed"]
    before = client_config.read_bytes()
    assert install_prepare.prepare(**selected) == result
    assert (
        client_config.read_bytes() == before
        and "owner-choice" in client_config.read_text()
    )
    assert token.read_text() == "q" * 43
    assert not (selected["journal"].parent / "retry").exists()
    mismatch = json.loads(settings.read_bytes())
    mismatch["identity"]["restoreEpoch"] = "00000000-0000-4000-8000-000000000099"
    settings.write_text(json.dumps(mismatch))
    with pytest.raises(ServiceError, match="matching_owner_authority"):
        install_prepare.prepare(**selected)
    assert client_config.read_bytes() == before
