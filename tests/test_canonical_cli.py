"""Real local CLI adapters use the same canonical receipts and retry state."""

import io
import json
from datetime import UTC, datetime

import pytest

from health_buddy.client.app import App
from health_buddy.cli import main
from health_buddy.core.service_api import ServiceError


def arguments(root, *command):
    return ["--workspace", str(root), "--development", *command]


def test_cli_explicit_authority_and_same_action_replay(tmp_path, capsys):
    root = tmp_path / "owner"
    assert main(["--workspace", str(root), "init"]) == 0
    assert main(["--workspace", str(root), "status"]) == 2
    assert "explicit_credential_file_required (HTTP 401)" in capsys.readouterr().err
    command = (
        "log",
        "measurement",
        "--measured-at-local",
        datetime.now(UTC).isoformat(),
        "--weight-lb",
        "150",
    )
    assert main(arguments(root, *command)) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(arguments(root, *command)) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert first["meta"]["dataRevision"] == 1
    assert App.development(root).snapshot()["weight"][0]["lb"] == 150
    assert main(arguments(root, "pending", "show")) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "state": "complete",
        "cursor": 1,
        "operation": "logs.write",
        "lastError": None,
    }


def test_cli_conflict_resolution_and_safe_diagnostics(tmp_path, capsys):
    root = tmp_path / "owner"
    command = (
        "log",
        "measurement",
        "--measured-at-local",
        datetime.now(UTC).isoformat(),
        "--weight-lb",
    )
    assert main(arguments(root, *command, "150")) == 0
    capsys.readouterr()
    assert main(arguments(root, *command, "151")) == 2
    assert "151" not in capsys.readouterr().err
    assert main(arguments(root, "pending", "show")) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["state"] == "pending" and "payload" not in summary
    assert main(arguments(root, "pending", "discard")) == 2
    capsys.readouterr()
    assert (
        main(arguments(root, "pending", "discard", "--acknowledge-possible-save")) == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "discarded"
    assert App.development(root).snapshot()["weight"][0]["lb"] == 150


def test_circumference_preview_has_no_write_or_retry_cursor(tmp_path):
    app = App.development(tmp_path / "owner")
    before = app.snapshot()["meta"]["dataRevision"]
    result = app.log_record(
        "circumference",
        [
            "--measured-at-local",
            "2020-01-01T08:00:00",
            "--site",
            "waist",
            "--reading",
            "32",
            "--reading",
            "32.2",
            "--measurement-site",
            "synthetic landmark",
        ],
    )
    assert result["preview"] is True
    assert app.snapshot()["meta"]["dataRevision"] == before
    assert app.workflow.inspect() == {"state": "empty", "cursor": 0}


def test_default_app_cannot_read_or_write(tmp_path):
    app = App(tmp_path / "owner")
    for action in (
        app.snapshot,
        app.html,
        app.context,
        lambda: app.log_record("measurement", []),
    ):
        with pytest.raises(ServiceError):
            action()


def test_cli_duplicate_json_fields_rejected_before_write(tmp_path, capsys, monkeypatch):
    root = tmp_path / "owner"
    monkeypatch.setattr(
        "sys.stdin", io.StringIO('{"schema_version":1,"schema_version":1}')
    )
    assert main(arguments(root, "log", "workout")) == 2
    assert "invalid_request" in capsys.readouterr().err
    app = App.development(root)
    assert app.snapshot()["meta"]["dataRevision"] == 0
    assert app.workflow.inspect()["state"] == "empty"
