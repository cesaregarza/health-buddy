"""Native maintenance and explicit health authority remain distinct CLI paths."""

import json

from health_buddy.cli import main
from health_buddy.extension_registry import Registry
from health_buddy.workspace import initialize
from tests.extension_fixtures import private_file
from tests.security_fixtures import secured


def test_native_install_review_and_inspection_need_no_health_credential(tmp_path, capsys):
    root = tmp_path / "owner"
    config = initialize(root)
    prefix = ["--workspace", str(root), "extension"]
    assert main([*prefix, "install", "--example", "local.weekly-mass"]) == 0
    installed = json.loads(capsys.readouterr().out)
    assert installed == {"installed": "local.weekly-mass", "enabled": False}
    assert main([
        *prefix, "enable", "--id", "local.weekly-mass", "--source-id", "manual"
    ]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["state"] == "ready" and selected["kind"] == "metric-view"
    assert main(["--workspace", str(root), "workspace", "describe", "--json"]) == 0
    description = json.loads(capsys.readouterr().out)
    assert description["personalInventory"]["complete"] is True
    assert main([*prefix, "preview", "--id", "local.weekly-mass"]) == 2
    assert "explicit_credential_file_required (HTTP 401)" in capsys.readouterr().err
    assert Registry(config).inspect()[0].enabled is True


def test_health_credential_does_not_authorize_cli_code_activation(tmp_path, capsys):
    runtime, _owner, token = secured(tmp_path / "owner")
    root = runtime.operations.config.root
    credential = root / "secrets/test-cli-owner"
    private_file(credential, token.encode() + b"\n")
    assert main([
        "--workspace", str(root), "--credential-file", str(credential),
        "extension", "install", "--example", "local.weekly-mass"
    ]) == 2
    output = capsys.readouterr()
    assert "native_maintenance_does_not_use_health_credential (HTTP 422)" in output.err
    assert output.out == "" and token not in output.err
    assert Registry(runtime.operations.config).inspect() == ()
