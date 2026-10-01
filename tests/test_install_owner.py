"""Guided owner authority setup; real auth/readiness and synthetic host only."""

import json
import os

import pytest

from health_buddy.install import activation as install_activation
from health_buddy.install import owner as install_owner
from health_buddy.core.domain import identity_value
from health_buddy.core.security_api import BearerProof
from health_buddy.security.runtime import open_runtime, read_credential
from health_buddy.core.service_api import ServiceError
from tests.test_install_activation import fixture, simulate_nonroot_owner
from tests.test_install_prepare import command_line, inputs


def prepared_owner(tmp_path, monkeypatch):
    selected = inputs(tmp_path, monkeypatch)
    from health_buddy.install.prepare import prepare

    prepare(**selected)
    root = selected["workspace"]
    config = root / "config.json"
    values = json.loads(config.read_bytes())
    values["timezone"] = "America/Chicago"
    values["identity"]["displayName"] = "Synthetic owner choice"
    config.write_text(json.dumps(values))
    note = root / "personal/OWNER.md"
    note.write_text("synthetic personal work")
    simulate_nonroot_owner(monkeypatch)
    arguments = dict(
        journal=selected["journal"],
        owner_token=root / "secrets/synthetic-owner-token",
        origin="https://synthetic.example.test",
        owner_subject="synthetic-owner",
        confirm_owner_setup=True,
    )
    return selected, arguments, values, note


def owner_command(arguments):
    selection = {k: v for k, v in arguments.items() if k != "confirm_owner_setup"}
    return [*command_line(selection), "--confirm-owner-setup"]


def test_default_prepare_guided_owner_activation_and_repeat(tmp_path, monkeypatch):
    activation, state, selected, identity, note = fixture(
        tmp_path, monkeypatch, guided_owner=True
    )
    root = selected["workspace"]
    token = root / "secrets/synthetic-owner-token"
    credential = token.read_bytes()
    runtime = open_runtime(root)
    caller = runtime.security.authenticate(BearerProof(read_credential(token)))
    authority = caller.client
    arguments = dict(
        journal=selected["journal"],
        owner_token=token,
        origin="https://synthetic.example.test",
        owner_subject="synthetic-owner",
        confirm_owner_setup=True,
    )
    config = (root / "config.json").read_bytes()
    assert install_owner.setup(**arguments)["ownerSetupReady"]
    assert install_activation.activate(**activation)["runtimeActivated"]
    assert install_owner.setup(**arguments)["ownerSetupReady"]
    assert install_activation.activate(**activation)["runtimeActivated"]
    fresh = open_runtime(root)
    assert (
        fresh.security.authenticate(BearerProof(read_credential(token))).client
        == authority
    )
    assert identity_value(fresh.operations.journal.verify().identity) == identity
    assert fresh.operations.journal.verify().revision == 0
    assert (
        token.read_bytes() == credential
        and (root / "config.json").read_bytes() == config
    )
    assert note.read_text() == "synthetic personal work retained"
    assert sum("up" in item for item in state["calls"]) == 1
    assert credential.strip().decode() not in selected["journal"].read_text()


@pytest.mark.parametrize("lost", ["configuration", "authority"])
def test_lost_owner_setup_ack_observes_owned_config_and_authenticates_retained_token(
    tmp_path, monkeypatch, lost
):
    selected, arguments, original, note = prepared_owner(tmp_path, monkeypatch)
    runtime = open_runtime(selected["workspace"])
    identity = runtime.operations.journal.verify().identity
    if lost == "configuration":
        real = install_owner.atomic_bytes

        def interrupted(path, payload):
            real(path, payload)
            if path.name == "config.json":
                raise OSError("synthetic lost config acknowledgement")

        monkeypatch.setattr(install_owner, "atomic_bytes", interrupted)
    else:
        real = install_owner.setup_security

        def interrupted(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError("synthetic lost security acknowledgement")

        monkeypatch.setattr(install_owner, "setup_security", interrupted)
    with pytest.raises(ServiceError, match="install_owner_" + lost + "_interrupted"):
        install_owner.setup(**arguments)
    progress = json.loads(selected["journal"].read_bytes())["ownerSetup"]
    assert progress["phase"] == (
        "configuring" if lost == "configuration" else "authority_preparing"
    )
    if lost == "configuration":
        monkeypatch.setattr(install_owner, "atomic_bytes", real)
    else:
        before = arguments["owner_token"].read_bytes()

        def no_reinitialize(*_args, **_kwargs):
            pytest.fail("retained authority must authenticate without reinitialization")

        monkeypatch.setattr(install_owner, "setup_security", no_reinitialize)
    assert install_owner.setup(**arguments)["ownerSetupReady"]
    if lost == "authority":
        assert arguments["owner_token"].read_bytes() == before
    current = json.loads((selected["workspace"] / "config.json").read_bytes())
    assert {key: value for key, value in current.items() if key != "security"} == {
        key: value for key, value in original.items() if key != "security"
    }
    fresh = open_runtime(selected["workspace"])
    authenticated = fresh.security.authenticate(
        BearerProof(read_credential(arguments["owner_token"]))
    )
    assert authenticated.client.identity == identity
    assert fresh.operations.journal.verify().revision == 0
    assert note.read_text() == "synthetic personal work"


def test_pending_foreign_config_edit_and_changed_selection_refuse_without_overwrite(
    tmp_path, monkeypatch
):
    selected, arguments, _original, _note = prepared_owner(tmp_path, monkeypatch)
    real = install_owner.atomic_bytes

    def interrupted(path, payload):
        real(path, payload)
        if path.name == "config.json":
            raise OSError("synthetic lost config acknowledgement")

    monkeypatch.setattr(install_owner, "atomic_bytes", interrupted)
    with pytest.raises(ServiceError):
        install_owner.setup(**arguments)
    monkeypatch.setattr(install_owner, "atomic_bytes", real)
    retained = selected["journal"].read_bytes()
    with pytest.raises(ServiceError, match="original_binding"):
        install_owner.setup(**{**arguments, "owner_subject": "another-owner"})
    config = selected["workspace"] / "config.json"
    config.write_text(config.read_text() + " ")
    edited = config.read_bytes()
    with pytest.raises(ServiceError, match="config_locally_changed"):
        install_owner.setup(**arguments)
    assert (
        config.read_bytes() == edited and selected["journal"].read_bytes() == retained
    )
    assert not arguments["owner_token"].exists()


def test_partial_authority_retains_empty_output_requires_explicit_recovery(
    tmp_path, monkeypatch, capsys
):
    selected, arguments, _original, _note = prepared_owner(tmp_path, monkeypatch)
    real = install_owner.setup_security

    def interrupted(*args, **kwargs):
        def fault(point):
            if point == "security_binding_written":
                raise OSError("synthetic partial authority")

        real(*args, **kwargs, fault=fault)

    monkeypatch.setattr(install_owner, "setup_security", interrupted)
    with pytest.raises(ServiceError, match="authority_interrupted"):
        install_owner.setup(**arguments)
    assert arguments["owner_token"].read_bytes() == b""
    binding = selected["workspace"] / "operations/security-binding.json"
    retained = binding.read_bytes()
    monkeypatch.setattr(install_owner, "setup_security", real)
    assert install_owner.main(owner_command(arguments)) == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["code"] == "install_owner_partial_requires_explicit_recovery"
    assert "revokes all credentials" in refusal["recovery"]
    assert (
        binding.read_bytes() == retained
        and arguments["owner_token"].read_bytes() == b""
    )
    assert str(arguments["owner_token"]) not in json.dumps(refusal)


def test_explicit_consent_validated_origin_and_unowned_output_refuse(
    tmp_path, monkeypatch
):
    selected, arguments, _original, _note = prepared_owner(tmp_path, monkeypatch)
    before = selected["journal"].read_bytes()
    for changes, code in (
        ({"confirm_owner_setup": False}, "explicit_consent"),
        ({"origin": "http://synthetic.example.test"}, "https_and_exact_subject"),
        ({"owner_subject": " owner "}, "https_and_exact_subject"),
    ):
        with pytest.raises(ServiceError, match=code):
            install_owner.setup(**{**arguments, **changes})
    arguments["owner_token"].write_text("synthetic foreign file retained")
    with pytest.raises(ServiceError, match="foreign_or_partial_authority"):
        install_owner.setup(**arguments)
    assert selected["journal"].read_bytes() == before
    assert arguments["owner_token"].read_text() == "synthetic foreign file retained"


def test_prepare_and_owner_setup_under_umask_002_leave_nothing_group_writable(
    tmp_path, monkeypatch, capsys
):
    # Ubuntu's default umask for ordinary users. prepared_owner prepares through
    # the library, outside any entry point's umask, as other library callers do.
    previous = os.umask(0o002)
    try:
        selected, arguments, _original, note = prepared_owner(tmp_path, monkeypatch)
        assert install_owner.main(owner_command(arguments)) == 0
    finally:
        os.umask(previous)
    assert json.loads(capsys.readouterr().out)["ownerSetupReady"]
    root = selected["workspace"]
    store = root / "stores/manual.git"
    assert not [path for path in store.rglob("*") if path.lstat().st_mode & 0o077]
    writable = {path for path in root.rglob("*") if path.lstat().st_mode & 0o022}
    # Only the owner's own note keeps the umask it was written with.
    assert writable <= {note}


def test_unready_workspace_names_readiness_and_a_plain_retry_completes(
    tmp_path, monkeypatch, capsys
):
    selected, arguments, _original, _note = prepared_owner(tmp_path, monkeypatch)
    # As Git left it on the host under umask 002.
    reference = selected["workspace"] / "stores/manual.git/refs/heads/main"
    reference.chmod(0o664)
    assert install_owner.main(owner_command(arguments)) == 2
    refusal = json.loads(capsys.readouterr().out)
    assert refusal == {
        "schemaVersion": 1,
        "code": "install_owner_workspace_not_ready",
        "ownerSetupReady": False,
        "recovery": install_owner.NOT_READY_RECOVERY,
    }
    assert "readiness check" in refusal["recovery"]
    credential = arguments["owner_token"].read_bytes()
    reference.chmod(0o644)  # The host's repair: chmod go-w.
    assert install_owner.main(owner_command(arguments)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ownerSetupReady"] and "recovery" not in result
    assert arguments["owner_token"].read_bytes() == credential
