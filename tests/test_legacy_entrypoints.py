"""Compatibility scripts require canonical workspace admission, never loose output."""

import json
from unittest.mock import patch

import pytest

from health_buddy.app import App
from health_buddy.service_api import Request
from scripts import (
    log_blood_pressure,
    log_circumference,
    log_intake,
    log_measurement,
    log_workout,
)

LOGGERS = [
    log_measurement,
    log_intake,
    log_blood_pressure,
    log_circumference,
    log_workout,
]


@pytest.mark.parametrize("module", LOGGERS)
def test_legacy_main_refuses_without_workspace(module, tmp_path):
    path = tmp_path / "never-created.csv"
    with patch(
        "health_buddy.cli.main", side_effect=AssertionError("No implicit workspace")
    ):
        assert module.main(["--data-file", str(path)]) == 2
    assert not path.exists()


@pytest.mark.parametrize(
    "module,operation,args",
    [
        (log_measurement, "measurement", ["--weight-lb", "150"]),
        (log_intake, "intake", ["--item-name", "synthetic"]),
        (log_blood_pressure, "blood-pressure", ["--systolic", "118"]),
        (log_circumference, "circumference", ["--site", "waist"]),
        (log_workout, "workout-start", ["start", "--session-id", "synthetic"]),
    ],
)
def test_compatibility_main_routes_explicit_workspace(
    module, operation, args, tmp_path
):
    workspace = str(tmp_path / "owner")
    with patch("health_buddy.cli.main", return_value=0) as canonical:
        assert (
            module.main(
                ["--workspace", workspace, "--development", "--new-write", *args]
            )
            == 0
        )
    fields = args[1:] if module is log_workout else args
    canonical.assert_called_once_with(
        [
            "--workspace",
            workspace,
            "--development",
            "--new-write",
            "log",
            operation,
            *fields,
        ]
    )


def test_measurement_wrapper_is_canonical_and_rejects_storage_override(
    tmp_path, capsys
):
    workspace = tmp_path / "owner"
    args = [
        "--workspace",
        str(workspace),
        "--development",
        "--measured-at-local",
        "2030-01-01T08:00:00",
        "--weight-lb",
        "150",
    ]
    assert log_measurement.main(args) == 0
    first = capsys.readouterr().out
    assert log_measurement.main(args) == 0
    assert capsys.readouterr().out == first
    outside = tmp_path / "never-created.csv"
    assert log_measurement.main([*args, "--data-file", str(outside)]) == 2
    assert not outside.exists()
    app = App.development(workspace)
    response = app.operations.execute(app.principal, Request("capabilities"))
    assert json.loads(response.body)["meta"]["dataRevision"] == 1
