"""Stage CLI boundaries preserve truthful results and own transient retries."""

import importlib
import json

import pytest

from health_buddy.core.service_api import ServiceError
from health_buddy.install.errors import store_retry_refusal

STAGES = [
    (
        "activation",
        "activate",
        "runtimeActivated",
        {
            "journal": "install.json",
            "environment": "runtime.env",
            "project": "health-buddy-synthetic",
            "uid": "1000",
            "gid": "1000",
        },
    ),
    (
        "owner",
        "setup",
        "ownerSetupReady",
        {
            "journal": "install.json",
            "owner-token": "owner-token",
            "origin": "https://synthetic.example.test",
            "owner-subject": "owner",
        },
    ),
    (
        "agent",
        "setup",
        "connected",
        {
            "journal": "install.json",
            "policy": "policy.json",
            "agent-token": "token",
            "settings": "settings.json",
            "retry-root": "retry",
            "client-config": "config",
            "skill-directory": "skill",
            "python": "python",
            "client": "codex",
        },
    ),
    (
        "https",
        "route",
        "connected",
        {
            "journal": "install.json",
            "tailscale": "tailscale",
            "daemon-socket": "tailscale.sock",
            "action": "setup",
        },
    ),
    (
        "rebind",
        "rebind",
        "originRebound",
        {
            "journal": "install.json",
            "owner-token": "owner-token",
            "origin": "https://synthetic.example.test",
            "owner-subject": "owner",
        },
    ),
    (
        "remove",
        "remove",
        "removed",
        {"journal": "install.json", "policy": "policy.json"},
    ),
    (
        "rearm",
        "rearm",
        "rearmed",
        {
            "journal": "install.json",
            "original-policy": "original.json",
            "policy": "policy.json",
            "agent-token": "token",
            "settings": "settings.json",
            "retry-root": "retry",
            "expected-removed-sha256": "a" * 64,
        },
    ),
    ("status", "status", "connected", {"journal": "install.json"}),
    (
        "acquire",
        "acquire",
        "installed",
        {
            "manifest-url": "https://synthetic.example.test/manifest",
            "trusted-manifest-sha256": "a" * 64,
            "bundle": "bundle",
            "staging": "staging",
        },
    ),
    (
        "prepare",
        "prepare",
        None,
        {
            "journal": "install.json",
            "bundle": "bundle",
            "manifest": "manifest.json",
            "workspace": "workspace",
            "trusted-manifest-sha256": "a" * 64,
        },
    ),
]


@pytest.mark.parametrize("stage,operation,completion,selection", STAGES)
def test_stage_cli_owns_retryable_store_refusal(
    monkeypatch, capsys, stage, operation, completion, selection
):
    module = importlib.import_module("health_buddy.install." + stage)

    def refused(**kwargs):
        raise ServiceError(503, "source_unavailable", retryable=True)

    monkeypatch.setattr(module, operation, refused)
    arguments = [part for key, value in selection.items() for part in ("--" + key, value)]
    assert module.main(arguments) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["code"] == "install_runtime_store_not_ready"
    assert value["retryable"] is True
    assert "wait ten seconds" in value["recovery"]
    assert "same command again unchanged" in value["recovery"]
    if completion is not None:
        assert value[completion] is False


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (503, "source_unavailable", False),
        (409, "source_unavailable", True),
        (503, "install_activation_runtime_not_healthy", True),
        (401, "unauthenticated", False),
    ],
)
def test_permanent_and_stage_specific_refusals_keep_their_own_recovery(
    status, code, retryable
):
    assert store_retry_refusal(ServiceError(status, code, retryable=retryable)) == {}
