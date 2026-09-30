"""Explicit operator configuration has no backend initialization side effects."""

import json
from pathlib import Path

import pytest

from health_buddy.domain import identity_value
from health_buddy.mcp_settings import Settings, private_path
from health_buddy.retry_paths import RetryRoot
from health_buddy.service_api import ServiceError
from tests.test_extension_workflow import IDENTITY


def settings_file(tmp_path):
    token = tmp_path / "agent-token"
    token.write_text("a" * 43)
    token.chmod(0o600)
    value = {
        "schemaVersion": 1,
        "origin": "https://synthetic.example.invalid",
        "identity": identity_value(IDENTITY),
        "credentialFile": str(token),
        "retryRoot": str(tmp_path / "client-state"),
        "clientId": "synthetic-client",
        "writeSources": ["manual"],
        "acknowledgeAiEgress": True,
    }
    path = tmp_path / "adapter.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path, value


def test_explicit_state_is_not_a_local_backend(tmp_path, monkeypatch):
    from health_buddy import security_runtime, workspace

    def forbidden(*args, **kwargs):
        raise AssertionError("local backend initialization is forbidden")

    monkeypatch.setattr(workspace, "initialize", forbidden)
    monkeypatch.setattr(security_runtime, "open_runtime", forbidden)
    path, value = settings_file(tmp_path)
    settings = Settings.read(path)
    assert settings.identity == IDENTITY and settings.token() == "a" * 43
    assert not settings.retry_root.exists()
    root = settings.state()
    assert root.root == settings.retry_root and not list(root.root.iterdir())
    assert not (root.root / "config.json").exists()
    assert settings.origin == value["origin"] and settings.tls() is True


@pytest.mark.parametrize(
    "change",
    [
        {"schemaVersion": True},
        {"acknowledgeAiEgress": False},
        {"origin": "http://127.0.0.1:8791"},
        {"origin": "https://user@synthetic.example.invalid"},
        {"origin": "https://synthetic.example.invalid/private"},
        {"extra": "private"},
        {"writeSources": [True]},
        {"clientId": "../escape"},
    ],
)
def test_config_refuses_ambient_or_ambiguous_shapes(tmp_path, change):
    path, value = settings_file(tmp_path)
    path.write_text(json.dumps({**value, **change}))
    with pytest.raises(ServiceError, match="invalid_adapter_settings"):
        Settings.read(path)


def test_broad_credential_and_duplicate_config_keys_refuse(tmp_path):
    path, value = settings_file(tmp_path)
    settings = Settings.read(path)
    settings.credential_file.chmod(0o644)
    with pytest.raises(ServiceError, match="credential_unavailable"):
        settings.token()
    path.write_text(json.dumps(value)[:-1] + ',"schemaVersion":1}')
    with pytest.raises(ServiceError, match="invalid_adapter_settings"):
        Settings.read(path)


@pytest.mark.parametrize(
    "name", ["/mnt/forbidden/credential", "//mnt/forbidden/credential"]
)
def test_forbidden_native_boundary_rejected_without_filesystem_probe(monkeypatch, name):
    def forbidden(*args, **kwargs):
        raise AssertionError("forbidden target must never be probed")

    monkeypatch.setattr(Path, "lstat", forbidden)
    with pytest.raises(ServiceError):
        private_path(name)
    with pytest.raises(ServiceError):
        RetryRoot.create(Path(name))


def test_ancestor_link_rejected_before_any_target_or_leaf_probe(tmp_path, monkeypatch):
    link = tmp_path / "alias"
    link.symlink_to("/never-opened-synthetic-target", target_is_directory=True)
    original = Path.lstat
    probes = []

    def tracked(path, *args, **kwargs):
        probes.append(path)
        if path != link and link in path.parents:
            raise AssertionError("descendant was probed through forbidden ancestor")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", tracked)
    with pytest.raises(ServiceError):
        private_path(str(link / "private" / "credential"))
    with pytest.raises(ServiceError):
        RetryRoot.create(link / "private" / "state")
    assert link in probes
