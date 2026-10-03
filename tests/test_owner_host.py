"""The install stages on an owner's host, run the way the guide runs them.

tests/owner_host.py says what is real and what stands in. These tests fail by
name, never skip, when pytest runs as root or the host has no Docker socket.
"""

from __future__ import annotations

import json
import os
import pwd
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from health_buddy.runtime.manifest import verify_source_identity
from tests import owner_host
from tests.owner_host import Owner
from tests.test_install_bytecode import ENTRY_POINTS, SOURCE, runs_as_program
from tests.test_runtime_bundle_asset import blocks

# The four fields of the completion test in docs/onboarding.md.
COMPLETION = (
    "runtimeLastActive",
    "ownerAuthenticated",
    "agentGrantRetained",
    "clientConfigurationLastPrepared",
)
# What an owner runs with `"$PYTHON" -m`: the CLI and each install stage. The
# image's packaged_runtime runs only through scripts/runtime_entrypoint.py.
MODULES = [
    ".".join(path.relative_to(SOURCE).with_suffix("").parts)
    for path in ENTRY_POINTS
    if runs_as_program(path)
]


def done(owner: Owner, name: str, key: str, *arguments: str) -> dict[str, Any]:
    """Run a stage; it is done when it exits 0, prints its key true and no code."""
    code, result = owner_host.stage(owner, name, *arguments)
    assert (code, result.get(key), result.get("code")) == (0, True, None), result
    return result


def agent_arguments(owner: Owner, client: str) -> list[str]:
    """The guide's agent command for one client, Codex's or Claude Code's block."""
    config = owner.client / "config.toml"
    if client == "claude":
        (owner.client / "claude").mkdir(mode=0o700)
        config = owner.client / "claude/.mcp.json"
    return [
        *("--policy", str(owner.client / "policy.json")),
        *("--agent-token", str(owner.client / "agent-token")),
        *("--settings", str(owner.client / "adapter.json")),
        *("--retry-root", str(owner.client / "retries")),
        *("--client", client, "--client-config", str(config)),
        *("--skill-directory", str(owner.client / "skills/health-buddy")),
        *("--python", str(owner.python), "--confirm-grant", "--acknowledge-ai-egress"),
    ]


def assert_read_back(read: dict[str, Any], at: str) -> None:
    """The guide's check of the read: the one record written, at that instant."""
    (record,) = read["records"]
    fields = ("value", "unit", "kind", "sourceId", "observedAt")
    assert [record[name] for name in fields] == [150, "lb", "body-mass", "manual", at]


def measure(owner: Owner) -> None:
    """The measurement block's write and read, through the CLI's entry point."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    token = str(owner.workspace / "secrets/native-owner-token")
    cli = ["--workspace", str(owner.workspace), "--credential-file", token]
    written = owner_host.launch(
        owner,
        "health_buddy.cli",
        *(*cli, "log", "measurement", "--measured-at-local", now),
        *("--timezone", "UTC", "--weight-lb", "150"),
    )
    assert written.returncode == 0, written.stderr
    assert json.loads(written.stdout)["data"]["saved"] is True
    read = owner_host.launch(
        owner,
        "health_buddy.cli",
        *(*cli, "records", "--source-ids", "manual", "--kinds", "body-mass"),
        *("--from", now, "--to", now, "--limit", "10"),
    )
    assert read.returncode == 0, read.stderr
    assert_read_back(json.loads(read.stdout), now)


@pytest.mark.parametrize("client", ["codex", "claude"])
def test_fresh_owner_runs_every_stage_through_the_entry_points_under_umask_002(
    tmp_path: Path, client: str
) -> None:
    owner = owner_host.bootstrapped(tmp_path)
    journal = ["--journal", str(owner.journal)]
    done(owner, "preflight", "preflightPassed", *owner.selection())
    done(owner, "prepare", "workspacePrepared", *journal, *owner.selection())
    done(
        owner,
        "owner",
        "ownerSetupReady",
        *journal,
        *("--owner-token", str(owner.workspace / "secrets/native-owner-token")),
        *("--origin", "https://health-buddy.local", "--owner-subject", "owner"),
        "--confirm-owner-setup",
    )
    done(
        owner,
        "activation",
        "runtimeActivated",
        *journal,
        *("--environment", str(owner.root / "install/runtime.env")),
        *("--project", "health-buddy-personal"),
        *("--uid", str(os.getuid()), "--gid", str(os.getgid())),
        *("--confirm-local-daemon", "--confirm-quiesced"),
    )
    policy, _codex, _claude = blocks("Explicit agent grant and redacted owner status")
    owner.run(policy)
    arguments = agent_arguments(owner, client)
    agent = done(owner, "agent", "agentGrantRetained", *journal, *arguments)
    assert agent["clientConfigurationPrepared"]
    measure(owner)
    credential = (owner.workspace / "secrets/native-owner-token").read_bytes()
    agent_token = (owner.client / "agent-token").read_bytes()
    done(
        owner, "rebind", "originRebound", *journal,
        "--owner-token", str(owner.workspace / "secrets/native-owner-token"),
        "--origin", "https://synthetic.example.test", "--owner-subject", "user@github",
        "--confirm-rebind", "--confirm-local-daemon", "--confirm-quiesced",
    )
    assert (owner.workspace / "secrets/native-owner-token").read_bytes() == credential
    assert (owner.client / "agent-token").read_bytes() == agent_token
    read = owner_host.launch(
        owner, "health_buddy.cli", "--workspace", str(owner.workspace),
        "--credential-file", str(owner.client / "agent-token"),
        "records", "--source-ids", "manual", "--kinds", "body-mass",
    )
    assert read.returncode == 0, read.stderr
    assert len(json.loads(read.stdout)["records"]) == 1
    status = done(owner, "status", "ownerAuthenticated", *journal)
    assert {key: status[key] for key in COMPLETION} == dict.fromkeys(COMPLETION, True)
    # The host, not the stages' word: nothing the stages wrote is open to others,
    # and the bundle holds only the bytecode Python writes before a stage's first
    # line, so it still verifies.
    roots = [owner.workspace, owner.root / "install", owner.client]
    assert owner_host.writable_by_others(*roots, owner.root / "artifacts") == []
    stages = ("preflight", "prepare", "owner", "activation", "agent", "rebind", "status")
    modules = [f"health_buddy.install.{name}" for name in stages]
    written = owner_host.bytecode(owner)
    assert "src/health_buddy/__pycache__" in written  # Writes were enabled.
    assert written <= owner_host.interpreter_caches(*modules, "health_buddy.cli")
    verify_source_identity(owner.source, owner.bundle / "release/source-manifest.json")


@pytest.fixture(scope="module")
def prepared_owner(tmp_path_factory: pytest.TempPathFactory) -> Owner:
    return owner_host.prepared(tmp_path_factory.mktemp("prepared"))


@pytest.mark.parametrize("module", MODULES, ids=lambda name: name.rpartition(".")[2])
def test_every_stage_answers_help_and_bad_flags_before_any_file(
    prepared_owner: Owner, module: str
) -> None:
    owner = prepared_owner
    before = owner_host.files(owner)
    usage = owner_host.launch(owner, module, "--help")
    assert usage.returncode == 0 and usage.stdout.startswith("usage:"), usage.stderr
    refused = owner_host.launch(owner, module, "--no-such-flag")
    assert refused.returncode == 2 and "usage:" in refused.stderr, refused.stderr
    assert owner_host.files(owner) == before
    assert owner_host.bytecode(owner) <= owner_host.interpreter_caches(*MODULES)


def printed(output: str) -> list[dict[str, Any]]:
    """The JSON lines a block printed, in order."""
    return [json.loads(line) for line in output.splitlines() if line.startswith("{")]


def test_documented_stage_blocks_run_as_written(tmp_path: Path) -> None:
    owner = owner_host.bootstrapped(tmp_path)
    (preflight,) = blocks("Read-only preflight")
    prepare, _existing_credential = blocks("Durable local preparation")
    owner_setup, _recovery = blocks("Guided native owner setup")
    (activation,) = blocks("Explicit runtime activation")
    policy, codex, _claude = blocks("Explicit agent grant and redacted owner status")
    (measurement,) = blocks("Log and verify a measurement")
    # Owner.run fails the test with the block's output unless it exits 0.
    checked, with_port = printed(owner.run(preflight))
    assert checked["preflightPassed"] and with_port["preflightPassed"]
    assert printed(owner.run(prepare))[0]["workspacePrepared"]
    assert printed(owner.run(owner_setup))[0]["ownerSetupReady"]
    assert printed(owner.run(activation))[0]["runtimeActivated"]
    owner.run(policy)
    agent, status = printed(owner.run(codex))
    assert agent["agentGrantRetained"] and agent["clientConfigurationPrepared"]
    assert {key: status[key] for key in COMPLETION} == dict.fromkeys(COMPLETION, True)
    output = owner.run(measurement)
    receipt, read = printed(output)
    assert receipt["data"]["saved"] is True
    timestamp = output.splitlines()[0].removeprefix("Measurement UTC timestamp: ")
    assert_read_back(read, timestamp)
    # env.sh keeps Python from writing bytecode into the bundle.
    assert owner_host.bytecode(owner) == set()


def test_install_stages_run_as_root_are_refused_by_identity(tmp_path: Path) -> None:
    owner = owner_host.prepared(tmp_path)
    owner_setup, _recovery = blocks("Guided native owner setup")
    (activation,) = blocks("Explicit runtime activation")
    activation = activation.replace('"$OWNER_UID"', str(os.getuid())).replace(
        '"$OWNER_GID"', str(os.getgid())
    )
    policy, agent, _claude = blocks("Explicit agent grant and redacted owner status")
    owner.run(policy)
    expected_owner = pwd.getpwuid(os.getuid()).pw_name
    before, written = owner_host.files(owner), owner_host.bytecode(owner)
    for block, code in (
        (owner_setup, "install_owner_requires_native_nonroot_owner"),
        (activation, "install_activation_requires_nonroot_identity"),
        (agent, "install_owner_requires_native_nonroot_owner"),
    ):
        completed = owner_host.run_as_root(owner, block)
        refusal = json.loads(completed.stdout)
        assert (completed.returncode, refusal["code"]) == (2, code), refusal
        assert f"workspace owner ({expected_owner})" in refusal["recovery"]
        assert "Do not run security recovery" in refusal["recovery"]
        assert (owner_host.files(owner), owner_host.bytecode(owner)) == (
            before,
            written,
        )
