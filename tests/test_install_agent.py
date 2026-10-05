"""Real authority/client files with bounded fake host responses; no model client."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from health_buddy import connect_agent
from health_buddy.connect_agent import check_mcp_readiness
from health_buddy.core.security_api import PairingReservation, SecurityRequest
from health_buddy.core.service_api import ServiceError
from health_buddy.install import activation as install_activation
from health_buddy.install import agent as install_agent
from health_buddy.install import https as install_https
from health_buddy.install import prepare as install_prepare
from health_buddy.install import status as install_status
from tests.test_install_activation import fixture as activation_fixture
from tests.test_install_https import serve_fixture
from tests.test_install_prepare import inputs as preparation_inputs


def connection_fixture(tmp_path, monkeypatch, *, private_https=True, local_only=False):
    if private_https:
        https, _state, selected, identity, note, _engine = serve_fixture(
            tmp_path, monkeypatch
        )
        install_https.route(**https)
    else:
        activation, _engine, selected, identity, note = activation_fixture(
            tmp_path, monkeypatch, guided_owner=True, local_only=local_only
        )
        install_activation.activate(**activation)
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
                "readKinds": [
                    "body-mass",
                    "water-intake",
                    "workout-session",
                    "workout-set",
                    "cardio-segment",
                ],
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


@pytest.mark.parametrize(
    ("failure", "mode", "expected"),
    [
        pytest.param("mode", 0o640, "mode_0600", id="mode-0640"),
        pytest.param("mode", 0o664, "mode_0600", id="mode-0664"),
        pytest.param("symlink", None, "regular_non_symlink_file"),
        pytest.param("large", None, "at_most_16384_bytes"),
        pytest.param("malformed", None, "json_object"),
        pytest.param("nonobject", None, "json_object"),
    ],
)
def test_policy_refusals_are_specific_and_precede_grant_or_journal_writes(
    tmp_path, monkeypatch, capsys, failure, mode, expected
):
    arguments, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    policy = arguments["policy"]
    if failure == "mode":
        policy.chmod(mode)
    elif failure == "symlink":
        target = policy.with_name("policy-target.json")
        target.write_bytes(policy.read_bytes())
        target.chmod(0o600)
        policy.unlink()
        policy.symlink_to(target.name)
    elif failure == "large":
        policy.write_bytes(b" " * 16385)
        policy.chmod(0o600)
    elif failure == "malformed":
        policy.write_text("[")
        policy.chmod(0o600)
    else:
        policy.write_text("[]")
        policy.chmod(0o600)
    journal_before = selected["journal"].read_bytes()
    argv = [
        "--journal",
        str(arguments["journal"]),
        "--policy",
        str(policy),
        "--agent-token",
        str(arguments["agent_token"]),
        "--settings",
        str(arguments["settings"]),
        "--retry-root",
        str(arguments["retry_root"]),
        "--client",
        arguments["client"],
        "--client-config",
        str(arguments["client_config"]),
        "--skill-directory",
        str(arguments["skill_directory"]),
        "--python",
        str(arguments["python"]),
        "--confirm-grant",
        "--acknowledge-ai-egress",
    ]
    assert install_agent.main(argv) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["code"] == "install_agent_invalid_policy"
    assert result["policyPath"] == str(policy)
    assert result["failedRequirement"] == expected
    if failure == "mode":
        assert f"chmod 600 -- {policy}" in result["recovery"]
    assert selected["journal"].read_bytes() == journal_before
    assert not arguments["agent_token"].exists()
    assert not arguments["settings"].exists()
    runtime, admitted = install_agent.owner(json.loads(journal_before))
    assert install_agent.actors(runtime, admitted) == []


def _readiness_calls(monkeypatch):
    calls = []
    # prepared(maintenance=True) memoizes readiness in both modules. Restore
    # the real check locally so these count tests always launch the subprocess.
    monkeypatch.setattr(install_agent, "check_mcp_readiness", check_mcp_readiness)
    monkeypatch.setattr(connect_agent, "check_mcp_readiness", check_mcp_readiness)
    original = connect_agent.subprocess.run

    def observed(command, *args, **kwargs):
        if command[-1:] == ["--check-dependencies"]:
            calls.append(command)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(connect_agent.subprocess, "run", observed)
    return calls


def test_agent_setup_and_direct_connect_each_probe_once(tmp_path, monkeypatch):
    arguments, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    calls = _readiness_calls(monkeypatch)
    result = install_agent.setup(**arguments)
    assert result["clientConfigurationPrepared"] and len(calls) == 1
    calls.clear()
    connect_agent.connect(
        arguments["client_config"],
        arguments["skill_directory"],
        settings=arguments["settings"],
        python=arguments["python"],
        source=selected["bundle"] / "source",
        workspace=selected["workspace"],
        client=arguments["client"],
    )
    assert len(calls) == 1


def test_readiness_refusal_probes_once_before_any_install_mutation(
    tmp_path, monkeypatch
):
    arguments, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    # An admitted basename reaches the actual failing subprocess probe.
    rejected = tmp_path / "python3.12"
    rejected.write_text("#!/bin/sh\nexit 23\n")
    rejected.chmod(0o700)
    arguments["python"] = rejected
    before = selected["journal"].read_bytes()
    calls = _readiness_calls(monkeypatch)
    with pytest.raises(connect_agent.McpReadinessError):
        install_agent.setup(**arguments)
    assert len(calls) == 1
    assert selected["journal"].read_bytes() == before
    assert not arguments["agent_token"].exists()
    assert not arguments["settings"].exists()


def test_status_report_tracks_agent_stage_without_claiming_acceptance(
    tmp_path, monkeypatch, capsys
):
    arguments, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    argv = ["--journal", str(selected["journal"]), "--report"]

    assert install_status.main(argv) == 0
    incomplete = capsys.readouterr().out
    assert incomplete.splitlines()[0] == (
        "LOCAL SETUP: incomplete (next required stage: agent_configuration; "
        "command: health_buddy.install.agent)"
    )
    assert len(incomplete.splitlines()) == 4

    assert install_status.main(argv) == 0
    assert capsys.readouterr().out == incomplete

    install_agent.setup(**arguments)
    assert install_status.main(argv) == 0
    complete = capsys.readouterr().out
    lines = complete.splitlines()
    assert lines[0] == "LOCAL SETUP: complete"
    assert lines[1] == (
        "OWNER ACCEPTANCE PENDING: fresh_named_client_acceptance, "
        "authenticated_record_readback"
    )
    assert len(lines) == 4
    summary = install_status.status(journal=selected["journal"])
    assert summary["phoneInstruction"].startswith("If you want phone data:")
    assert "before owner setup" in summary["phoneInstruction"]
    assert not summary["healthkitReceiverEnabled"]
    assert summary["healthkitMode"] == "read-only"
    assert lines[2] == "OPTIONAL: actual_private_https_acceptance, phone_acceptance"
    canonical = json.dumps(
        summary, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()[:12]
    assert lines[3] == f"REPORT DIGEST: {digest}"
    changed_guidance = {**summary, "phoneInstruction": "Different optional phone guidance"}
    changed_report = install_status.format_report(changed_guidance).splitlines()
    assert changed_report[:3] == lines[:3]
    assert changed_report[3] != lines[3]
    assert lines[3] != incomplete.splitlines()[3]
    assert str(selected["workspace"]) not in complete
    assert str(arguments["agent_token"]) not in complete


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
    assert not summary["phoneReceiverConfigured"]
    assert not summary["healthkitReceiverEnabled"]
    assert summary["healthkitMode"] == "read-only"
    assert summary["phoneInstruction"].startswith("If you want phone data:")
    assert "before owner setup" in summary["phoneInstruction"]
    assert "Deliberately approve pairing" in summary["phoneInstruction"]
    stages = {item["name"]: item["state"] for item in summary["localStages"]}
    assert stages["owner_setup"] == "currently_authenticated"
    assert stages["runtime_activation"] == "last_active"
    assert stages["agent_configuration"] == "last_prepared"
    assert summary["nextRequiredStage"] == "authenticated_record_readback"
    assert summary["pendingAcceptance"] == [
        "fresh_named_client_acceptance",
        "authenticated_record_readback",
    ]
    assert summary["optionalPendingAcceptance"] == [
        "actual_private_https_acceptance",
        "phone_acceptance",
    ]
    assert "pending" not in summary
    redacted = json.dumps(summary).encode()
    for private_value in (
        token.strip(),
        pairing.encode(),
        str(selected["workspace"]).encode(),
        b"Synthetic private phone",
    ):
        assert private_value not in redacted
    assert json.loads(retained)["agentSetup"]["binding"]["identity"] == identity
    runtime, admitted = install_agent.owner(json.loads(retained))
    actor_id = json.loads(retained)["agentSetup"]["actorId"]
    runtime.security.execute(
        admitted.principal,
        SecurityRequest(
            "grants.revoke", resource_id=actor_id, identity=admitted.client.identity
        ),
    )
    revoked = install_status.status(journal=selected["journal"])
    assert revoked["clientConfigurationLastPrepared"]
    assert not revoked["agentGrantRetained"]
    assert revoked["nextRequiredStage"] == "agent_grant_reconciliation"
    assert "owner grant review" in revoked["nextRequiredCommand"]


def test_agent_setup_needs_only_the_active_runtime(tmp_path, monkeypatch):
    arguments, selected, identity, _note = connection_fixture(
        tmp_path, monkeypatch, private_https=False
    )
    record = json.loads(selected["journal"].read_bytes())
    assert record["activation"]["phase"] == "active" and "privateHttps" not in record
    summary = install_status.status(journal=selected["journal"])
    assert summary["nextRequiredStage"] == "agent_configuration"
    assert summary["nextRequiredCommand"] == "health_buddy.install.agent"
    assert not summary["privateHttpsLastConfigured"]
    result = install_agent.setup(**arguments)
    assert result["clientConfigurationPrepared"] and not result["connected"]
    socket = str(selected["workspace"] / "security/runtime/http.sock")
    settings = json.loads(arguments["settings"].read_bytes())
    assert settings["socketPath"] == socket and settings["identity"] == identity
    assert settings["origin"] == "https://synthetic.example.test"
    record = json.loads(selected["journal"].read_bytes())
    assert record["agentSetup"]["binding"]["socketPath"] == socket
    summary = install_status.status(journal=selected["journal"])
    assert summary["clientConfigurationLastPrepared"]
    assert not summary["privateHttpsLastConfigured"]
    record["activation"]["phase"] = "starting"
    selected["journal"].write_text(json.dumps(record))
    summary = install_status.status(journal=selected["journal"])
    assert summary["nextRequiredStage"] == "runtime_activation"
    assert not summary["runtimeLastActive"]
    with pytest.raises(ServiceError, match=r"^install_agent_requires_active_runtime$"):
        install_agent.setup(**arguments)


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
        ServiceError, match=r"settings_locally_changed|integration_locally_changed"
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


@pytest.mark.parametrize("existing", ["agent_token", "settings", "retry_root"])
def test_first_handoff_conflict_names_the_path_and_preserves_unowned_content(
    tmp_path, monkeypatch, capsys, existing
):
    arguments, selected, _identity, _note = connection_fixture(
        tmp_path, monkeypatch, private_https=False
    )
    path = arguments[existing]
    sentinel = "synthetic-private-content-never-print"
    if existing == "retry_root":
        path.mkdir(mode=0o700)
        retained = path / "pending.json"
    else:
        retained = path
    retained.write_text(sentinel)
    retained.chmod(0o600)
    before = selected["journal"].read_bytes()
    argv = [
        f"--{key.replace('_', '-')}={value}"
        for key, value in arguments.items()
        if not isinstance(value, bool)
    ]
    argv += ["--confirm-grant", "--acknowledge-ai-egress"]
    assert install_agent.main(argv) == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["code"] == "install_agent_unowned_handoff_or_grant"
    assert result["conflictingPath"] == str(path)
    assert "absent token/settings/retry" in result["recovery"]
    assert "owner review" in result["recovery"]
    assert sentinel not in output
    assert retained.read_text() == sentinel
    assert selected["journal"].read_bytes() == before
    runtime, admitted = install_agent.owner(json.loads(before))
    assert install_agent.actors(runtime, admitted) == []
    assert not arguments["skill_directory"].exists()


def test_status_names_activation_after_owner_setup(tmp_path, monkeypatch):
    _activation, _engine, selected, _identity, _note = activation_fixture(
        tmp_path, monkeypatch, guided_owner=True
    )
    summary = install_status.status(journal=selected["journal"])
    assert summary["localStages"][2]["state"] == "incomplete"
    assert summary["nextRequiredStage"] == "runtime_activation"
    assert summary["nextRequiredCommand"] == "health_buddy.install.activation"


def test_status_names_owner_setup_as_next_stage_for_prepared_install(
    tmp_path, monkeypatch, capsys
):
    selected = preparation_inputs(tmp_path, monkeypatch)
    install_prepare.prepare(**selected)
    journal_before = selected["journal"].read_bytes()
    assert install_status.main(["--journal", str(selected["journal"])]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == "install_agent_requires_retained_owner"
    assert output["incomplete"] is True
    assert output["nextRequiredStage"] == "owner_setup"
    assert output["nextRequiredCommand"] == "health_buddy.install.owner"
    assert "guided-native-owner-setup" in output["recovery"]
    assert selected["journal"].read_bytes() == journal_before


@pytest.mark.parametrize(
    ("change", "client", "expected"),
    [
        ("client_config", "claude", "claude_project_config_required"),
        ("skill_directory", "codex", "invalid_codex_skill_directory"),
    ],
)
def test_invalid_client_target_refuses_before_grant_or_journal_changes(
    tmp_path, monkeypatch, capsys, change, client, expected
):
    arguments, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    runtime, admitted = install_agent.owner(
        json.loads(selected["journal"].read_bytes())
    )
    assert install_agent.actors(runtime, admitted) == []
    journal_before = selected["journal"].read_bytes()
    config_before = arguments["client_config"].read_bytes()
    invalid = dict(arguments, client=client)
    if change == "skill_directory":
        invalid["skill_directory"] = arguments["skill_directory"].parent / "wrong-name"
    argv = [
        "--journal",
        str(invalid["journal"]),
        "--policy",
        str(invalid["policy"]),
        "--agent-token",
        str(invalid["agent_token"]),
        "--settings",
        str(invalid["settings"]),
        "--retry-root",
        str(invalid["retry_root"]),
        "--client",
        invalid["client"],
        "--client-config",
        str(invalid["client_config"]),
        "--skill-directory",
        str(invalid["skill_directory"]),
        "--python",
        str(invalid["python"]),
        "--confirm-grant",
        "--acknowledge-ai-egress",
    ]
    assert install_agent.main(argv) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["code"] == expected
    assert output["recovery"]
    assert selected["journal"].read_bytes() == journal_before
    assert not invalid["agent_token"].exists() and not invalid["settings"].exists()
    assert not invalid["retry_root"].exists()
    assert arguments["client_config"].read_bytes() == config_before
    runtime, admitted = install_agent.owner(json.loads(journal_before))
    assert install_agent.actors(runtime, admitted) == []
    corrected = (
        dict(
            invalid,
            client_config=(
                arguments["client_config"].parent / "claude-launcher" / ".mcp.json"
            ),
        )
        if client == "claude"
        else arguments
    )
    if client == "claude":
        corrected["client_config"].parent.mkdir(mode=0o700)
    result = install_agent.setup(**corrected)
    assert result["agentGrantRetained"] and result["clientConfigurationPrepared"]
    if client == "claude":
        assert arguments["client_config"].read_bytes() == config_before
    else:
        assert arguments["client_config"].read_bytes().startswith(config_before)
    runtime, admitted = install_agent.owner(
        json.loads(selected["journal"].read_bytes())
    )
    assert len(install_agent.actors(runtime, admitted)) == 1


def test_agent_resume_reports_only_differing_binding_field_names(
    tmp_path, monkeypatch, capsys
):
    arguments, _selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    install_agent.setup(**arguments)
    alternate = dict(
        arguments,
        client_config=arguments["client_config"].parent / "other.toml",
    )
    argv = [
        "--journal",
        str(alternate["journal"]),
        "--policy",
        str(alternate["policy"]),
        "--agent-token",
        str(alternate["agent_token"]),
        "--settings",
        str(alternate["settings"]),
        "--retry-root",
        str(alternate["retry_root"]),
        "--client",
        alternate["client"],
        "--client-config",
        str(alternate["client_config"]),
        "--skill-directory",
        str(alternate["skill_directory"]),
        "--python",
        str(alternate["python"]),
        "--confirm-grant",
        "--acknowledge-ai-egress",
    ]
    assert install_agent.main(argv) == 2
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["code"] == "install_agent_resume_requires_original_binding"
    assert data["differingFields"] == ["config"]
    assert "removal/re-arm" in data["recovery"]
    assert str(alternate["client_config"]) not in output
    assert alternate["agent_token"].read_bytes().strip() not in output.encode()
    retained = json.loads(alternate["journal"].read_bytes())
    retained["agentSetup"]["binding"]["unboundField"] = None
    alternate["journal"].write_text(json.dumps(retained))
    argv[argv.index(str(alternate["client_config"]))] = str(arguments["client_config"])
    assert install_agent.main(argv) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["code"] == "install_agent_resume_requires_original_binding"
    assert data["differingFields"] == ["unboundField"]


def test_status_owner_config_change_keeps_named_refusal_and_guidance(
    tmp_path, monkeypatch, capsys
):
    _arguments, selected, _identity, _note = connection_fixture(tmp_path, monkeypatch)
    journal_before = selected["journal"].read_bytes()
    config = selected["workspace"] / "config.json"
    values = json.loads(config.read_bytes())
    values["timezone"] = "America/Chicago"
    config.write_text(json.dumps(values))
    changed = config.read_bytes()
    assert install_status.main(["--journal", str(selected["journal"])]) == 2
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["code"] == "install_agent_owner_config_changed"
    recovery = data["recovery"].lower()
    assert "inspect" in recovery
    assert "do not rewrite ingress, delete the journal, or rebind" in recovery
    assert config.read_bytes() == changed
    assert selected["journal"].read_bytes() == journal_before
