"""One-time origin transition: real authority/data/files, synthetic daemon only."""

import json

import pytest

from health_buddy import cli
from health_buddy.core.security_api import PairingReservation, SecurityRequest
from health_buddy.core.service_api import ServiceError
from health_buddy.install import agent, https, rebind, rebind_state, status
from tests.test_install_agent import connection_fixture
from tests.test_install_https import serve_fixture
from tests.test_install_owner import prepared_owner


def arguments(selected):
    return dict(
        journal=selected["journal"],
        owner_token=selected["workspace"] / "secrets/synthetic-owner-token",
        origin="https://synthetic.example.test",
        owner_subject="user@github",
        confirm_rebind=True,
        confirm_local_daemon=True,
        confirm_quiesced=True,
    )


def command(values):
    argv = []
    for key, value in values.items():
        argv.append("--" + key.replace("_", "-"))
        if value is not True:
            argv.append(str(value))
    return argv


@pytest.mark.parametrize("agent_first", [False, True])
def test_rebind_preserves_authenticated_records_grants_and_client_config(
    tmp_path, monkeypatch, capsys, agent_first
):
    client, selected, _identity, note = connection_fixture(
        tmp_path, monkeypatch, private_https=False, local_only=True
    )
    values = arguments(selected)
    retained_token = values["owner_token"].read_bytes()
    if agent_first:
        agent.setup(**client)
    retained_client = client["client_config"].read_bytes()
    native = [
        "--workspace",
        str(selected["workspace"]),
        "--credential-file",
        str(values["owner_token"]),
    ]
    assert (
        cli.main(
            [
                *native,
                "log",
                "measurement",
                "--weight-lb",
                "150",
                "--measured-at-local",
                "2030-01-01T12:00:00Z",
                "--timezone",
                "UTC",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert rebind.main(command(values)) == 0
    assert json.loads(capsys.readouterr().out)["originRebound"]
    assert client["client_config"].read_bytes() == retained_client
    assert values["owner_token"].read_bytes() == retained_token
    assert (
        cli.main(
            [
                *native,
                "records",
                "--source-ids",
                "manual",
                "--kinds",
                "body-mass",
                "--from",
                "2030-01-01T12:00:00Z",
                "--to",
                "2030-01-01T12:00:00Z",
            ]
        )
        == 0
    )
    records = json.loads(capsys.readouterr().out)["records"]
    assert len(records) == 1 and records[0]["value"] == 150
    agent.setup(**client)
    summary = status.status(journal=selected["journal"])
    assert summary["ownerAuthenticated"] and summary["agentGrantRetained"]
    assert summary["runtimeLastActive"] and summary["clientConfigurationLastPrepared"]
    assert json.loads(client["settings"].read_bytes())["origin"] == values["origin"]
    journal = selected["journal"].read_bytes()
    assert retained_token.strip() not in journal
    assert rebind.rebind(**values)["originRebound"]
    assert selected["journal"].read_bytes() == journal
    assert note.read_text() == "synthetic personal work retained"


def test_https_dry_run_admits_the_rebound_origin(tmp_path, monkeypatch):
    route, _state, selected, _identity, _note, engine = serve_fixture(
        tmp_path, monkeypatch, local_only=True
    )
    assert rebind.rebind(**arguments(selected))["originRebound"]
    assert https.route(**dict(route, action="dry-run"))["eligible"]
    mutations = [
        call[10:]
        for call in engine["calls"]
        if call[3] == "compose" and call[10] in ("up", "stop")
    ]
    assert mutations == [
        ["up", "--detach", "--wait", "--no-deps", "api"],
        ["stop", "--timeout", "30", "api"],
        ["up", "--detach", "--wait", "--no-deps", "api"],
    ]


@pytest.mark.parametrize("blocked", ["subject", "token", "pairing", "settings"])
def test_rebind_refusals_name_the_field_and_preserve_files(
    tmp_path, monkeypatch, capsys, blocked
):
    client, selected, _identity, _note = connection_fixture(
        tmp_path, monkeypatch, private_https=False, local_only=True
    )
    values = arguments(selected)
    field = {
        "subject": "ownerSubject",
        "token": "ownerToken",
        "pairing": "activeDevicePairings",
        "settings": "agentSettings",
    }[blocked]
    if blocked == "subject":
        values["owner_subject"] = "not-an-exact-login-subject"
    elif blocked == "token":
        token = tmp_path / "unknown-owner-token"
        token.write_text("A" * 43 + "\n")
        token.chmod(0o600)
        values["owner_token"] = token
    elif blocked == "pairing":
        runtime, admitted = agent.owner(json.loads(selected["journal"].read_bytes()))
        runtime.security.execute(
            admitted.principal,
            SecurityRequest(
                "pairing.create",
                payload=PairingReservation("Synthetic phone"),
                identity=admitted.client.identity,
            ),
        )
    else:
        agent.setup(**client)
        client["settings"].write_text('{"syntheticOwnerEdit": true}\n')
    retained = {
        path: path.read_bytes()
        for path in (
            selected["journal"],
            selected["workspace"] / "config.json",
            values["owner_token"],
        )
    }
    assert rebind.main(command(values)) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["details"]["blockingField"] == field
    assert "A" * 43 not in json.dumps(output)
    assert all(path.read_bytes() == content for path, content in retained.items())


def test_rebind_requires_completed_owner_setup(tmp_path, monkeypatch):
    selected, _owner, _config, _note = prepared_owner(tmp_path, monkeypatch)
    with pytest.raises(
        ServiceError, match="install_rebind_requires_completed_owner_setup"
    ):
        rebind.rebind(**arguments(selected))
    assert "originRebind" not in json.loads(selected["journal"].read_bytes())


@pytest.mark.parametrize("interrupted", ["config", "settings", "journal"])
def test_partial_rebind_resumes_exact_selection_without_replacing_authority(
    tmp_path, monkeypatch, interrupted
):
    client, selected, _identity, _note = connection_fixture(
        tmp_path, monkeypatch, private_https=False, local_only=True
    )
    agent.setup(**client)
    values = arguments(selected)
    secrets = {
        path: path.read_bytes()
        for path in (values["owner_token"], client["agent_token"])
    }
    target = {
        "config": selected["workspace"] / "config.json",
        "settings": client["settings"],
        "journal": selected["journal"],
    }[interrupted]
    write = rebind_state.atomic_bytes
    lost = False

    def publish_then_interrupt(path, payload):
        nonlocal lost
        write(path, payload)
        if path == target and not lost:
            lost = True
            raise OSError("synthetic interrupted acknowledgement")

    monkeypatch.setattr(rebind_state, "atomic_bytes", publish_then_interrupt)
    with pytest.raises(ServiceError, match=r"^install_rebind_interrupted$"):
        rebind.rebind(**values)
    with pytest.raises(ServiceError, match="resume_requires_original_binding"):
        rebind.rebind(**dict(values, owner_subject="other@github"))
    assert rebind.rebind(**values)["originRebound"]
    assert all(path.read_bytes() == content for path, content in secrets.items())
    assert status.status(journal=selected["journal"])["agentGrantRetained"]
