"""Real local CLI adapters use the same canonical receipts and retry state."""

import io
import json
import re
import shlex
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from health_buddy.cli import main
from health_buddy.client.app import App
from health_buddy.core.service_api import ServiceError
from tests.test_runtime_bundle_asset import run

GUIDE = Path(__file__).resolve().parents[1] / "docs/install-preflight.md"


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


@pytest.mark.parametrize(
    "argument,value,accepted",
    [
        ("--measured-at-local", "yesterday", "ISO-8601 local date-time"),
        ("--timezone", "Not/AZone", "IANA time-zone name"),
    ],
)
def test_bad_logger_time_refuses_before_intent_and_names_expected_form(
    tmp_path, capsys, argument, value, accepted
):
    root = tmp_path / "owner"
    assert main(["--workspace", str(root), "init"]) == 0
    capsys.readouterr()
    assert (
        main(
            arguments(
                root,
                "log",
                "measurement",
                "--measured-at-local",
                "2030-01-01T08:00:00",
                "--timezone",
                "UTC",
                "--weight-lb",
                "150",
                argument,
                value,
            )
        )
        == 2
    )
    refusal = capsys.readouterr().err
    assert refusal.count("\n") == 1
    assert argument in refusal and value in refusal and accepted in refusal
    assert not (root / "personal/state/native-client.json").exists()
    assert not (root / "personal/state/native-client.lock").exists()


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


def test_log_help_needs_no_credential_or_workspace(tmp_path, capsys):
    root = tmp_path / "owner"

    def shown(*command):
        with pytest.raises(SystemExit) as exited:
            main(["--workspace", str(root), "log", *command, "--help"])
        assert exited.value.code == 0
        return capsys.readouterr().out

    measurement = shown("measurement")
    assert "--measured-at-local" in measurement and "--weight-lb" in measurement
    # The usage line brackets only the optional flags.
    assert "[--weight-lb" not in measurement and "[--timezone" in measurement
    assert "JSON on standard input" in shown("workout")
    assert "log KIND --help" in shown()
    assert not root.exists()


def test_log_flag_errors_name_the_flag_before_any_credential(tmp_path, capsys):
    root = tmp_path / "owner"
    weight = ["measurement", "--weight-lb", "150"]
    for command, named in (
        (
            [*weight, "--measured-at-local", "2030-01-01T08:00:00", "--value-lb", "1"],
            "unrecognized arguments: --value-lb",
        ),
        (weight, "required: --measured-at-local"),
        (["circumference", "--site", "neck"], "invalid choice: 'neck'"),
    ):
        with pytest.raises(SystemExit) as exited:
            main(["--workspace", str(root), "log", *command])
        assert exited.value.code == 2
        error = capsys.readouterr().err
        assert error.startswith("usage: ") and named in error
        assert error.endswith("Health Buddy: invalid_logger_arguments (HTTP 422).\n")
    assert not root.exists()


def test_documented_owner_measurement_round_trip(tmp_path, capsys):
    (block,) = [
        block
        for block in re.findall(r"```sh\n(.*?)```", GUIDE.read_text(), re.DOTALL)
        if 'MEASURED_AT_UTC="$(date -u ' in block
    ]
    root = tmp_path / "owner"
    token = root / "secrets/native-owner-token"
    assert main(["--workspace", str(root), "init"]) == 0
    bootstrap = ["security", "bootstrap", "--owner-token-file", str(token)]
    assert main(["--workspace", str(root), *bootstrap]) == 0
    capsys.readouterr()
    home = tmp_path / "shell-owner"
    env = home / "health-buddy/env.sh"
    env.parent.mkdir(parents=True)
    env.write_text(
        "\n".join(
            f"export {name}={shlex.quote(str(value))}"
            for name, value in {
                "PYTHON": sys.executable,
                "OWNER_WORKSPACE": root,
                "PYTHONPATH": GUIDE.parents[1] / "src",
                "PYTHONDONTWRITEBYTECODE": "1",
            }.items()
        )
    )
    before = datetime.now(UTC).replace(microsecond=0)
    stamp_line, receipt, listed = run(block, home, tmp_path, {}).splitlines()
    stamp = stamp_line.removeprefix("Measurement UTC timestamp: ")
    assert before <= datetime.fromisoformat(stamp) <= datetime.now(UTC)
    write_data = json.loads(receipt)["data"]
    assert write_data["saved"] is True
    assert (write_data["sourceId"], write_data["kind"]) == ("manual", "body-mass")
    assert write_data["observedAt"] == stamp
    assert "warnings" not in write_data
    (record,) = json.loads(listed)["records"]
    assert (record["value"], record["unit"], record["observedAt"]) == (150, "lb", stamp)
    assert (record["sourceId"], record["kind"]) == ("manual", "body-mass")
    assert record["observedAt"] == write_data["observedAt"]
    assert record["attributes"]["source"]["value"] == "synthetic-test"


def test_credentialed_records_reads_future_measurement_and_retains_warning(
    tmp_path, capsys, monkeypatch
):
    root = tmp_path / "owner"
    token = root / "secrets/native-owner-token"
    assert main(["--workspace", str(root), "init"]) == 0
    assert (
        main(
            [
                "--workspace",
                str(root),
                "security",
                "bootstrap",
                "--owner-token-file",
                str(token),
            ]
        )
        == 0
    )
    capsys.readouterr()
    monkeypatch.setattr(
        "health_buddy.core.operations._now", lambda: "2026-10-02T02:44:00Z"
    )
    base = ["--workspace", str(root), "--credential-file", str(token)]
    stamp = "2026-10-02T12:00:00Z"
    assert (
        main(
            [
                *base,
                "log",
                "measurement",
                "--measured-at-local",
                stamp,
                "--timezone",
                "UTC",
                "--weight-lb",
                "150",
            ]
        )
        == 0
    )
    saved = json.loads(capsys.readouterr().out)
    assert saved["data"]["warnings"][0]["code"] == "future_measurement_timestamp"
    assert (saved["data"]["sourceId"], saved["data"]["kind"]) == (
        "manual",
        "body-mass",
    )
    assert saved["data"]["observedAt"] == stamp
    read = [
        "records",
        "--source-ids",
        "manual",
        "--kinds",
        "body-mass",
        "--from",
        stamp,
        "--to",
        stamp,
        "--limit",
        "10",
    ]
    assert main(["--workspace", str(root), *read]) == 2
    assert "explicit_credential_file_required" in capsys.readouterr().err
    assert main([*base, *read]) == 0
    (record,) = json.loads(capsys.readouterr().out)["records"]
    assert (record["value"], record["unit"], record["observedAt"]) == (150, "lb", stamp)
    assert record["observedAt"] == saved["data"]["observedAt"]
    # These limits are enforced by the existing canonical reader.
    assert main([*base, *read[:-1], "501"]) == 2
    capsys.readouterr()
    monkeypatch.setattr(
        "health_buddy.core.operations._now", lambda: "2026-10-03T02:44:00Z"
    )
    assert main([*base, "pending", "retry"]) == 0
    assert json.loads(capsys.readouterr().out) == saved
